"""Package-manager behavior without running any real installers."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.manifest = json.loads(
            (install.ROOT / "profiles/packages.json").read_text()
        )
        self.packages = self.manifest["common"] + self.manifest["home"]
        self.commands = {
            p["command"] for p in self.manifest["common"] if "command" in p
        }
        for package in self.manifest["common"]:
            self.commands.update(package.get("commands", []))
        self.commands.update({"brew", "dnf", "rpm", "sudo", "winget"})
        self.installed = set()
        self.repo_packages = set()
        self.fail_installs = set()
        self.module_present = True
        self.add_installed_commands = True
        self.bwrap_version = "bubblewrap 0.9.0"
        self.unshare_version = "unshare from util-linux 2.39.0"
        self.unshare_help = "--map-current-user --keep-caps"
        self.tun_present = True
        self.calls = []
        self.manager = "dnf"
        for target, replacement in [
            ("install.Path.home", lambda: self.home),
            ("install.Path.is_char_device", lambda path: self.tun_present),
            ("install.detect_manager", lambda: self.manager),
            (
                "install.shutil.which",
                lambda command: (
                    f"/fake/{command}" if command in self.commands else None
                ),
            ),
            ("install.os.geteuid", lambda: 1000),
            ("install.subprocess.run", self.run_command),
            ("install.refresh_windows_path", lambda: None),
        ]:
            patcher = patch(target, replacement, create=target == "install.os.geteuid")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"DOTFILES_ENV": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, command, **kwargs):
        self.calls.append(command)
        words = command[1:] if command[0] == "sudo" else command
        executable = Path(words[0]).name
        status, stdout, stderr = 0, "", ""
        if executable == "rpm":
            status = 0 if words[-1] in self.installed else 1
        elif executable == "dnf" and "--available" in words:
            status = 0 if words[-1] in self.repo_packages else 1
            stderr = "" if status == 0 else "No matching packages to list"
        elif executable == "brew" and "list" in words:
            status = 0 if words[-1] in self.installed else 1
        elif executable == "bwrap":
            stdout = self.bwrap_version
        elif executable == "unshare":
            stdout = self.unshare_version if "--version" in words else self.unshare_help
        elif executable == "pwsh" and "Get-Module" in words[-1]:
            status = 0 if self.module_present else 1
        elif executable == "pwsh" and "Install-Module" in words[-1]:
            self.module_present = True
        elif "install" in words:
            package_id = (
                words[words.index("--id") + 1] if "--id" in words else words[-1]
            )
            if package_id in self.fail_installs:
                status = 1
            else:
                self.installed.add(package_id)
                if self.add_installed_commands:
                    for package in self.packages:
                        if package.get(self.manager) == package_id:
                            self.commands.update(package.get("commands", []))
                            if package.get("command"):
                                self.commands.add(package["command"])
        else:
            raise AssertionError(f"Unexpected subprocess: {command}")
        if status and kwargs.get("check"):
            raise subprocess.CalledProcessError(status, command)
        return subprocess.CompletedProcess(command, status, stdout, stderr)

    def invoke(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = install.main(list(args))
        return status, output.getvalue() + errors.getvalue()

    def installs(self):
        return [c for c in self.calls if "install" in c or "Install-Module" in c[-1]]

    def test_windows_handoff_quotes_checkout_path_and_uses_powershell_wrapper(self):
        self.manager = "winget"
        checkout = self.home / "shared config"
        (checkout / "profiles").mkdir(parents=True)
        (checkout / "profiles/packages.json").write_text(json.dumps(self.manifest))
        with patch.object(install, "ROOT", checkout):
            status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 0)
        self.assertIn(f"& '{checkout / 'link.ps1'}' -Profile work", output)

    def test_profile_required_before_package_commands(self):
        status, output = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn("no default profile", output)
        self.assertEqual(self.calls, [])

    def test_saved_profile_and_explicit_override_do_not_write_marker(self):
        marker = self.home / ".dotfiles-env"
        marker.write_text("home\n")
        status, output = self.invoke("--dry-run")
        self.assertEqual(status, 0)
        self.assertIn("Profile: home", output)
        status, output = self.invoke("--profile", "work", "--dry-run")
        self.assertEqual(status, 0)
        self.assertIn("Profile: work", output)
        self.assertEqual(marker.read_text(), "home\n")
        self.assertEqual(self.installs(), [])

    def test_invalid_environment_does_not_fall_back_to_home_marker(self):
        (self.home / ".dotfiles-env").write_text("home\n")
        with patch.dict(os.environ, {"DOTFILES_ENV": "wrong"}):
            status, _ = self.invoke("--dry-run")
        self.assertEqual(status, 1)
        self.assertEqual(self.calls, [])

    def test_missing_required_packages_block_all_installations(self):
        self.commands.difference_update({"git", "gh", "copilot"})
        self.repo_packages.add("git")
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertIn("GitHub CLI: RPM gh is unavailable", output)
        self.assertIn("Copilot CLI: no DNF package", output)
        self.assertIn("https://github.com/github/copilot-cli", output)
        self.assertEqual(self.installs(), [])
        self.assertIn(["dnf", "-q", "list", "--available", "git"], self.calls)

    def test_installed_upstream_tool_needs_no_dnf_package(self):
        status, _ = self.invoke("--profile", "work", "--dry-run")
        self.assertEqual(status, 0)
        self.assertFalse(any("copilot" in call for call in self.calls))

    def test_installed_rpm_with_missing_path_blocks_install(self):
        self.commands.remove("rg")
        self.installed.add("ripgrep")
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertIn("RPM ripgrep is installed but rg is not on PATH", output)
        self.assertEqual(self.installs(), [])

    def test_dry_run_only_queries_enabled_repositories(self):
        self.commands.remove("fd")
        self.repo_packages.add("fd-find")
        status, output = self.invoke("--profile", "work", "--dry-run")
        self.assertEqual(status, 0)
        self.assertIn("sudo dnf install -y fd-find", output)
        self.assertEqual(self.installs(), [])
        self.assertFalse((self.home / ".dotfiles-env").exists())

    def test_dnf_install_verifies_command(self):
        self.commands.remove("fd")
        self.repo_packages.add("fd-find")
        status, _ = self.invoke("--profile", "work")
        self.assertEqual(status, 0)
        self.assertEqual(self.installs(), [["sudo", "dnf", "install", "-y", "fd-find"]])

    def test_successful_package_manager_exit_is_not_enough(self):
        self.commands.remove("fd")
        self.repo_packages.add("fd-find")
        self.add_installed_commands = False
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertIn("fd is still unavailable", output)
        self.assertNotIn("Required tools are available", output)

    def test_optional_installation_failure_is_reported(self):
        self.repo_packages.add("atuin")
        self.fail_installs.add("atuin")
        status, output = self.invoke("--profile", "home")
        self.assertEqual(status, 0)
        self.assertIn("Optional installation failed", output)
        self.assertIn("optional items were skipped or failed", output)

    def test_old_bubblewrap_blocks_installs(self):
        self.bwrap_version = "bubblewrap 0.4.0"
        self.commands.remove("fd")
        self.repo_packages.add("fd-find")
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertIn("bubblewrap >= 0.5.0", output)
        self.assertEqual(self.installs(), [])

    def test_tun_device_is_required_before_install(self):
        self.tun_present = False
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertIn("requires /dev/net/tun", output)
        self.assertEqual(self.installs(), [])

    def test_old_util_linux_or_missing_options_block_install(self):
        for version, help_text in [
            ("util-linux 2.32.1", self.unshare_help),
            (self.unshare_version, "--keep-caps"),
        ]:
            with self.subTest(version=version, help=help_text):
                self.unshare_version, self.unshare_help = version, help_text
                status, output = self.invoke("--profile", "work")
                self.assertEqual(status, 1)
                self.assertIn("util-linux >= 2.35", output)
                self.assertEqual(self.installs(), [])

    def test_missing_iptables_restore_is_not_hidden_by_iptables(self):
        self.commands.remove("ip6tables-restore")
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertIn("iptables sandbox tools", output)
        self.assertEqual(self.installs(), [])

    def test_windows_installs_powershell_before_copilot_and_psfzf(self):
        self.manager = "winget"
        self.commands.difference_update({"pwsh", "copilot"})
        self.module_present = False
        status, output = self.invoke("--profile", "work")
        self.assertEqual(status, 0)
        calls = self.installs()
        self.assertEqual(
            calls[0],
            [
                "winget",
                "install",
                "--exact",
                "--id",
                "Microsoft.PowerShell",
                "--source",
                "winget",
            ],
        )
        self.assertEqual(
            calls[1],
            [
                "winget",
                "install",
                "--exact",
                "--id",
                "GitHub.Copilot",
                "--source",
                "winget",
            ],
        )
        self.assertIn(
            "Install-Module -Name PSFzf -Scope CurrentUser -Repository PSGallery",
            calls[2][-1],
        )
        self.assertIn("-NoProfile", calls[2])
        self.assertIn("Native Windows is unsupported", output)

    def test_work_does_not_install_home_packages(self):
        self.manager = "brew"
        status, output = self.invoke("--profile", "work", "--dry-run")
        self.assertEqual(status, 0)
        self.assertNotIn("atuin", output.lower())
        self.assertNotIn("ruff", output.lower())

    def test_missing_brew_is_not_bootstrapped(self):
        self.manager = "brew"
        self.commands.remove("brew")
        status, output = self.invoke("--profile", "home")
        self.assertEqual(status, 1)
        self.assertIn("brew is required", output)
        self.assertEqual(self.calls, [])

    def test_brew_installs_terminal_and_copilot_as_casks(self):
        self.manager = "brew"
        self.commands.remove("copilot")
        status, _ = self.invoke("--profile", "work")
        self.assertEqual(status, 0)
        self.assertEqual(
            self.installs(),
            [
                ["brew", "install", "--cask", "ghostty"],
                ["brew", "install", "--cask", "copilot-cli"],
            ],
        )

    def test_package_failure_stops_required_installations(self):
        self.manager = "winget"
        self.commands.difference_update({"git", "gh"})
        self.fail_installs.add("Git.Git")
        status, _ = self.invoke("--profile", "work")
        self.assertEqual(status, 1)
        self.assertEqual(len(self.installs()), 1)


class PlatformTests(unittest.TestCase):
    def test_supported_hosts_and_unsupported_linux(self):
        cases = [
            ("Darwin", {}, "brew"),
            ("Windows", {}, "winget"),
            ("Linux", {"ID": "rhel"}, "dnf"),
            ("Linux", {"ID": "rocky", "ID_LIKE": "rhel centos fedora"}, "dnf"),
        ]
        for system, release, expected in cases:
            with (
                self.subTest(system=system, release=release),
                patch("platform.system", return_value=system),
                patch("platform.freedesktop_os_release", return_value=release),
            ):
                self.assertEqual(install.detect_manager(), expected)
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.freedesktop_os_release", return_value={"ID": "ubuntu"}),
            self.assertRaisesRegex(ValueError, "Supported hosts"),
        ):
            install.detect_manager()


if __name__ == "__main__":
    unittest.main()

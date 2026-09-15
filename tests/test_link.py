import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import link
from dotfiles_profile import resolve_profile


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo, self.home, self.overlay = [
            self.base / name for name in ("shared", "user", "private")
        ]
        for path in (self.repo, self.home, self.overlay):
            path.mkdir()
        for name, content in {
            "profiles/shared.json": json.dumps(
                {"files": {".config/shared.txt": "shared.txt"}}
            ),
            "shared.txt": "shared configuration\n",
            "gitconfig": "[user]\n\tuseConfigOnly = true\n",
            "agents/core.md": "Use focused changes.\n",
            "agents/work.md": "Follow employer rules.\n",
            "copilot/copilot-instructions.md": "Write clearly.\n",
            "zshrc": "# shared shell\n",
            "copilot/shell-defaults.sh": "# shared launcher\n",
            "powershell/Microsoft.PowerShell_profile.ps1": "# shared shell\n",
            "tmux.conf": "# shared tmux\n",
        }.items():
            self.write(self.repo / name, content)
        self.write(
            self.repo / "bin/apply-copilot-settings.py",
            (ROOT / "bin/apply-copilot-settings.py").read_text(),
        )
        for name, content in {
            "profiles/home.json": json.dumps(
                {"files": {".config/private.txt": "private.txt"}}
            ),
            "private.txt": "private overlay\n",
            "gitconfig.home": "[user]\n\tname = Personal\n",
            "agents/home.md": "Personal automation.\n",
            "tmux.home.conf": "# private tmux\n",
            "copilot/subagents.json": '{"research": {"model": "personal-model"}}',
        }.items():
            self.write(self.overlay / name, content)

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def plan(self, profile="work", overlay=None, copilot_home=None):
        platform = "windows" if os.name == "nt" else "linux"
        options = {"copilot_home": copilot_home} if copilot_home else {}
        return link.plan(
            self.repo,
            self.home,
            profile,
            platform,
            overlay,
            str(self.home / "Documents/PowerShell/profile.ps1"),
            **options,
        )

    def apply(self, profile="work", overlay=None, dry=False):
        with contextlib.redirect_stdout(io.StringIO()):
            self.plan(profile, overlay).apply(dry)

    def snapshot(self):
        return {
            p.relative_to(self.home).as_posix(): ("link", os.readlink(p))
            if p.is_symlink()
            else ("file", p.read_bytes())
            for p in self.home.rglob("*")
            if p.is_file() or p.is_symlink()
        }

    def test_profile_resolution_never_defaults_or_writes(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                resolve_profile(home=self.home)
            self.assertEqual(self.snapshot(), {})
            self.write(self.home / ".dotfiles-env", "work\n")
            self.assertEqual(resolve_profile(home=self.home), "work")
            with patch.dict(os.environ, {"DOTFILES_ENV": "invalid"}):
                with self.assertRaises(ValueError):
                    resolve_profile(home=self.home)
                self.assertEqual(resolve_profile("home", self.home), "home")

    def test_private_login_script_blocks_home_to_work_before_any_writes(self):
        self.apply("home", self.overlay)
        self.write(
            self.home / ".zprofile", f'source "{self.overlay}/personal-startup.zsh"\n'
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "review remaining reference"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_backup_symlink_cannot_redirect_private_configuration(self):
        outside = self.base / "outside"
        outside.mkdir()
        backups = self.home / ".config/dotfiles/backups"
        backups.parent.mkdir(parents=True)
        backups.symlink_to(outside, target_is_directory=True)
        self.write(self.home / ".gitconfig", "# existing private settings\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "symlinked backup"):
            self.apply()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_backup_junction_cannot_redirect_private_configuration(self):
        outside = self.base / "outside-backups"
        outside.mkdir()
        backups = self.home / ".config/dotfiles/backups"
        backups.parent.mkdir(parents=True)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(backups), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest("junction creation is unavailable")

        self.write(self.home / ".gitconfig", "# existing private settings\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "symlinked backup"):
            self.apply()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_dry_run_writes_nothing(self):
        self.apply(dry=True)
        self.assertEqual(self.snapshot(), {})

    def test_preserves_git_shell_settings_and_is_idempotent(self):
        self.write(
            self.home / ".gitconfig",
            "[user]\n\tname = Work\n\temail = work@example.test\n",
        )
        self.write(self.home / ".zshrc", "# existing shell\n")
        self.write(self.home / ".copilot/settings.json", '{"custom": 123}')
        self.apply()
        self.assertIn("name = Work", (self.home / ".gitconfig").read_text())
        if os.name != "nt":
            self.assertIn("# existing shell", (self.home / ".zshrc").read_text())
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        self.assertEqual(settings["custom"], 123)
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertTrue(settings["footer"]["showUsername"])
        before = self.snapshot()
        self.apply()
        self.assertEqual(before, self.snapshot())

    def test_managed_copilot_home_receives_configuration(self):
        copilot_home = self.base / "managed-copilot"
        self.plan(copilot_home=copilot_home).apply(False)

        self.assertFalse((self.home / ".copilot").exists())
        self.assertIn(
            "Write clearly.",
            (copilot_home / "copilot-instructions.md").read_text(),
        )
        settings = json.loads((copilot_home / "settings.json").read_text())
        denied = settings["sandbox"]["userPolicy"]["filesystem"]["deniedPaths"]
        self.assertIn(str(copilot_home / "session-state"), denied)

    def test_changed_copilot_home_requires_review(self):
        first = self.base / "managed-copilot-first"
        second = self.base / "managed-copilot-second"
        self.plan(copilot_home=first).apply(False)
        before = self.snapshot()

        with self.assertRaisesRegex(ValueError, "COPILOT_HOME changed"):
            self.plan(copilot_home=second)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue((first / "settings.json").exists())
        self.assertFalse(second.exists())

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_symlinked_copilot_home_cannot_redirect_writes(self):
        outside = self.base / "outside-copilot"
        outside.mkdir()
        (self.home / ".copilot").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlinked configuration directory"):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_junctioned_copilot_home_cannot_redirect_writes(self):
        outside = self.base / "outside-copilot"
        outside.mkdir()
        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(self.home / ".copilot"),
                str(outside),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest("junction creation is unavailable")

        with self.assertRaisesRegex(ValueError, "symlinked configuration directory"):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_copilot_home_beneath_junction_is_pinned_to_physical_path(self):
        outside = self.base / "outside-parent"
        outside.mkdir()
        junction = self.base / "junction-parent"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest("junction creation is unavailable")

        installer = self.plan(copilot_home=junction / "managed-copilot")
        self.assertEqual(
            installer.copilot_home,
            (outside / "managed-copilot").resolve(),
        )
        installer.apply(False)
        self.assertTrue((outside / "managed-copilot/settings.json").exists())

    def test_unknown_file_blocks_entire_install(self):
        self.write(self.home / ".config/shared.txt", "locally customized")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.apply()
        self.assertEqual(before, self.snapshot())

    def test_home_to_work_removes_owned_private_files_and_rules(self):
        self.apply("home", self.overlay)
        self.assertIn("Personal automation", (self.home / "AGENTS.md").read_text())
        self.apply("work")
        self.assertFalse((self.home / ".config/private.txt").exists())
        self.assertNotIn("Personal automation", (self.home / "AGENTS.md").read_text())
        self.assertNotIn("gitconfig.home", (self.home / ".gitconfig").read_text())
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        self.assertNotIn("research", settings["subagents"]["agents"])
        self.assertEqual((self.home / ".dotfiles-env").read_text(), "work\n")

    def test_modified_private_copy_blocks_transition(self):
        self.apply("home", self.overlay)
        target = self.home / ".config/private.txt"
        target.unlink()
        self.write(target, "local change")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "modified file"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_modified_managed_block_blocks_transition(self):
        self.apply("home", self.overlay)
        target = self.home / "AGENTS.md"
        target.write_text(
            target.read_text().replace("Personal automation", "User customization")
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "edited managed block"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_unmanaged_agents_need_review_for_work(self):
        self.write(self.home / "AGENTS.md", "Unreviewed global automation")
        with self.assertRaisesRegex(ValueError, "existing global agent instructions"):
            self.apply("work")
        self.assertEqual(len(self.snapshot()), 1)

    def test_unowned_rules_outside_home_block_block_work(self):
        self.write(self.home / "AGENTS.md", "Personal standing authorization.\n")
        self.apply("home", self.overlay)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "existing global agent instructions"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_legacy_git_include_is_adopted_and_removed_on_work(self):
        self.write(
            self.home / ".gitconfig",
            "[include]\n\tpath = "
            + str(self.overlay / "gitconfig").replace(chr(92), "/")
            + "\n",
        )
        self.apply("home", self.overlay)
        self.apply("work")
        self.assertNotIn(
            str(self.overlay).replace(chr(92), "/"),
            (self.home / ".gitconfig").read_text(),
        )

    def test_private_reference_outside_managed_shell_block_blocks_work(self):
        self.apply("home", self.overlay)
        target = self.home / (
            "Documents/PowerShell/profile.ps1" if os.name == "nt" else ".zshrc"
        )
        target.write_text(
            target.read_text()
            + "\n# source "
            + str(self.overlay / "custom-startup")
            + "\n"
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_external_mcp_blocks_transition(self):
        self.apply("home", self.overlay)
        self.write(
            self.home / ".codex/config.toml",
            '[mcp_servers.private]\nurl = "https://example.test"\n',
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "MCP/plugin"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_work_rejects_home_only_overlay(self):
        with self.assertRaisesRegex(ValueError, r"profiles[/\\]work\.json"):
            self.plan("work", self.overlay)
        self.assertEqual(self.snapshot(), {})

    def work_overlay(self, name="work-overlay"):
        overlay = self.base / name
        for relative, content in {
            "profiles/work.json": json.dumps(
                {"files": {".config/work.txt": "work.txt"}}
            ),
            "work.txt": name + " configuration\n",
            "agents/work.md": name + " instructions\n",
            "gitconfig.work": "[user]\n\tname = Example Developer\n",
            "zshrc.work": "export OVERLAY_TEST=" + name + "\n",
            "bashrc.work": "export OVERLAY_TEST=" + name + "\n",
            "tmux.work.conf": "# work tmux\n",
            "powershell/profile.work.ps1": "$env:OVERLAY_TEST = '" + name + "'\n",
            # The selected profile must never fall back to these files.
            "agents/home.md": "UNSELECTED HOME INSTRUCTIONS\n",
            "gitconfig.home": "[user]\n\tname = Unselected Home\n",
        }.items():
            self.write(overlay / relative, content)
        return overlay

    def test_work_overlay_fresh_repeat_switch_and_removal(self):
        first, second = self.work_overlay(), self.work_overlay("replacement")
        self.apply("work", first, dry=True)
        self.assertEqual(self.snapshot(), {})
        with self.assertRaisesRegex(ValueError, r"profiles[/\\]home\.json"):
            self.apply("home", first)
        self.apply("work", first)
        self.assertIn("Follow employer rules", (self.home / "AGENTS.md").read_text())
        self.assertIn("work-overlay instructions", (self.home / "AGENTS.md").read_text())
        self.assertNotIn("UNSELECTED HOME", (self.home / "AGENTS.md").read_text())
        self.assertIn("gitconfig.work", (self.home / ".gitconfig").read_text())
        self.assertNotIn("gitconfig.home", (self.home / ".gitconfig").read_text())
        self.assertEqual((self.home / ".config/work.txt").read_text(), "work-overlay configuration\n")
        before = self.snapshot()
        self.apply("work", first)
        self.assertEqual(before, self.snapshot())
        self.apply("work", second)
        self.assertNotIn("work-overlay instructions", (self.home / "AGENTS.md").read_text())
        self.assertIn("replacement instructions", (self.home / "AGENTS.md").read_text())
        self.assertEqual((self.home / ".config/work.txt").read_text(), "replacement configuration\n")
        self.apply("work")
        self.assertFalse((self.home / ".config/work.txt").exists())
        state = json.loads((self.home / ".config/dotfiles/state.json").read_text())
        self.assertNotIn("overlay", state)
        for path, content in self.snapshot().items():
            if "/backups/" not in path:
                self.assertNotIn(str(second), str(content), path)
        before = self.snapshot()
        self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_home_to_work_overlay_removes_personal_state(self):
        work = self.work_overlay()
        self.apply("home", self.overlay)
        before = self.snapshot()
        self.apply("home", self.overlay)
        self.assertEqual(before, self.snapshot())
        self.apply("work", work)
        self.assertFalse((self.home / ".config/private.txt").exists())
        for path, content in self.snapshot().items():
            if "/backups/" not in path:
                self.assertNotIn(str(self.overlay), str(content), path)
                self.assertNotIn("Personal automation", str(content), path)
                self.assertNotIn("personal-model", str(content), path)
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertEqual((self.home / ".dotfiles-env").read_text(), "work\n")
        self.apply("home", self.overlay)
        self.assertFalse((self.home / ".config/work.txt").exists())
        self.assertNotIn("work-overlay instructions", (self.home / "AGENTS.md").read_text())
        self.assertIn("Personal automation", (self.home / "AGENTS.md").read_text())

    def test_overlay_can_omit_all_optional_files(self):
        overlay = self.base / "empty-work"
        self.write(overlay / "profiles/work.json", "{}")
        self.apply("work", overlay)
        self.assertNotIn("gitconfig.work", (self.home / ".gitconfig").read_text())
        self.assertIn("Follow employer rules", (self.home / "AGENTS.md").read_text())

    def test_overlay_cannot_replace_shared_or_generated_configuration(self):
        overlay = self.work_overlay()
        for relative in (
            ".config/shared.txt", ".gitconfig", ".dotfiles-env",
            ".config/dotfiles/state.json", ".copilot/settings.json",
            ".config", ".", ".config/shared.txt/child",
            ".config/dotfiles/backups/extra.txt",
        ):
            with self.subTest(relative=relative):
                self.write(overlay / "profiles/work.json", json.dumps({"files": {relative: "work.txt"}}))
                with self.assertRaisesRegex(ValueError, "reserved|already configured|invalid installation"):
                    self.apply("work", overlay)
                self.assertEqual(self.snapshot(), {})

    def test_overlay_switch_requires_review_of_external_startup_reference(self):
        overlay = self.work_overlay()
        self.apply("work", overlay)
        self.write(self.home / ".zprofile", f'source "{overlay}/external.zsh"\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("work", self.work_overlay("replacement"))
        self.assertEqual(before, self.snapshot())

    def test_retained_work_overlay_reference_does_not_block_relink(self):
        overlay = self.work_overlay()
        self.apply("work", overlay)
        target = self.home / ".gitconfig"
        target.write_text(target.read_text() + "\n# " + str(overlay) + "\n")
        self.apply("work", overlay)
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("work")

    def test_profile_switch_in_same_overlay_reviews_external_startup(self):
        overlay = self.work_overlay()
        self.write(overlay / "profiles/home.json", "{}")
        self.apply("work", overlay)
        self.write(self.home / ".zprofile", f'source "{overlay}/zshrc.work"\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("home", overlay)
        self.assertEqual(before, self.snapshot())

    def test_shell_additions_follow_selected_profile_on_both_platforms(self):
        overlay = self.work_overlay()
        # The CI matrix executes this with native paths and file semantics on each OS.
        self.apply("work", overlay)
        if os.name == "nt":
            target = self.home / "Documents/PowerShell/profile.ps1"
            self.assertIn("profile.work.ps1", target.read_text())
            self.assertIn("DOTFILES_PRIVATE_DIR", target.read_text())
            self.assertFalse((self.home / ".config/work.txt").is_symlink())
        else:
            for name in (".zshrc", ".bashrc"):
                self.assertIn(name[1:] + ".work", (self.home / name).read_text())
            self.assertIn("tmux.work.conf", (self.home / ".tmux.conf").read_text())
            target = self.home / ".zshrc"
        self.apply("work")
        self.assertNotIn(str(overlay), target.read_text())
        self.assertIn("DOTFILES_PRIVATE_DIR", target.read_text())

    def test_native_shell_loads_selected_overlay_and_clears_removed_path(self):
        overlay = self.work_overlay()
        self.apply("work", overlay)
        environment = dict(os.environ, DOTFILES_PRIVATE_DIR="stale-overlay")
        environment.pop("OVERLAY_TEST", None)
        if os.name == "nt":
            shell = shutil.which("pwsh")
            self.assertIsNotNone(shell, "PowerShell 7 is required for the Windows setup tests")
            target = str(self.home / "Documents/PowerShell/profile.ps1").replace("'", "''")
            command = [shell, "-NoProfile", "-Command", f". '{target}'; Write-Output \"$env:DOTFILES_ENV|$env:DOTFILES_PRIVATE_DIR|$env:OVERLAY_TEST\""]
        else:
            command = ["bash", "--noprofile", "--norc", "-c", '. "$1"; printf "%s|%s|%s\\n" "$DOTFILES_ENV" "$DOTFILES_PRIVATE_DIR" "$OVERLAY_TEST"', "test", str(self.home / ".bashrc")]
        result = subprocess.run(command, env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), f"work|{overlay}|work-overlay")
        self.apply("work")
        result = subprocess.run(command, env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "work||")

    def test_readonly_blocks_before_writes(self):
        target = self.home / ".gitconfig"
        self.write(target, "# read only\n")
        target.chmod(0o444)
        self.addCleanup(target.chmod, 0o644)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "non-writable"):
            self.apply()
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == "nt", "symlinks require Windows developer mode")
    def test_symlink_parent_cannot_redirect_writes(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.home / ".config").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked configuration directory"):
            self.apply()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "native Windows installation")
    def test_user_wezterm_override_is_checked_even_when_process_is_shared(self):
        for process, user in [(self.repo, self.overlay), (self.overlay, self.repo)]:
            with (
                patch.dict(
                    os.environ, {"WEZTERM_CONFIG_FILE": str(process / "wezterm.lua")}
                ),
                self.assertRaisesRegex(ValueError, "WEZTERM_CONFIG_FILE"),
            ):
                link.plan(
                    self.repo,
                    self.home,
                    "work",
                    "windows",
                    powershell_profile=str(
                        self.home / "Documents/PowerShell/profile.ps1"
                    ),
                    wezterm_user_config=str(user / "wezterm.lua"),
                )
        self.assertEqual(self.snapshot(), {})

    @unittest.skipUnless(os.name == "nt", "native Windows installation")
    def test_windows_copies_and_preserves_powershell_profile(self):
        target = self.home / "Documents/PowerShell/profile.ps1"
        self.write(target, "# existing profile\n")
        self.apply()
        self.assertFalse((self.home / ".config/shared.txt").is_symlink())
        self.assertIn("# existing profile", target.read_text())
        self.assertIn("return dofile", (self.home / ".wezterm.lua").read_text())

    @unittest.skipUnless(os.name == "nt", "native Windows installation")
    def test_existing_wezterm_requires_review(self):
        self.write(self.home / ".wezterm.lua", "return {}\n")
        with self.assertRaisesRegex(ValueError, "existing WezTerm"):
            self.apply()
        self.assertEqual(len(self.snapshot()), 1)


class PolicyTests(unittest.TestCase):
    def test_policy_requires_exact_mandatory_settings(self):
        spec = importlib.util.spec_from_file_location(
            "policy", ROOT / "bin/check-copilot-policy.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            policy = {
                "sandbox": {
                    "enabled": True,
                    "failIfUnavailable": True,
                    "allowBypass": False,
                }
            }
            path.write_text(json.dumps(policy))
            module.validate_policy(path, posix=False)
            for key in policy["sandbox"]:
                changed = json.loads(json.dumps(policy))
                changed["sandbox"].pop(key)
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, key):
                    module.validate_policy(path, posix=False)


if __name__ == "__main__":
    unittest.main()

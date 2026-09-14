#!/usr/bin/env python3
"""Install profile packages without changing repositories, accounts, or dotfiles."""

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def query(command):
    return subprocess.run(command, capture_output=True, text=True, check=False)


def detect_manager():
    system = platform.system()
    if system == "Windows":
        return "winget"
    if system == "Darwin":
        return "brew"
    if system == "Linux":
        release = platform.freedesktop_os_release()
        family = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
        if "rhel" in family:
            return "dnf"
    raise ValueError("Supported hosts are macOS, Windows, and RHEL-compatible Linux.")


def package_installed(package, manager):
    if manager == "dnf" and package.get("dnf"):
        return query(["rpm", "-q", package["dnf"]]).returncode == 0
    if manager == "brew":
        kind = "--cask" if package.get("cask") else "--formula"
        return (
            query(["brew", "list", "--versions", kind, package["brew"]]).returncode == 0
        )
    return False


def available(package, manager):
    if package["name"] == "Python":
        return sys.version_info >= (3, 11)
    if package.get("module"):
        pwsh = shutil.which("pwsh")
        if not pwsh:
            return False
        return (
            query(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "if (Get-Module -ListAvailable -Name PSFzf) { exit 0 } else { exit 1 }",
                ]
            ).returncode
            == 0
        )
    commands = package.get(
        "commands", [package["command"]] if "command" in package else []
    )
    if commands:
        return all(shutil.which(command) is not None for command in commands)
    return package_installed(package, manager)


def make_plan(packages, manager):
    pending, missing, notes = [], [], []
    for package in packages:
        name = package["name"]
        if manager not in package.get("platforms", ["brew", "dnf", "winget"]):
            if package.get("unsupported"):
                notes.append(f"{name}: {package['unsupported']}")
            continue
        if available(package, manager):
            print(f"Present: {name}")
            continue
        problem = None
        if manager == "dnf":
            rpm = package.get("dnf")
            if not rpm:
                problem = "no DNF package is configured; install the upstream tool through an approved source"
            elif package_installed(package, manager):
                problem = f"RPM {rpm} is installed but {package.get('command', name)} is not on PATH"
            else:
                result = query(["dnf", "-q", "list", "--available", rpm])
                if result.returncode:
                    detail = (result.stderr or result.stdout).strip()
                    problem = f"RPM {rpm} is unavailable from enabled repositories"
                    if detail:
                        problem += f" ({detail})"
        if problem:
            message = f"{name}: {problem}. Source: {package['source']}"
            (notes if package.get("optional") else missing).append(message)
        else:
            pending.append(package)
    return pending, missing, notes


def install_command(package, manager):
    if package.get("module"):
        return [
            shutil.which("pwsh") or "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "$ErrorActionPreference = 'Stop'; Install-Module -Name PSFzf -Scope CurrentUser -Repository PSGallery",
        ]
    if manager == "winget":
        return [
            "winget",
            "install",
            "--exact",
            "--id",
            package["winget"],
            "--source",
            "winget",
        ]
    if manager == "brew":
        return [
            "brew",
            "install",
            *(["--cask"] if package.get("cask") else []),
            package["brew"],
        ]
    prefix = [] if os.geteuid() == 0 else ["sudo"]
    return [*prefix, "dnf", "install", "-y", package["dnf"]]


def refresh_windows_path():
    # Installers update the registry, not the current Python process's environment.
    import winreg

    additions = []
    for hive, key in [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ]:
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _ = winreg.QueryValueEx(handle, "Path")
                additions.append(os.path.expandvars(value))
        except FileNotFoundError:
            pass
    paths = os.pathsep.join([os.environ.get("PATH", ""), *additions]).split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys(path for path in paths if path))


def check_bubblewrap():
    result = query(["bwrap", "--version"])
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", result.stdout)
    if (
        result.returncode
        or not match
        or tuple(int(part or 0) for part in match.groups()) < (0, 5, 0)
    ):
        raise ValueError(
            "Copilot requires bubblewrap >= 0.5.0. Obtain a supported version through an approved source."
        )


def check_sandbox_dependencies():
    if not Path("/dev/net/tun").is_char_device():
        raise ValueError(
            "Copilot's Linux proxy requires /dev/net/tun. Ask the host administrator to provide the TUN device."
        )
    if shutil.which("bwrap"):
        check_bubblewrap()
    if shutil.which("unshare"):
        version = query(["unshare", "--version"])
        match = re.search(r"(\d+)\.(\d+)", version.stdout)
        help_text = query(["unshare", "--help"])
        if (
            version.returncode
            or not match
            or tuple(map(int, match.groups())) < (2, 35)
            or help_text.returncode
            or "--map-current-user" not in help_text.stdout
            or "--keep-caps" not in help_text.stdout
        ):
            raise ValueError(
                "Copilot's Linux proxy requires util-linux >= 2.35 and unshare --map-current-user/--keep-caps. Obtain a supported version through an approved source."
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["work", "home"])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect dependencies and print commands without installing",
    )
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3, 11):
            raise ValueError("Python 3.11+ is required.")
        from dotfiles_profile import resolve_profile

        profile = resolve_profile(args.profile, Path.home())
        manager = detect_manager()
        if not shutil.which(manager):
            raise ValueError(
                f"{manager} is required. Install it through an approved source and add it to PATH first."
            )
        manifest = json.loads(
            (ROOT / "profiles" / "packages.json").read_text(encoding="utf-8")
        )
        packages = manifest["common"] + (manifest["home"] if profile == "home" else [])
        print(f"Profile: {profile}; package manager: {manager}")
        pending, missing, notes = make_plan(packages, manager)
        for note in notes:
            print(f"Skipped: {note}")
        for package in pending:
            prefix = "Optional" if package.get("optional") else "Install"
            print(f"{prefix}: {shlex.join(install_command(package, manager))}")
        if manager == "dnf":
            print(
                "Only enabled repositories are used. Repository access errors also block preflight."
            )
            try:
                check_sandbox_dependencies()
            except ValueError as error:
                missing.append(str(error))
        if missing:
            raise ValueError(
                "Required tools are missing; no packages were installed:\n  "
                + "\n  ".join(missing)
            )
        if args.dry_run:
            print("Dry run complete. No packages or profiles were changed.")
            return 0
        if (
            manager == "dnf"
            and pending
            and os.geteuid() != 0
            and not shutil.which("sudo")
        ):
            raise ValueError(
                "Package installation requires sudo or an administrator-run installer."
            )
        for package in pending:
            try:
                subprocess.run(install_command(package, manager), check=True)
                if manager == "winget":
                    refresh_windows_path()
                if not available(package, manager):
                    raise ValueError(
                        f"{package['name']} is still unavailable. Reopen the terminal or correct PATH, then rerun."
                    )
            except (subprocess.CalledProcessError, ValueError) as error:
                if not package.get("optional"):
                    raise
                notes.append(f"{package['name']}: {error}")
                print(f"Optional installation failed: {notes[-1]}")
        if manager == "dnf":
            check_sandbox_dependencies()
            print(
                "Linux sandbox dependencies are available; this does not verify an active Copilot sandbox."
            )
        print(
            "Required tools are available"
            + ("; optional items were skipped or failed." if notes else ".")
        )
        if manager == "winget":
            script = str(ROOT / "link.ps1").replace("'", "''")
            command = f"& '{script}' -Profile {profile}"
        else:
            command = shlex.join(["sh", str(ROOT / "link.sh"), "--profile", profile])
        print(f"Configure the shell separately: {command}")
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Apply hardened cross-platform defaults to Copilot CLI settings."""

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


SENSITIVE_PATHS = (
    ".aws",
    ".claude",
    ".codex",
    ".config/alerts",
    ".config/Bitwarden CLI",
    ".config/gh",
    ".copilot/logs",
    ".copilot/permissions-config.json",
    ".copilot/session-state",
    ".git-credentials",
    ".gnupg",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    ".tailscale-oauth.env",
    ".bash_history",
    ".zsh_history",
)

WINDOWS_SENSITIVE_PATHS = (
    "AppData/Local/Bitwarden",
    "AppData/Roaming/Bitwarden",
    "AppData/Roaming/GitHub CLI",
)

UNIX_SENSITIVE_PATHS = (
    "Library/Application Support/Bitwarden",
    "Library/Application Support/Bitwarden CLI",
)

COPILOT_SENSITIVE_PATHS = (
    "logs",
    "permissions-config.json",
    "session-state",
)


def require_object(parent, key, label):
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def require_string_list(parent, key, label):
    value = parent.get(key)
    if value is None:
        value = []
        parent[key] = value
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings")
    return value


def home_path(home, relative, platform):
    path_class = PureWindowsPath if platform == "windows" else PurePosixPath
    home_path_value = path_class(home)
    if not home_path_value.is_absolute():
        raise ValueError("--home must be an absolute path")
    return str(home_path_value.joinpath(*relative.split("/")))


def merge_unique(existing, additions):
    return list(dict.fromkeys([*existing, *additions]))


def build_settings(
    existing,
    subagents,
    home,
    platform,
    sandbox_enabled=True,
    copilot_home=None,
):
    if not isinstance(existing, dict):
        raise ValueError("Copilot settings must be a JSON object")
    if not isinstance(subagents, dict) or any(
        not isinstance(name, str) or not isinstance(config, dict)
        for name, config in subagents.items()
    ):
        raise ValueError("subagent settings must be an object of objects")

    existing["memory"] = False
    existing["effortLevel"] = "high"
    existing["includeCoAuthoredBy"] = True
    existing["theme"] = "default"
    existing["experimental"] = True
    existing["defaultPermissionMode"] = "manual"

    footer = require_object(existing, "footer", "footer")
    footer.update(
        {
            "showModelEffort": True,
            "showDirectory": False,
            "showBranch": False,
            "showContextWindow": True,
            "showQuota": True,
            "showAiUsed": False,
            "showAgent": False,
            "showCodeChanges": False,
            "showUsername": False,
            "showSandbox": True,
            "showYolo": True,
            "showCiStatus": False,
            "showPullRequest": False,
            "showCustom": False,
        }
    )

    configured_subagents = require_object(existing, "subagents", "subagents")
    agents = require_object(configured_subagents, "agents", "subagents.agents")
    for name, config in subagents.items():
        target = require_object(agents, name, f"subagents.agents.{name}")
        target.update(config)

    disabled_mcp_servers = require_string_list(
        existing, "disabledMcpServers", "disabledMcpServers"
    )
    existing["disabledMcpServers"] = merge_unique(
        disabled_mcp_servers, ["github-mcp-server"]
    )

    sandbox = require_object(existing, "sandbox", "sandbox")
    sandbox.update(
        {
            "enabled": sandbox_enabled,
            "addCurrentWorkingDirectory": True,
            "sandboxMcpServers": True,
            "sandboxLspServers": True,
            "allowBypass": False,
            "allowDevToolAccess": False,
        }
    )

    auth = require_object(sandbox, "auth", "sandbox.auth")
    auth.update({"git": False, "gh": False})

    user_policy = require_object(sandbox, "userPolicy", "sandbox.userPolicy")
    filesystem = require_object(
        user_policy, "filesystem", "sandbox.userPolicy.filesystem"
    )
    denied_paths = require_string_list(
        filesystem, "deniedPaths", "sandbox.userPolicy.filesystem.deniedPaths"
    )
    platform_paths = (
        WINDOWS_SENSITIVE_PATHS if platform == "windows" else UNIX_SENSITIVE_PATHS
    )
    if copilot_home is None:
        copilot_home = home_path(home, ".copilot", platform)
    path_class = PureWindowsPath if platform == "windows" else PurePosixPath
    copilot_home_path = path_class(copilot_home)
    if not copilot_home_path.is_absolute():
        raise ValueError("--copilot-home must be an absolute path")
    filesystem["deniedPaths"] = merge_unique(
        denied_paths,
        [
            *[
                home_path(home, path, platform)
                for path in (*SENSITIVE_PATHS, *platform_paths)
            ],
            *[
                str(copilot_home_path.joinpath(*path.split("/")))
                for path in COPILOT_SENSITIVE_PATHS
            ],
        ],
    )

    network = require_object(user_policy, "network", "sandbox.userPolicy.network")
    network.update({"allowOutbound": True, "allowLocalNetwork": False})

    if platform == "unix":
        seatbelt = require_object(user_policy, "seatbelt", "sandbox.userPolicy.seatbelt")
        seatbelt["keychainAccess"] = False

    return existing


def load_json(path, label):
    try:
        with path.open(encoding="utf-8-sig") as stream:
            return json.load(stream)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error


def write_settings(path, settings):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to replace symlinked settings file: {path}")
    if os.name != "nt":
        os.chmod(path.parent, stat.S_IRWXU)

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(settings, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary_path, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, TypeError):
        temporary_path.unlink(missing_ok=True)
        raise


def apply_settings(
    settings_path,
    subagents_path,
    home,
    platform,
    sandbox_enabled=True,
    copilot_home=None,
):
    settings = load_json(settings_path, "Copilot settings") if settings_path.exists() else {}
    subagents = load_json(subagents_path, "subagent settings")
    updated = build_settings(
        settings,
        subagents,
        home,
        platform,
        sandbox_enabled=sandbox_enabled,
        copilot_home=copilot_home,
    )
    write_settings(settings_path, updated)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--subagents", required=True, type=Path)
    parser.add_argument("--home", required=True)
    parser.add_argument("--copilot-home")
    parser.add_argument("--platform", required=True, choices=("unix", "windows"))
    parser.add_argument(
        "--sandbox-enabled",
        choices=("true", "false"),
        default="true",
    )
    args = parser.parse_args(argv)
    try:
        apply_settings(
            args.settings,
            args.subagents,
            args.home,
            args.platform,
            sandbox_enabled=args.sandbox_enabled == "true",
            copilot_home=args.copilot_home,
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"apply-copilot-settings: {error}\n")


if __name__ == "__main__":
    main()

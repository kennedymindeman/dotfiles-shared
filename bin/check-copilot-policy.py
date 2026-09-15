#!/usr/bin/env python3
"""Check a local managed sandbox policy for the work launcher."""

import json
import os
import stat
import sys
from pathlib import Path


def validate_policy(path, posix=None):
    posix = os.name != "nt" if posix is None else posix
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing regular managed policy file: {path}")
    if posix:
        for item in (path, *path.parents):
            info = item.stat()
            if (
                item.is_symlink()
                or info.st_uid != 0
                or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                raise ValueError(
                    f"managed policy must be root-owned and protected from user writes: {item}"
                )
    policy = json.loads(path.read_text(encoding="utf-8-sig"))
    sandbox = policy.get("sandbox", {})
    for key, expected in (
        ("enabled", True),
        ("failIfUnavailable", True),
        ("allowBypass", False),
    ):
        if sandbox.get(key) is not expected:
            raise ValueError(
                f"managed policy must set sandbox.{key} to {str(expected).lower()}"
            )


def main():
    if os.name == "nt":
        root = os.environ.get("ProgramFiles")
        if not root:
            raise ValueError("ProgramFiles is unavailable")
        path = Path(root) / "GitHubCopilot/managed-settings.json"
    elif sys.platform == "darwin":
        path = Path("/Library/Application Support/GitHubCopilot/managed-settings.json")
    else:
        path = Path("/etc/github-copilot/managed-settings.json")
    try:
        path.lstat()
    except FileNotFoundError:
        print(
            f"copilot: warning: administrator-managed sandbox policy is missing: {path}",
            file=sys.stderr,
        )
        return
    validate_policy(path)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, AttributeError) as error:
        sys.exit(
            f"copilot: work requires administrator-managed mandatory sandboxing: {error}"
        )

#!/usr/bin/env -S python3 -I
"""PreToolUse hook (matcher: Bash) for the researcher agent, which reads untrusted
web content. Allows exactly three command shapes and blocks the rest (exit 2):

  ~/.claude/hooks/research-fetch.py ARGS   the fetch-media recipes; it validates ARGS
  mktemp -d [-t PREFIX]
  rm -rf DIR...                            each DIR made by mktemp -d

A command may use only letters, digits, spaces, quotes, and _-./:=,+@%. That
leaves no way to chain, pipe, redirect, substitute, glob, or expand, so the
argv shlex sees is the argv the shell runs. No fetch-media recipe needs more:
the wrapper takes video ids and KEY=VALUE pairs instead of URLs with ? and &.
The single exception is the literal leading `~/.claude/hooks/research-fetch.py`,
whose tilde expands to this hook's own idea of home.
"""

import json
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import Path

WRAPPER = str(Path.home() / ".claude/hooks/research-fetch.py")
SAFE = re.compile(r"[A-Za-z0-9 _\-./:=,+@%'\"]*")
MKTEMP_NAME = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9]{8,}")
ALLOWED = """researcher Bash runs only these, one command per call, with no shell syntax
(no ; & | $ ` ( ) < > * ? ~ \\ or newlines):
  ~/.claude/hooks/research-fetch.py transcript VIDEO_ID
  ~/.claude/hooks/research-fetch.py formats VIDEO_ID
  ~/.claude/hooks/research-fetch.py storyboard VIDEO_ID DIR
  ~/.claude/hooks/research-fetch.py slice VIDEO_ID START END DIR [FRAME]
  ~/.claude/hooks/research-fetch.py reddit /r/SUB/PATH [KEY=VALUE ...]
  mktemp -d [-t PREFIX]
  rm -rf DIR   (DIR printed by mktemp -d)
Use Read, Grep, Glob, WebFetch, and WebSearch for everything else."""


def block(reason):
    print(f"Blocked ({reason}). {ALLOWED}", file=sys.stderr)
    sys.exit(2)


def is_scratch(path):
    """True for an existing dir you own, mode 700, named like mktemp -d output,
    somewhere under the temp dir. Symlinks are not followed."""
    if not path.startswith("/") or "/../" in path + "/" or "/./" in path + "/":
        return False
    try:
        st = os.lstat(path)
        parent = os.path.realpath(os.path.dirname(path.rstrip("/")))
    except OSError:
        return False
    # mktemp -d and gettempdir() both use $TMPDIR when set, else /tmp.
    root = os.path.realpath(tempfile.gettempdir())
    return (
        stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and stat.S_IMODE(st.st_mode) == 0o700
        and MKTEMP_NAME.fullmatch(os.path.basename(path.rstrip("/"))) is not None
        and (parent == root or parent.startswith(root + "/"))
    )


def check(command):
    """Return None when allowed, else the block reason."""
    tilde = "~/.claude/hooks/research-fetch.py"
    if command == tilde or command.startswith(tilde + " "):
        command = WRAPPER + command[len(tilde) :]
    bad = sorted(set(SAFE.sub("", command)))
    if bad:
        return "shell syntax not allowed: " + " ".join(repr(c) for c in bad)
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"cannot parse: {e}"
    if not argv:
        return "empty command"
    if any(arg.startswith("=") for arg in argv):
        return "a word starting with = is zsh path expansion"
    return check_argv(argv)


def check_argv(argv):
    if argv[0] == WRAPPER:
        return None
    if argv == ["mktemp", "-d"] or (
        len(argv) == 4
        and argv[:3] == ["mktemp", "-d", "-t"]
        and re.fullmatch(r"[A-Za-z0-9_-]+", argv[3])
    ):
        return None
    if argv[0] == "rm":
        if len(argv) < 3 or argv[1] not in ("-rf", "-fr"):
            return "rm must be rm -rf DIR"
        for path in argv[2:]:
            if not is_scratch(path):
                return f"{path} is not a dir made by mktemp -d"
        return None
    return f"{argv[0]} is not an allowed command"


def main():
    try:
        payload = json.load(sys.stdin)
        command = payload["tool_input"]["command"]
    except (ValueError, KeyError, TypeError):
        block("unreadable hook input")
    if payload.get("tool_name", "Bash") != "Bash":
        return
    if not isinstance(command, str):
        block("command is not a string")
    reason = check(command)
    if reason:
        block(reason)


if __name__ == "__main__":
    main()

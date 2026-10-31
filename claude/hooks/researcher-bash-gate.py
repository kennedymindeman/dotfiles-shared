#!/usr/bin/env -S python3 -I
"""PreToolUse hook (matcher: Bash) for the researcher agent, which reads untrusted
web content. Allows one command, ~/.claude/hooks/research-fetch.py, and blocks
the rest (exit 2). The wrapper validates its own arguments, including the
scratch dirs it creates and deletes.

A command may use only letters, digits, spaces, quotes, and _-./:=,+@%. That
leaves no way to chain, pipe, redirect, substitute, glob, or expand, so the
argv shlex sees is the argv the shell runs. No fetch-media recipe needs more:
the wrapper takes video ids and KEY=VALUE pairs instead of URLs with ? and &.
Two exceptions: the literal leading `~/.claude/hooks/research-fetch.py`, whose
tilde expands to this hook's own idea of home, and the URL of `get`, which may
hold ? & = # and the like when it is one single-quoted word. Nothing expands
inside single quotes, and the URL may not contain a quote, space, or control
character, so the word cannot end early.
"""

import json
import re
import shlex
import sys
from pathlib import Path

WRAPPER = str(Path.home() / ".claude/hooks/research-fetch.py")
SAFE = re.compile(r"[A-Za-z0-9 _\-./:=,+@%'\"]*")
ALLOWED = """researcher Bash runs only ~/.claude/hooks/research-fetch.py, one call at a
time, with no shell syntax (no ; & | $ ` ( ) < > * ? ~ \\ or newlines):
  ~/.claude/hooks/research-fetch.py scratch      (prints a scratch DIR)
  ~/.claude/hooks/research-fetch.py clean DIR
  ~/.claude/hooks/research-fetch.py transcript VIDEO_ID
  ~/.claude/hooks/research-fetch.py formats VIDEO_ID
  ~/.claude/hooks/research-fetch.py storyboard VIDEO_ID DIR
  ~/.claude/hooks/research-fetch.py slice VIDEO_ID START END DIR [FRAME]
  ~/.claude/hooks/research-fetch.py reddit /r/SUB/PATH [KEY=VALUE ...]
  ~/.claude/hooks/research-fetch.py get 'URL' [MAX_KB] [DIR]   (single-quote the URL)
  ~/.claude/hooks/research-fetch.py help
Use Read, Grep, Glob, WebFetch, and WebSearch for everything else."""


def block(reason):
    print(f"Blocked ({reason}). {ALLOWED}", file=sys.stderr)
    sys.exit(2)


def check(command):
    """Return None when allowed, else the block reason."""
    tilde = "~/.claude/hooks/research-fetch.py"
    if command == tilde or command.startswith(tilde + " "):
        command = WRAPPER + command[len(tilde) :]
    url = re.match(re.escape(WRAPPER) + r" get ('[!-&(-~]+')( |$)", command)
    if url:
        command = command[: url.start(1)] + "URL" + command[url.end(1) :]
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
    if argv[0] != WRAPPER:
        return f"{argv[0]} is not an allowed command"
    return None


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

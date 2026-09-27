#!/usr/bin/env python3
"""Replace em dashes, en dashes, and ellipses in skill markdown with ASCII.

Windows PowerShell 5.1 reads BOM-less files as cp1252, so these characters
turn into mojibake on the work machine.

PostToolUse (Edit|Write): reads the hook JSON on stdin.
Sweep: skill-ascii.py FILE...
"""

import json
import re
import sys
from pathlib import Path

SUBS = [
    (re.compile(r"[ \t]*—[ \t]*"), " - "),
    (re.compile("–"), "-"),
    (re.compile("…"), "..."),
]


def in_skill(path):
    for d in path.parents:
        if (d / "SKILL.md").is_file():
            return True
        if (d / ".git").exists():
            return False
    return False


def fix(path):
    path = Path(path)
    if path.suffix != ".md" or not path.is_file() or not in_skill(path.resolve()):
        return
    text = path.read_text(encoding="utf-8")
    new = text
    for pattern, repl in SUBS:
        new = pattern.sub(repl, new)
    if new != text:
        path.write_text(new, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            fix(arg)
    else:
        fix(json.load(sys.stdin).get("tool_input", {}).get("file_path") or "")

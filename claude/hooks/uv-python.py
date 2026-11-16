#!/usr/bin/env -S python3 -I
"""PreToolUse hook (matcher: Bash): run Python through uv.

Blocks (exit 2) a Bash call when any simple command in it runs a bare
python, python3, python3.N, pip, pip3, or pip3.N, looking past leading
VAR=value assignments, shell keywords, and the wrappers below. Allows
interpreters named by a path, the words as arguments, everything when uv is
not on PATH, and fails open on anything it cannot read. Stdlib only and
Python 3.9 compatible, since it cannot assume uv or a modern Python.
"""

import json
import re
import shlex
import shutil
import sys

BARE = re.compile(r"(python|python3(\.\d+)?|pip|pip3(\.\d+)?)")
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Strip heredoc bodies so their lines are not read as commands.
HEREDOC = re.compile(
    r"(<<-?[ \t]*(['\"]?)([\w.-]+)\2[^\n]*\n).*?^[ \t]*\3[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
PUNCT = ";&|()<>\n"
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{"}
# Wrappers that run their argument as a command, with the options that take a value.
WRAPPERS = {
    "env": {"-u", "-C"},
    "time": set(),
    "nohup": set(),
    "command": set(),
    "exec": {"-a"},
    "sudo": {"-u", "-g", "-p", "-C", "-D", "-h", "-r", "-t", "-U"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a"},
}


def commands(command):
    """Yield the token list of each simple command."""
    lexer = shlex.shlex(
        HEREDOC.sub(r"\1", command), posix=True, punctuation_chars=PUNCT
    )
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    current, comment = [], False
    for token in lexer:
        if comment:
            comment = token != "\n"
        elif token and all(c in PUNCT for c in token) and token[0] not in "<>":
            yield current
            current = []
        elif token.startswith("#") and not current:
            comment = True
        else:
            current.append(token)
    yield current


def command_word(tokens):
    """Return (index, word) of the program a simple command runs."""
    i = 0
    while i < len(tokens):
        word = tokens[i]
        if ASSIGN.match(word) or word in KEYWORDS:
            i += 1
        elif word in WRAPPERS:
            takes_value = WRAPPERS[word]
            i += 1
            while i < len(tokens) and (
                tokens[i].startswith("-") or (word == "env" and ASSIGN.match(tokens[i]))
            ):
                if word == "command" and tokens[i] in ("-v", "-V"):
                    return None
                i += 2 if tokens[i] in takes_value else 1
        else:
            return i, word
    return None


def suggestion(word, args):
    if word.startswith("pip"):
        text = f"`{shlex.join(['uv', 'pip'] + args)}`"
        packages = [a for a in args[1:] if not a.startswith("-")]
        if args[:1] == ["install"] and packages:
            text += f", or `uv run --with {','.join(packages)} ...` for a one-off"
        return text
    if args and args[0].endswith(".py"):
        return f"`{shlex.join(['uv', 'run'] + args)}`"
    return f"`{shlex.join(['uv', 'run', 'python'] + args)}`"


def check(command):
    """Return None when allowed, else the block message."""
    for tokens in commands(command):
        found = command_word(tokens)
        if not found:
            continue
        i, word = found
        if BARE.fullmatch(word):
            args = tokens[i + 1 :]
            return (
                f"Blocked: bare `{word}`. Run Python through uv: "
                f"`{shlex.join([word] + args)}` -> {suggestion(word, args)}."
            )
    return None


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash" or not shutil.which("uv"):
            return
        reason = check(payload["tool_input"]["command"])
    except Exception:  # noqa: BLE001 - fail open: a broken hook must not block Bash
        return
    if reason:
        print(reason, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

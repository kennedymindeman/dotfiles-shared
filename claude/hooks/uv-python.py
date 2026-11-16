#!/usr/bin/env -S python3 -I
"""PreToolUse hook (matcher: Bash): run Python through uv.

Blocks (exit 2) a Bash call when any simple command in it runs a bare
python, python3, python3.N, pip, pip3, or pip3.N, looking past leading
VAR=value assignments, redirections, shell keywords, and the wrappers below.
Allows interpreters named by a path, the words as arguments, everything when
uv is not on PATH, and fails open on anything it cannot read or that is too
long to parse quickly. Stdlib only and Python 3.9 compatible, since it cannot
assume uv or a modern Python.

Known misses: backticks, "$(python3 ...)" inside double quotes, bash -c '...'.
"""

import json
import re
import shlex
import shutil
import sys

LIMIT = 64 * 1024  # shlex is quadratic on long tokens; allow anything longer
BARE = re.compile(r"python|python3(\.[0-9]+)?|pip|pip3(\.[0-9]+)?")
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC = re.compile(
    r"(?<!<)<<(?!<)-?[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s<>;&|()'\"]+))"
)
PUNCT = ";&|()<>\n"
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{"}
# Wrapper: (option letters that take a value, option letters that only query,
# positional arguments before the command).
WRAPPERS = {
    "env": ("uC", "", 0),
    "time": ("", "", 0),
    "nohup": ("", "", 0),
    "command": ("", "vV", 0),
    "exec": ("a", "", 0),
    "sudo": ("ugpCDhrtU", "l", 0),
    "xargs": ("InPLdsEa", "", 0),
    "nice": ("n", "", 0),
    "timeout": ("sk", "", 1),
    "gtimeout": ("sk", "", 1),
}
PYTHON_FORMS = """\
  uv run --no-project file.py
  uv run --no-project python -c ...
  uv run --no-project python -m module
  uv run --no-project --with <package> python ..."""
PIP_FORMS = """\
  uv run --no-project --with <package> ...           one-off
  uv pip install ...                                 inside a venv
  uv tool install <tool>                             a CLI
  uv run --no-project --with-requirements <file> ...  a requirements file"""
PROJECT = (
    "A project with its own environment: name its interpreter by path "
    "(`.venv/bin/python`) or use its runner (`poetry run`, `tox`)."
)


def strip_heredocs(command):
    """Drop heredoc bodies and their end lines, scanning each line once."""
    kept, ends = [], []
    for line in command.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        kept.append(line)
        ends = [a or b or c for a, b, c in HEREDOC.findall(line)]
    return "\n".join(kept)


def strip_comments(text):
    """Drop unquoted # comments up to the end of their line."""
    out, quote, i = [], None, 0
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            out.append(text[i : i + 2])
            i += 2
            continue
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "#" and (not out or out[-1][-1] in " \t\n;&|()<>"):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        out.append(c)
        i += 1
    return "".join(out)


def commands(command):
    """Yield the token list of each simple command."""
    text = strip_comments(strip_heredocs(command).replace("\\\n", ""))
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCT)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    current = []
    for token in lexer:
        if token and all(c in PUNCT for c in token) and token[0] not in "<>":
            if token[0] in "()" and len(current) == 1:
                current = []  # a case pattern `python)` or function `python3()`
            yield current
            current = []
        else:
            current.append(token)
    yield current


def command_word(tokens):
    """Return (index, word) of the program a simple command runs."""
    i = 0
    while i < len(tokens):
        word = tokens[i]
        if word[:1] in ("<", ">") and all(c in PUNCT for c in word):
            i += 2  # redirection and its target
        elif (
            word.isdigit() and tokens[i + 1 : i + 2] and tokens[i + 1][:1] in ("<", ">")
        ):
            i += 1  # the fd number of a redirection like 2>&1
        elif ASSIGN.match(word) or word in KEYWORDS:
            i += 1
        elif word in WRAPPERS:
            takes_value, query, positionals = WRAPPERS[word]
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                flags = tokens[i][1:]
                if not flags.startswith("-") and set(flags) & set(query):
                    return None
                i += 2 if len(flags) == 1 and flags in takes_value else 1
            i += positionals
        else:
            return i, word
    return None


def check(command):
    """Return None when allowed, else the block message."""
    if len(command) > LIMIT:
        return None
    for tokens in commands(command):
        found = command_word(tokens)
        if found and BARE.fullmatch(found[1]):
            word = found[1]
            forms = PIP_FORMS if word.startswith("pip") else PYTHON_FORMS
            return f"Blocked: bare `{word}`. Run it through uv:\n{forms}\n{PROJECT}"
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

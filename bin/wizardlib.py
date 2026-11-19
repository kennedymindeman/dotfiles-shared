"""Shared helpers for setup wizards: one screen per stage, hidden secret entry,
idempotent .env upserts, and a closing summary.

The dotfiles linker installs it as ~/.config/dotfiles/wizardlib.py. Wizards
import it from there:

    sys.path.insert(0, os.path.expanduser("~/.config/dotfiles"))
    import wizardlib as w

Stdlib only. Input comes from input()/getpass(), so tests can drive a wizard by
patching builtins.input and getpass.getpass.
"""

from __future__ import annotations

import getpass
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from typing import NoReturn, Union

_TTY = sys.stdout.isatty()

StrPath = Union[str, "os.PathLike[str]"]


def _c(code: int) -> str:
    return f"\033[{code}m" if _TTY and os.environ.get("TERM") != "dumb" else ""


BOLD, DIM, RESET = _c(1), _c(2), _c(0)
BLUE, GREEN, YELLOW, RED = _c(34), _c(32), _c(33), _c(31)


def _encodes(stream: object, text: str) -> bool:
    try:
        text.encode(getattr(stream, "encoding", None) or "ascii")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


# ASCII stand-ins where the console can't encode the glyphs (cp1252 on Windows).
_GLYPHS = "▸·✓⚠↗→•✗"
if all(_encodes(s, _GLYPHS) for s in (sys.stdout, sys.stderr)):
    STAGE, SEP, CHECK, WARN, OPEN, ARROW, BULLET, CROSS = list(_GLYPHS)
else:
    STAGE, SEP, CHECK, WARN, OPEN, ARROW, BULLET, CROSS = [
        ">",
        "-",
        "ok",
        "!",
        "->",
        "->",
        "*",
        "x",
    ]


class Wizard:
    def __init__(self, title: str, total: int, intro: Iterable[str] = ()) -> None:
        self.title, self.total, self.intro = title, total, intro
        self.index = 0
        # (key, path) pairs written this run
        self.written: list[tuple[str, str]] = []
        self.todo: list[str] = []  # things left for the human

    # screens
    def clear(self) -> None:
        if _TTY:
            print("\033[2J\033[3J\033[H", end="")

    def banner(self) -> None:
        self.clear()
        print(f"\n{BOLD}{BLUE}  {self.title}{RESET}")
        print(f"{DIM}  {self.total} stages{RESET}\n")
        for line in self.intro:
            print(f"{DIM}  {line}{RESET}")
        print(f"{DIM}  Stop any time with Ctrl-C and re-run later.{RESET}")
        pause("Ready to start?")

    def stage(self, name: str) -> None:
        self.clear()
        self.index += 1
        print(
            f"\n{BOLD}{BLUE}{STAGE} Stage {self.index}/{self.total} {SEP} {name}{RESET}"
        )

    def finish(self, ok: bool = True) -> None:
        self.clear()
        mark = (
            f"{GREEN}  {CHECK} Done"
            if ok
            else f"{YELLOW}  {WARN} Finished with steps left"
        )
        print(f"\n{BOLD}{mark}{RESET}")
        for key, path in self.written:
            note(f"wrote {key} {ARROW} {path}")
        if self.todo:
            print()
            warn("still to do by hand:")
            for item in self.todo:
                note(f"  - {item}")
        print()

    def write_env(self, path: StrPath, key: str, value: str) -> None:
        write_env(path, key, value)
        self.written.append((key, str(path)))
        print(f"  {GREEN}{CHECK} wrote{RESET} {key} {ARROW} {path}")


def say(msg: str) -> None:
    print(f"  {msg}")


def step(msg: str) -> None:
    print(f"  {BLUE}{BULLET}{RESET} {msg}")


def note(msg: str) -> None:
    print(f"  {DIM}{msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}{WARN} {msg}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}{CHECK}{RESET} {msg}")


def die(msg: str, code: int = 1) -> NoReturn:
    print(f"  {RED}{CROSS} {msg}{RESET}", file=sys.stderr)
    sys.exit(code)


def show_url(url: str) -> None:
    """Print a URL for the human to open. Never launches a browser: these
    wizards often run on a headless machine over ssh."""
    print(f"  {GREEN}{OPEN} open{RESET} {url}")


def pause(msg: str = "Press Enter to continue") -> None:
    try:
        input(f"  {DIM}{msg}{RESET} ")
    except EOFError:
        die("Stopped: input closed. Re-run any time.")


def confirm(question: str) -> bool:
    try:
        reply = input(f"  {YELLOW}? {question} [y/N]{RESET} ")
    except EOFError:
        return False
    return reply.strip().lower().startswith("y")


def ask(prompt: str, current: str | None = None, required: bool = True) -> str:
    """Visible input. Enter keeps `current` when one is given."""
    hint = f" {DIM}[Enter keeps current]{RESET}" if current else ""
    while True:
        try:
            value = input(f"  {BOLD}{prompt}{RESET}{hint} ").strip()
        except EOFError:
            die("Stopped: input closed. Re-run any time.")
        if not value and current:
            return current
        if value or not required:
            return value
        warn("a value is required")


def ask_secret(prompt: str, current: str | None = None) -> str:
    """Hidden input. Enter keeps `current` when one is given."""
    hint = " [Enter keeps current]" if current else ""
    while True:
        try:
            value = getpass.getpass(f"  {prompt}{hint} ").strip()
        except EOFError:
            die("Stopped: input closed. Re-run any time.")
        if not value and current:
            return current
        if value:
            return value
        warn("a value is required")


def read_env(path: StrPath, key: str) -> str | None:
    """Current value of KEY in a dotenv file, or None. Undoes write_env's quoting."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return None
    value = None
    for line in lines:
        if line.startswith(f"{key}="):
            value = line[len(key) + 1 :]
    if value is None:
        return None
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("'\\''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def write_env(path: StrPath, key: str, value: str) -> None:
    """Upsert KEY='VALUE' in a dotenv file. Creates it (and its directory) at
    mode 600, rewrites atomically, and keeps the file mode 600. On Windows mode
    bits don't apply; privacy comes from the user profile's ACLs."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"bad env key {key!r}")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{key} value contains a newline")
    # python-dotenv can't read a shell-escaped quote inside '...', so refuse it.
    if "'" in value:
        raise ValueError(f"{key} value contains a single quote")
    # realpath: write through a symlinked env file instead of replacing the link.
    path = os.path.realpath(os.path.expanduser(str(path)))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}='{value}'")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def write_secret_file(path: StrPath, data: str | bytes, mode: int = 0o600) -> None:
    """Write bytes or text to a file created private from the start. On Windows
    mode bits don't apply; privacy comes from the user profile's ACLs."""
    path = os.path.expanduser(str(path))
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise

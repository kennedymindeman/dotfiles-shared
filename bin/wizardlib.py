"""Shared helpers for setup wizards: one screen per stage, hidden secret entry,
idempotent .env upserts, and a closing summary.

The dotfiles linker installs it as ~/.config/dotfiles/wizardlib.py. Wizards
import it from there:

    sys.path.insert(0, os.path.expanduser("~/.config/dotfiles"))
    import wizardlib as w

Stdlib only. Input comes from input()/getpass(), so tests can drive a wizard by
patching builtins.input and getpass.getpass.
"""

import getpass
import os
import re
import sys
import tempfile

_TTY = sys.stdout.isatty()


def _c(code):
    return f"\033[{code}m" if _TTY and os.environ.get("TERM") != "dumb" else ""


BOLD, DIM, RESET = _c(1), _c(2), _c(0)
BLUE, GREEN, YELLOW, RED = _c(34), _c(32), _c(33), _c(31)


class Wizard:
    def __init__(self, title, total, intro=()):
        self.title, self.total, self.intro = title, total, intro
        self.index = 0
        self.written = []  # (key, path) pairs written this run
        self.todo = []  # things left for the human

    # screens
    def clear(self):
        if _TTY:
            print("\033[2J\033[3J\033[H", end="")

    def banner(self):
        self.clear()
        print(f"\n{BOLD}{BLUE}  {self.title}{RESET}")
        print(f"{DIM}  {self.total} stages{RESET}\n")
        for line in self.intro:
            print(f"{DIM}  {line}{RESET}")
        print(f"{DIM}  Stop any time with Ctrl-C and re-run later.{RESET}")
        pause("Ready to start?")

    def stage(self, name):
        self.clear()
        self.index += 1
        print(f"\n{BOLD}{BLUE}▸ Stage {self.index}/{self.total} · {name}{RESET}")

    def finish(self, ok=True):
        self.clear()
        mark = f"{GREEN}  ✓ Done" if ok else f"{YELLOW}  ⚠ Finished with steps left"
        print(f"\n{BOLD}{mark}{RESET}")
        for key, path in self.written:
            note(f"wrote {key} → {path}")
        if self.todo:
            print()
            warn("still to do by hand:")
            for item in self.todo:
                note(f"  - {item}")
        print()

    def write_env(self, path, key, value):
        write_env(path, key, value)
        self.written.append((key, str(path)))
        print(f"  {GREEN}✓ wrote{RESET} {key} → {path}")


def say(msg):
    print(f"  {msg}")


def step(msg):
    print(f"  {BLUE}•{RESET} {msg}")


def note(msg):
    print(f"  {DIM}{msg}{RESET}")


def warn(msg):
    print(f"  {YELLOW}⚠ {msg}{RESET}")


def ok(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def die(msg, code=1):
    print(f"  {RED}✗ {msg}{RESET}", file=sys.stderr)
    sys.exit(code)


def show_url(url):
    """Print a URL for the human to open. Never launches a browser: these
    wizards often run on a headless machine over ssh."""
    print(f"  {GREEN}↗ open{RESET} {url}")


def pause(msg="Press Enter to continue"):
    try:
        input(f"  {DIM}{msg}{RESET} ")
    except EOFError:
        die("Stopped: input closed. Re-run any time.")


def confirm(question):
    try:
        reply = input(f"  {YELLOW}? {question} [y/N]{RESET} ")
    except EOFError:
        return False
    return reply.strip().lower().startswith("y")


def ask(prompt, current=None, required=True):
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


def ask_secret(prompt, current=None):
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


def read_env(path, key):
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


def write_env(path, key, value):
    """Upsert KEY='VALUE' in a dotenv file. Creates it (and its directory) at
    mode 600, rewrites atomically, and keeps the file mode 600."""
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


def write_secret_file(path, data, mode=0o600):
    """Write bytes or text to a file created private from the start."""
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

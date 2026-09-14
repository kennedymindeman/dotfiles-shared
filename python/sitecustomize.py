"""Drop into a post-mortem debugger on an unhandled exception (DEBUG_ON_FAIL=1).

Reached as `sitecustomize`: CPython's site.py imports that module name at
interpreter startup, so putting this directory on PYTHONPATH covers every
interpreter the shell launches — including the per-project venv `uv run`
creates, which is why the shim is not installed into any one site-packages.
zshrc adds the directory when DEBUG_ON_FAIL=1; the check below repeats at
import time, so a leftover PYTHONPATH entry (an inherited env, a container
image) is inert without the flag.
"""

import os
import sys


def _post_mortem(exc_type, exc, tb):
    sys.__excepthook__(exc_type, exc, tb)
    # Ctrl-C and sys.exit() are control flow, not failures.
    if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
        return
    # A debugger needs a terminal on both ends. Without one (CI, a pipe,
    # </dev/null) the prompt reads EOF immediately, so the only effect would be
    # a confusing second traceback — leave the plain traceback as the answer.
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        return
    # Stdlib pdb, not the pudb that PYTHONBREAKPOINT points at: this has to work
    # unchanged in any uv venv or container image, and only pdb is always there.
    import pdb  # noqa: T100 — a debugger import is the point of this module

    pdb.post_mortem(tb)


if os.environ.get("DEBUG_ON_FAIL") == "1":
    sys.excepthook = _post_mortem

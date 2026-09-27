#!/usr/bin/env python3
"""Global PostToolUse hook: advisory diff coverage for the commit just made.

Reads the hook payload on stdin and acts only on Bash commands containing
"git commit". Opts in automatically when the repo has added .py lines in
the commit and a .venv (in the main checkout or worktree) with coverage
installed; otherwise stays silent. Runs pytest under coverage, intersects
the commit's added lines with the coverage data, and reports covered/added
executable lines back to the agent via additionalContext. Never blocks:
always exits 0.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
HUNK_RE = re.compile(r"^@@ .* \+(\d+)(?:,(\d+))? @@")


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=False, **kwargs)


def is_measurable(path):
    # skip tests and anything in hidden dirs (.claude/, .venv/, ...)
    parts = path.split("/")
    name = parts[-1]
    if any(p.startswith(".") for p in parts):
        return False
    if "tests" in parts[:-1]:
        return False
    return not (name.startswith("test_") or name == "conftest.py")


def added_lines(root):
    base = run("git", "rev-parse", "--verify", "-q", "HEAD~1", cwd=root).stdout.strip()
    diff = run(
        "git", "diff", "-U0", base or EMPTY_TREE, "HEAD", "--", "*.py", cwd=root
    ).stdout
    added, path = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            candidate = line[6:]
            path = candidate if is_measurable(candidate) else None
        elif path and (m := HUNK_RE.match(line)):
            start, count = int(m.group(1)), int(m.group(2) or "1")
            added.setdefault(path, set()).update(range(start, start + count))
    return added


def find_python(root):
    """Venv python for this repo: worktree .venv, else main checkout .venv."""
    common = run(
        "git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root
    ).stdout.strip()
    for base in (root, os.path.dirname(common)):
        python = os.path.join(base, ".venv", "bin", "python")
        if os.path.exists(python):
            return python
    return None


def measure(root):
    """Run pytest under coverage; return (files dict or None, pytest ok)."""
    python = find_python(root)
    if python is None:
        return None, True
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "COVERAGE_FILE": os.path.join(tmp, ".coverage")}
        test = run(
            python,
            "-m",
            "coverage",
            "run",
            "--source=.",
            "-m",
            "pytest",
            "-q",
            cwd=root,
            env=env,
        )
        report = run(python, "-m", "coverage", "json", "-o", "-", cwd=root, env=env)
    if report.returncode != 0:
        return None, test.returncode == 0
    return json.loads(report.stdout)["files"], test.returncode == 0


def ranges(lines):
    out, sorted_lines = [], sorted(lines)
    for n in sorted_lines:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def report(root):
    added = added_lines(root)
    if not added:
        return None
    files, tests_ok = measure(root)
    if files is None:
        return None
    covered = executable = 0
    uncovered = []
    for path, lines in sorted(added.items()):
        cov = files.get(path)
        if cov is None:
            continue
        known = set(cov["executed_lines"]) | set(cov["missing_lines"])
        hit = lines & set(cov["executed_lines"])
        miss = (lines & known) - hit
        executable += len(lines & known)
        covered += len(hit)
        if miss:
            uncovered.append(f"{path}:{ranges(miss)}")
    if not executable:
        return None
    sha = run("git", "rev-parse", "--short", "HEAD", cwd=root).stdout.strip()
    pct = 100 * covered // executable
    msg = (
        f"Diff coverage for commit {sha}: {covered}/{executable} added "
        f"executable lines covered ({pct}%), test files excluded."
    )
    if uncovered:
        msg += " Uncovered: " + "; ".join(uncovered) + "."
    if not tests_ok:
        msg += " Note: pytest exited nonzero during measurement."
    return msg + " This is advisory only, not a gate."


def main():
    payload = json.load(sys.stdin)
    command = (payload.get("tool_input") or {}).get("command", "")
    if "git commit" not in command:
        return
    root = run(
        "git", "rev-parse", "--show-toplevel", cwd=payload.get("cwd") or None
    ).stdout.strip()
    if not root:
        return
    msg = report(root)
    if msg:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": msg,
                    },
                    "systemMessage": msg,
                    "suppressOutput": True,
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001, S110 - a hook must never block
        pass

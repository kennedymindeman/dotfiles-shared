import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude/hooks/uv-python.py"


@unittest.skipIf(os.name == "nt", "needs a POSIX PATH")
class UvPythonHookTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base)
        self.with_uv = base / "with-uv"
        self.with_uv.mkdir()
        uv = self.with_uv / "uv"
        uv.write_text("#!/bin/sh\n")
        uv.chmod(0o755)
        self.without_uv = base / "without-uv"
        self.without_uv.mkdir()

    def run_hook(self, payload, path=None):
        return subprocess.run(
            [sys.executable, "-I", str(HOOK)],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            env={"PATH": str(path or self.with_uv)},
            capture_output=True,
            check=False,
            text=True,
        )

    def bash(self, command, path=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        return self.run_hook(payload, path)

    def assertAllowed(self, command, path=None):
        result = self.bash(command, path)
        self.assertEqual(result.returncode, 0, f"{command!r}: {result.stderr}")

    def assertBlocked(self, command, word):
        result = self.bash(command)
        self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
        self.assertIn(f"bare `{word}`", result.stderr)
        return result.stderr

    def test_blocks_bare_interpreters(self):
        for command, word in [
            ("python3 -B -m unittest discover -s tests -p 'test_*.py'", "python3"),
            ("python x.py", "python"),
            ("python3.12 -c 'print(1)'", "python3.12"),
            ("pip install requests", "pip"),
            ("pip3 list", "pip3"),
            ("pip3.11 freeze", "pip3.11"),
            ("python3", "python3"),
        ]:
            with self.subTest(command=command):
                self.assertBlocked(command, word)

    def test_blocks_in_pipelines_and_lists(self):
        for command in [
            "cat data.json | python3 -m json.tool",
            "cd src && python3 x.py",
            "false || python3 x.py",
            "ls; python3 x.py",
            "ls\npython3 x.py",
            "(cd src && pip list)",
            "echo $(python3 -c 'print(1)')",
            "for f in *.py; do python3 $f; done",
            "if python3 -c 1; then echo ok; fi",
            "python3 - <<'EOF'\nprint(1)\nEOF",
            "cat <<EOF | python3\nprint(1)\nEOF",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(command, "pip" if "pip" in command else "python3")

    def test_blocks_after_assignments_and_wrappers(self):
        for command in [
            "PYTHONPATH=src python3 x.py",
            "A=1 B='two words' python3 x.py",
            "env python3 x.py",
            "env -i PATH=/bin FOO=1 python3 x.py",
            "env -u HOME python3 x.py",
            "time python3 x.py",
            "nohup python3 server.py",
            "command python3 x.py",
            "exec python3 x.py",
            "sudo python3 x.py",
            "sudo -u nobody -E python3 x.py",
            "find . -name '*.py' | xargs python3 -m py_compile",
            "ls | xargs -n 1 -P 4 python3 x.py",
            "time env A=1 nohup python3 x.py",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(command, "python3")

    def test_allows_uv(self):
        for command in [
            "uv run python -m unittest",
            "uv run python3 x.py",
            "uv run x.py",
            "uvx ruff check .",
            "uv pip install requests",
            "uv tool install ruff",
            "uv run --with requests python -c 'import requests'",
            "cd src && uv run python x.py | grep ok",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_allows_paths(self):
        for command in [
            "/usr/bin/python3 x.py",
            ".venv/bin/python -m pytest",
            "./venv/bin/pip install x",
            "env -i /usr/bin/python3 -I x.py",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_allows_words_as_arguments(self):
        for command in [
            "grep python3 file",
            'echo "pip install x"',
            "echo 'python3 x.py; pip list'",
            "which python3",
            "command -v python3",
            "command -V pip",
            "brew upgrade python3",
            "git commit -m 'run python3 tests'",
            "cat > notes.txt <<'EOF'\npip install x\npython3 y.py\nEOF",
            "# python3 x.py\nls",
            "ls > python3",
        ]:
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_allows_everything_without_uv(self):
        for command in ["python3 x.py", "pip install x", "ls | python3 -m json.tool"]:
            with self.subTest(command=command):
                self.assertAllowed(command, path=self.without_uv)

    def test_ignores_other_tools(self):
        for tool in ["Read", "Write", "Edit", "Grep"]:
            payload = {"tool_name": tool, "tool_input": {"command": "python3 x.py"}}
            with self.subTest(tool=tool):
                self.assertEqual(self.run_hook(payload).returncode, 0)

    def test_fails_open_on_bad_input(self):
        for payload in [
            "not json",
            "",
            "[]",
            {"tool_name": "Bash"},
            {"tool_name": "Bash", "tool_input": {"command": 3}},
            {"tool_name": "Bash", "tool_input": {"command": "python3 'unclosed"}},
        ]:
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook(payload).returncode, 0)

    def test_message_shows_uv_form(self):
        for command, expected in [
            ("python3 -m unittest", "`uv run python -m unittest`"),
            ("python3 x.py", "`uv run x.py`"),
            ("pip install x", "`uv pip install x`"),
            ("pip install x", "`uv run --with x ...`"),
            ("python3", "`uv run python`"),
        ]:
            with self.subTest(command=command, expected=expected):
                stderr = self.assertBlocked(command, command.split()[0])
                self.assertIn(expected, stderr)


if __name__ == "__main__":
    unittest.main()

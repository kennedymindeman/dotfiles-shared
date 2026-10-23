import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude/hooks/review-scratch-only.sh"
AGENT = ROOT / "claude/agents/reviewer.md"


@unittest.skipIf(os.name == "nt" or not shutil.which("jq"), "needs sh and jq")
class ReviewScratchHookTests(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base)
        self.real_tmp = Path(base, "tmp")
        self.real_tmp.mkdir()
        # TMPDIR is a symlink with a trailing slash, like macOS /var -> /private/var.
        Path(base, "link").symlink_to(self.real_tmp)
        self.tmpdir = f"{base}/link/"
        self.repo = Path(base, "repo")
        self.repo.mkdir()
        self.scratch = Path(
            tempfile.mkdtemp(prefix="claude-review-", dir=self.real_tmp)
        )
        self.linked_scratch = f"{self.tmpdir}{self.scratch.name}"

    def run_hook(self, payload, path_env=None):
        env = {**os.environ, "TMPDIR": self.tmpdir}
        if path_env is not None:
            env["PATH"] = path_env
        return subprocess.run(
            [str(HOOK)],
            input=json.dumps(payload),
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def write(self, path):
        return self.run_hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(path)}}
        )

    def assertAllowed(self, path):
        result = self.write(path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def assertBlocked(self, path):
        result = self.write(path)
        self.assertEqual(result.returncode, 2, f"{path} was allowed")
        self.assertIn("Blocked", result.stderr)

    def test_allows_scratch_paths_in_every_spelling(self):
        self.assertAllowed(self.scratch / "test_probe.py")
        self.assertAllowed(f"{self.linked_scratch}/test_probe.py")
        self.assertAllowed(f"{self.tmpdir}/{self.scratch.name}//test_probe.py")
        self.assertAllowed(self.scratch / "probe..py")
        self.assertAllowed(self.scratch / "new/nested/test_probe.py")

    def test_blocks_paths_outside_scratch(self):
        self.assertBlocked(self.repo / "app.py")
        self.assertBlocked(self.real_tmp / "app.py")
        self.assertBlocked("relative/claude-review-x/a.py")
        self.assertBlocked(self.scratch / ".." / "app.py")
        self.assertBlocked(self.scratch)

    def test_blocks_symlinks_into_repo(self):
        (self.scratch / "repo").symlink_to(self.repo)
        self.assertBlocked(self.scratch / "repo/app.py")
        (self.scratch / "file.py").symlink_to(self.repo / "app.py")
        self.assertBlocked(self.scratch / "file.py")
        planted = self.real_tmp / "claude-review-planted"
        planted.symlink_to(self.repo)
        self.assertBlocked(planted / "app.py")

    def test_blocks_scratch_dirs_it_did_not_make(self):
        loose = self.real_tmp / "claude-review-loose"
        loose.mkdir(mode=0o755)
        loose.chmod(0o755)
        self.assertBlocked(loose / "a.py")
        self.assertBlocked(self.real_tmp / "review-old/a.py")

    def test_blocks_missing_file_path(self):
        result = self.run_hook({"tool_name": "Write", "tool_input": {}})
        self.assertEqual(result.returncode, 2)

    def test_blocks_without_jq(self):
        bin_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, bin_dir)
        for tool in ("dirname", "find", "id"):
            (bin_dir / tool).symlink_to(shutil.which(tool))
        result = self.run_hook(
            {"tool_input": {"file_path": str(self.scratch / "a.py")}},
            path_env=str(bin_dir),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("jq not found", result.stderr)

    def test_agent_hook_blocks_when_script_missing(self):
        command = re.search(r"command: '(.*)'", AGENT.read_text()).group(1)
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home)
        result = subprocess.run(
            ["sh", "-c", command],
            input="{}",
            env={**os.environ, "HOME": home},
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()

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
        self.home = Path(base, "home")
        self.findings = self.home / ".cache/review-findings"

    def run_hook(self, payload, path_env=None, home_env=None):
        env = {**os.environ, "TMPDIR": self.tmpdir, "HOME": home_env or str(self.home)}
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

    def make_findings(self, mode=0o700):
        self.findings.mkdir(parents=True)
        self.findings.chmod(mode)

    def test_blocks_hardlinks_in_scratch(self):
        target = self.repo / "app.py"
        target.write_text("x\n")
        os.link(target, self.scratch / "linked.py")
        self.assertBlocked(self.scratch / "linked.py")

    def test_allows_a_findings_file_per_lane(self):
        self.assertBlocked(self.findings / "repo-pr3-abc1234-blind.md")
        self.make_findings()
        self.assertAllowed(self.findings / "repo-pr3-abc1234-blind.md")
        self.assertAllowed(self.findings / "repo-pr3-abc1234-red-team.md")
        (self.findings / "repo-pr3-abc1234-blind.md").write_text("findings\n")
        self.assertAllowed(self.findings / "repo-pr3-abc1234-blind.md")
        self.assertAllowed(f"{self.home}/.cache//review-findings/repo-pr3-abc1234-blind.md")

    def test_blocks_findings_dir_not_mode_700(self):
        self.make_findings(0o777)
        self.assertBlocked(self.findings / "repo-pr3-abc1234-blind.md")

    def test_blocks_hardlinks_in_findings(self):
        self.make_findings()
        target = self.repo / "README.md"
        target.write_text("x\n")
        os.link(target, self.findings / "repo-pr3-abc1234-blind.md")
        self.assertBlocked(self.findings / "repo-pr3-abc1234-blind.md")

    def test_blocks_a_symlinked_cache_parent(self):
        elsewhere = self.repo / "cache"
        (elsewhere / "review-findings").mkdir(parents=True, mode=0o700)
        (elsewhere / "review-findings").chmod(0o700)
        self.home.mkdir()
        (self.home / ".cache").symlink_to(elsewhere)
        self.assertBlocked(self.findings / "repo-pr3-abc1234-blind.md")

    def test_blocks_findings_under_a_relative_home(self):
        self.make_findings()
        result = self.run_hook(
            {"tool_input": {"file_path": str(self.findings / "a-pr1-abc1234-blind.md")}},
            home_env=os.path.relpath(self.home),
        )
        self.assertEqual(result.returncode, 2, "relative HOME was allowed")

    def test_blocks_other_writes_near_findings(self):
        self.make_findings()
        self.assertBlocked(self.findings / "probe.py")
        self.assertBlocked(self.findings / "sub/repo-pr3-abc1234.md")
        self.assertBlocked(self.findings / ".." / "repo-pr3-abc1234.md")
        self.assertBlocked(self.home / ".cache/repo-pr3-abc1234.md")
        (self.findings / "linked.md").symlink_to(self.repo / "README.md")
        self.assertBlocked(self.findings / "linked.md")

    def test_blocks_a_findings_dir_that_links_elsewhere(self):
        self.findings.parent.mkdir(parents=True)
        self.repo.chmod(0o700)
        self.findings.symlink_to(self.repo)
        self.assertBlocked(self.findings / "repo-pr3-abc1234.md")
        self.assertBlocked(self.repo / "repo-pr3-abc1234.md")

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

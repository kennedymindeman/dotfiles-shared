import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude/hooks/pr-merge-gate.sh"
URL = "https://github.com/example-org/example-repo/pull/7"

# Stands in for gh: logs each call, answers `pr view` with the fixture PR and
# `api graphql` with the fixture unresolved threads (one line per thread).
STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_STUB_LOG"
case "$1 $2" in
  "pr view") printf '%s %s\\n' "$GH_STUB_URL" "$GH_STUB_BASE" ;;
  "api graphql") printf '%s' "$GH_STUB_THREADS" ;;
esac
"""


@unittest.skipIf(os.name == "nt" or not shutil.which("jq"), "needs sh and jq")
class PrMergeGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        gh = self.dir / "gh"
        gh.write_text(STUB)
        gh.chmod(0o755)
        self.log = self.dir / "gh.log"

    def run_hook(self, command, base="main", protected=None, threads=""):
        env = {
            **os.environ,
            "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_LOG": str(self.log),
            "GH_STUB_URL": URL,
            "GH_STUB_BASE": base,
            "GH_STUB_THREADS": threads,
        }
        env.pop("PR_MERGE_GATE_PROTECTED_BASES", None)
        if protected is not None:
            env["PR_MERGE_GATE_PROTECTED_BASES"] = protected
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        return subprocess.run(
            [str(HOOK)],
            input=json.dumps(payload),
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def gh_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_refuses_merge_into_protected_base(self):
        result = self.run_hook("gh pr merge 7 --squash", protected="main")
        self.assertEqual(result.returncode, 2)
        self.assertIn("protected base branch", result.stderr)
        self.assertIn(URL, result.stderr)
        self.assertIn("gh pr merge 7 -R example-org/example-repo", result.stderr)
        self.assertEqual(len(self.gh_calls()), 1, "no thread query after refusal")

    def test_allows_merge_into_unprotected_base(self):
        result = self.run_hook(
            "gh pr merge 7 --merge", base="run/2026-01-01", protected="main"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_protects_nothing(self):
        self.assertEqual(self.run_hook("gh pr merge 7").returncode, 0)
        self.assertEqual(self.run_hook("gh pr merge 7", protected="").returncode, 0)

    def test_matches_whole_branch_names_from_list(self):
        self.assertEqual(
            self.run_hook(
                "gh pr merge 7", base="release", protected="main release"
            ).returncode,
            2,
        )
        self.assertEqual(
            self.run_hook(
                "gh pr merge 7", base="main-old", protected="main"
            ).returncode,
            0,
        )

    def test_passes_pr_and_repo_to_gh(self):
        self.run_hook("gh pr merge 7 -R example-org/example-repo", protected="main")
        self.assertTrue(
            self.gh_calls()[0].startswith("pr view 7 -R example-org/example-repo")
        )

    def test_still_blocks_unresolved_threads(self):
        result = self.run_hook(
            "gh pr merge 7", base="dev", protected="main", threads="T1\tf.py:3"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved review threads", result.stderr)

    def test_ignores_other_commands(self):
        self.assertEqual(self.run_hook("gh pr view 7", protected="main").returncode, 0)
        self.assertEqual(self.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()

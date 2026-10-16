"""pr-merge-gate.sh frees the head branch's worktree before --delete-branch."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "claude/hooks/pr-merge-gate.sh"
FAKE_GH = """#!/bin/sh
case "$*" in
  "pr view"*) echo "https://github.com/o/r/pull/1 feature" ;;
  "api graphql"*) [ -n "${FAKE_THREAD:-}" ] && echo "$FAKE_THREAD" ;;
esac
exit 0
"""


@unittest.skipUnless(
    shutil.which("sh") and shutil.which("jq") and shutil.which("git"),
    "needs sh, jq, and git",
)
class WorktreeCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        self.main = root / "main"
        self.wt = root / "wt feature"
        self.git(root, "init", "-q", "--bare", "remote.git")
        self.git(root, "clone", "-q", "remote.git", "main")
        for key, value in [("user.email", "t@t"), ("user.name", "t")]:
            self.git(self.main, "config", key, value)
        self.git(self.main, "commit", "-q", "--allow-empty", "-m", "base")
        self.git(self.main, "push", "-q", "origin", "HEAD")
        self.git(self.main, "branch", "feature")
        self.git(self.main, "push", "-q", "origin", "feature")
        self.git(self.main, "worktree", "add", "-q", str(self.wt), "feature")

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
        ).stdout

    def hook(self, command="gh pr merge 1 --squash --delete-branch", cwd=None):
        payload = {"tool_input": {"command": command}, "cwd": str(cwd or self.main)}
        return subprocess.run(
            ["sh", str(HOOK)],
            check=False,
            input=json.dumps(payload),
            env=self.env,
            capture_output=True,
            text=True,
        )

    def test_clean_worktree_is_removed_and_merge_allowed(self):
        result = self.hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.wt.exists())
        self.assertNotIn(str(self.wt), self.git(self.main, "worktree", "list"))
        self.assertTrue(self.main.exists())

    def test_uncommitted_change_blocks(self):
        (self.wt / "tracked").write_text("a")
        self.git(self.wt, "add", "tracked")
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn(self.wt.as_posix(), result.stderr)
        self.assertIn("uncommitted", result.stderr)
        self.assertTrue((self.wt / "tracked").exists())

    def test_untracked_file_blocks(self):
        (self.wt / "notes.txt").write_text("keep me")
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn(self.wt.as_posix(), result.stderr)
        self.assertIn("notes.txt", result.stderr)
        self.assertTrue((self.wt / "notes.txt").exists())

    def test_unpushed_commit_blocks(self):
        self.git(self.wt, "commit", "-q", "--allow-empty", "-m", "local only")
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn(self.wt.as_posix(), result.stderr)
        self.assertIn("unpushed", result.stderr)
        self.assertTrue(self.wt.exists())

    def test_ignored_file_blocks(self):
        (self.main / ".git/info/exclude").write_text(".env\n")
        (self.wt / ".env").write_text("SECRET=x")
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn(".env", result.stderr)
        self.assertTrue((self.wt / ".env").exists())

    def test_untracked_file_blocks_when_status_hides_untracked(self):
        self.git(self.main, "config", "status.showUntrackedFiles", "no")
        (self.wt / "notes.txt").write_text("keep me")
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("notes.txt", result.stderr)
        self.assertTrue((self.wt / "notes.txt").exists())

    def test_short_delete_flag_removes_worktree(self):
        result = self.hook("gh pr merge 1 --squash -d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.wt.exists())

    def test_flags_after_a_separator_are_ignored(self):
        result = self.hook("gh pr merge 1 --squash && git branch -d old")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())

    def test_option_value_is_not_parsed_as_a_flag(self):
        result = self.hook("gh pr merge 1 --squash -t -d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())

    def test_auto_merge_leaves_worktree(self):
        result = self.hook("gh pr merge 1 --squash --auto -d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())

    def test_commands_that_must_not_remove_the_worktree(self):
        for command in [
            'gh pr merge 1 --squash --body "do not use -d here"',
            "gh pr merge 1 -s -b 'see -d flag'",
            'gh pr merge 1 --squash --body="x -d y"',
            "gh pr merge 1 -s # later -d",
            "gh pr merge 1 -s\ngit branch -d old",
            "gh pr merge 1 --disable-auto -d",
            "gh pr merge 1 --auto=true -d",
            f"cd {self.main} && gh pr merge 1 -s -d",
            "GH_REPO=o/r gh pr merge 1 -s -d",
            "gh pr merge 1 -s -d --body 'unbalanced",
        ]:
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.wt.exists())

    def test_combined_short_flags_remove_worktree(self):
        result = self.hook("gh pr merge 1 -sd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.wt.exists())

    def test_backslash_continuation_removes_worktree(self):
        result = self.hook("gh pr merge 1 --squash \\\n  --delete-branch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.wt.exists())

    def test_unbalanced_quote_still_blocks_on_unresolved_thread(self):
        self.env["FAKE_THREAD"] = "T1\tf.py:1\tbot\thttps://example.invalid/c1\n    fix"
        result = self.hook("gh pr merge 1 -s -d --body 'unbalanced")
        self.assertEqual(result.returncode, 2)
        self.assertIn("T1", result.stderr)
        self.assertTrue(self.wt.exists())

    def test_prunable_worktree_is_pruned_and_merge_allowed(self):
        shutil.rmtree(self.wt)
        result = self.hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("prunable", self.git(self.main, "worktree", "list", "--porcelain"))
        self.assertNotIn(self.wt.as_posix(), self.git(self.main, "worktree", "list"))

    def test_unresolved_thread_blocks_and_leaves_worktree(self):
        self.env["FAKE_THREAD"] = "T1\tf.py:1\tbot\thttps://example.invalid/c1\n    fix"
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved review threads", result.stderr)
        self.assertIn("T1", result.stderr)
        self.assertTrue(self.wt.exists())

    def test_running_from_inside_the_worktree_blocks(self):
        result = self.hook(cwd=self.wt)
        self.assertEqual(result.returncode, 2)
        self.assertIn(self.wt.as_posix(), result.stderr)
        self.assertIn(f"rerun gh pr merge from {self.main.as_posix()}", result.stderr)
        self.assertTrue(self.wt.exists())

    def test_main_checkout_is_never_removed(self):
        self.git(self.main, "worktree", "remove", str(self.wt))
        self.git(self.main, "checkout", "-q", "feature")
        result = self.hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.main / ".git").exists())

    def test_without_delete_branch_worktree_stays(self):
        result = self.hook("gh pr merge 1 --squash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())

    def test_repo_flag_leaves_local_worktrees_alone(self):
        result = self.hook("gh pr merge 1 -R o/r --squash --delete-branch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())


if __name__ == "__main__":
    unittest.main()

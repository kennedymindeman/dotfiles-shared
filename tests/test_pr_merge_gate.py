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
  "pr view"*) echo "$*" >> "$GH_LOG"; echo "https://github.com/o/r/pull/1 feature" ;;
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
        self.bin_dir = bin_dir
        self.gh_log = root / "gh.log"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_LOG": str(self.gh_log),
        }
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

    def test_commands_without_delete_leave_the_worktree(self):
        for command in [
            'gh pr merge 1 --squash --body "do not use -d here"',
            "gh pr merge 1 -s -b 'see -d flag'",
            'gh pr merge 1 --squash --body="x -d y"',
            "gh pr merge 1 -s # later -d",
            "gh pr merge 1 -s\ngit branch -d old",
        ]:
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.wt.exists())

    def test_ambiguous_delete_blocks_with_remove_command(self):
        for command in [
            "gh pr merge 1 --squash --auto -d",
            "gh pr merge 1 --disable-auto -d",
            "gh pr merge 1 --auto=true -d",
            "gh pr merge 1 -R o/r --squash --delete-branch",
            f"cd {self.main} && gh pr merge 1 -s -d",
            f"pushd {self.main} >/dev/null; gh pr merge 1 -s -d; popd",
            "GH_REPO=o/r gh pr merge 1 -s -d",
            "GIT_DIR=x gh pr merge 1 -s -d",
            "gh pr merge 1 -s -d --body 'unbalanced",
            "gh pr merge 1 -s -d=false",
            "gh pr merge 1 -s --delete-branch=true",
            "gh pr merge --squash -d",
            "gh pr merge --squash -d 2>&1",
            "gh pr merge 2 -s -d 2>/dev/null",
            "gh pr merge 1 -s -d || true",
            "(gh pr merge 1 -s -d)",
            "gh pr merge 1 -s -d | cat",
        ]:
            with self.subTest(command=command):
                result = self.hook(command)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    f"git worktree remove '{self.wt.as_posix()}'", result.stderr
                )
                self.assertTrue(self.wt.exists())

    def test_python_failure_still_blocks_delete(self):
        (self.bin_dir / "python3").write_text("#!/bin/sh\nexit 1\n")
        (self.bin_dir / "python3").chmod(0o755)
        result = self.hook("gh pr merge 1 -s -d")
        self.assertEqual(result.returncode, 2)
        self.assertIn("git worktree remove", result.stderr)
        self.assertTrue(self.wt.exists())

    def test_unresolved_thread_check_keeps_the_pr_number(self):
        self.env["FAKE_THREAD"] = "T1\tf.py:1\tbot\thttps://example.invalid/c1\n    fix"
        stub = self.bin_dir / "python3"
        for name, command, view in [
            ("comment", "gh pr merge 5 --squash  # don't delete yet", "pr view 5 "),
            (
                "heredoc",
                "gh pr merge 5 --squash --body-file - <<'EOF'\nit's done\nEOF",
                "pr view 5 ",
            ),
            ("ansi-c", "gh pr merge 5 --squash --subject $'it\\'s done'", "pr view 5 "),
            ("redirect", "gh pr merge --squash 2>&1", "pr view --json"),
            ("python-fails", "gh pr merge 5 --squash", "pr view 5 "),
        ]:
            with self.subTest(name=name):
                if name == "python-fails":
                    stub.write_text("#!/bin/sh\nexit 1\n")
                    stub.chmod(0o755)
                self.gh_log.write_text("")
                result = self.hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("unresolved review threads", result.stderr)
                self.assertTrue(
                    self.gh_log.read_text().startswith(view), self.gh_log.read_text()
                )

    def test_explicit_pr_number_reaches_gh(self):
        self.hook("gh pr merge 7 --squash -d")
        self.assertTrue(self.gh_log.read_text().startswith("pr view 7 "))

    def test_combined_short_flags_remove_worktree(self):
        result = self.hook("gh pr merge 1 -sd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.wt.exists())

    def test_backslash_continuation_removes_worktree(self):
        # jq -r on Windows writes CRLF line endings.
        for newline in ["\n", "\r\n"]:
            with self.subTest(newline=repr(newline)):
                if not self.wt.exists():
                    self.git(
                        self.main, "worktree", "add", "-q", str(self.wt), "feature"
                    )
                result = self.hook(
                    f"gh pr merge 1 --squash \\{newline}  --delete-branch"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.wt.exists())

    def test_unbalanced_quote_still_blocks_on_unresolved_thread(self):
        self.env["FAKE_THREAD"] = "T1\tf.py:1\tbot\thttps://example.invalid/c1\n    fix"
        result = self.hook("gh pr merge 1 -s -d --body 'unbalanced")
        self.assertEqual(result.returncode, 2)
        self.assertIn("T1", result.stderr)
        self.assertTrue(self.wt.exists())

    def test_missing_worktree_dir_blocks_without_pruning(self):
        other = self.wt.parent / "wt other"
        self.git(self.main, "worktree", "add", "-q", "-b", "other", str(other))
        shutil.rmtree(other)
        shutil.rmtree(self.wt)
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("git worktree prune", result.stderr)
        listed = self.git(self.main, "worktree", "list", "--porcelain")
        self.assertIn(other.as_posix(), listed)
        self.assertIn(self.wt.as_posix(), listed)

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


if __name__ == "__main__":
    unittest.main()

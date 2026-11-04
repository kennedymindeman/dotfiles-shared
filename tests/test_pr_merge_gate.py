import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude/hooks/pr-merge-gate.sh"
URL = "https://github.com/example-org/example-repo/pull/"

# Stands in for gh. Logs "<cwd>|<GH_REPO>|<args>" per call. `pr view <N>`
# answers with base GH_STUB_BASE_<N> (else GH_STUB_BASE; FAIL exits 1), and
# `api graphql` with the fixture unresolved threads.
STUB = """#!/bin/sh
printf '%s|%s|%s\\n' "$PWD" "${GH_REPO:-}" "$*" >> "$GH_STUB_LOG"
case "$1 $2" in
  "pr view")
    sel=$3; case "$sel" in ''|-*|*[!0-9]*) sel=7 ;; esac
    eval "base=\\${GH_STUB_BASE_$sel:-\\$GH_STUB_BASE}"
    [ "$base" = FAIL ] && { echo "no pull requests found" >&2; exit 1; }
    case "$*" in
      *baseRefName*) printf '%s %s%s\\n' "$base" "$GH_STUB_URL" "$sel" ;;
      *) printf '%s%s\\n' "$GH_STUB_URL" "$sel" ;;
    esac ;;
  "api graphql") printf '%s' "$GH_STUB_THREADS" ;;
esac
"""


@unittest.skipIf(
    os.name == "nt" or not shutil.which("jq") or not shutil.which("python3"),
    "needs sh, jq, and python3",
)
class PrMergeGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.dir)
        gh = self.dir / "gh"
        gh.write_text(STUB)
        gh.chmod(0o755)
        self.log = self.dir / "gh.log"
        self.work = self.dir / "work"
        self.work.mkdir()

    def run_hook(self, command, base="main", protected=None, threads="", **bases):
        env = {
            **os.environ,
            "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_LOG": str(self.log),
            "GH_STUB_URL": URL,
            "GH_STUB_BASE": base,
            "GH_STUB_THREADS": threads,
            **{f"GH_STUB_BASE_{n.lstrip('_')}": b for n, b in bases.items()},
        }
        for key in ("PR_MERGE_GATE_PROTECTED_BASES", "GH_REPO"):
            env.pop(key, None)
        if protected is not None:
            env["PR_MERGE_GATE_PROTECTED_BASES"] = protected
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(self.dir),
        }
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

    def view_calls(self):
        return [c for c in self.gh_calls() if "|pr view" in c]

    def assertRefused(self, command, text="protected base branch", **kwargs):
        result = self.run_hook(command, protected="main", **kwargs)
        self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
        self.assertIn(text, result.stderr)
        return result

    def assertAllowed(self, command, **kwargs):
        kwargs.setdefault("protected", "main")
        result = self.run_hook(command, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)

    # Protected bases.

    def test_refuses_merge_into_protected_base(self):
        result = self.assertRefused("gh pr merge 7 --squash")
        self.assertIn(URL + "7", result.stderr)
        self.assertIn("gh pr merge 7 -R example-org/example-repo", result.stderr)
        self.assertFalse(any("graphql" in c for c in self.gh_calls()))

    def test_allows_merge_into_unprotected_base(self):
        self.assertAllowed("gh pr merge 7 --merge", base="run/2026-01-01")

    def test_matches_whole_branch_names_from_list(self):
        result = self.run_hook(
            "gh pr merge 7", base="release", protected="main release"
        )
        self.assertEqual(result.returncode, 2)
        self.assertAllowed("gh pr merge 7", base="main-old")

    def test_passes_pr_and_repo_to_gh(self):
        self.assertRefused("gh pr merge 7 -R example-org/example-repo")
        self.assertIn("pr view 7 -R example-org/example-repo", self.view_calls()[0])

    # Base can't be determined: refused only when a list is set.

    def test_refuses_when_lookup_fails(self):
        self.assertRefused("gh pr merge 7", "can't tell which branch", base="FAIL")

    def test_refuses_unparseable_command(self):
        self.assertRefused("gh pr merge 7 --subject 'oops", "unparseable")

    def test_refuses_flag_missing_its_value(self):
        self.assertRefused("gh pr merge --subject", "has no value", base="dev")

    # Selector and context parsing.

    def test_value_flags_before_selector(self):
        cases = [
            'gh pr merge --subject "Ship it now" 8',
            "gh pr merge --body 'two words' 8",
            "gh pr merge --match-head-commit abc123 8",
            "gh pr merge -t title 8",
            "gh pr merge -b body 8",
            "gh pr merge -F body.md --squash 8",
            "gh pr merge --subject=title 8",
        ]
        for command in cases:
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                self.assertRefused(command, base="dev", _8="main")
                self.assertIn("|pr view 8 ", self.view_calls()[0])

    def test_cd_prefix_runs_lookup_in_that_dir(self):
        self.assertRefused(f"cd {self.work} && gh pr merge 7")
        self.assertTrue(self.view_calls()[0].startswith(f"{self.work}|"))

    def test_relative_cd_prefix(self):
        self.assertRefused("cd work && gh pr merge 7")
        self.assertTrue(self.view_calls()[0].startswith(f"{self.work}|"))

    def test_gh_repo_assignment(self):
        self.assertRefused("GH_REPO=example-org/example-repo gh pr merge 7")
        self.assertIn("|example-org/example-repo|pr view 7", self.view_calls()[0])

    def test_exported_gh_repo(self):
        self.assertRefused("export GH_REPO=example-org/other; gh pr merge 7")
        self.assertIn("|example-org/other|pr view 7", self.view_calls()[0])

    def test_checks_every_merge_in_compound_command(self):
        for command in (
            "gh pr merge 7 && gh pr merge 8",
            "gh pr merge 7; gh pr merge 8",
            "gh pr merge 7\ngh pr merge 8",
            "gh pr merge 7 2>&1 | tail -1; gh pr merge 8",
        ):
            with self.subTest(command=command):
                self.assertRefused(command, base="dev", _7="main", _8="dev")

    def test_shell_c_string_is_checked(self):
        self.assertRefused("bash -lc 'cd work && gh pr merge 7'")

    def test_quoted_mention_is_not_a_merge(self):
        self.assertAllowed("grep -n 'gh pr merge' notes.md")
        self.assertFalse(any("baseRefName" in c for c in self.gh_calls()))

    def test_redirection_is_not_a_selector(self):
        self.assertAllowed("gh pr merge 8 --merge > out.txt 2>&1", base="dev")

    # Unchanged behaviour.

    def test_default_protects_nothing(self):
        self.assertAllowed("gh pr merge 7", protected=None)
        self.assertAllowed("gh pr merge 7", protected="")
        self.assertAllowed("gh pr merge 7", protected=None, base="FAIL")

    def test_default_makes_the_same_gh_calls_as_before(self):
        self.assertAllowed("gh pr merge 7 -R o/r", protected=None)
        calls = [c.split("|", 2)[2] for c in self.gh_calls() if c.startswith(f"{self.dir}|")]
        self.assertEqual(calls[0], "pr view 7 -R o/r --json url -q .url")
        self.assertTrue(calls[1].startswith("api graphql"))
        self.assertEqual(len(calls), 2)

    def test_still_blocks_unresolved_threads(self):
        for protected in (None, "main"):
            with self.subTest(protected=protected):
                result = self.run_hook(
                    "gh pr merge 7",
                    base="dev",
                    protected=protected,
                    threads="T1\tf.py:3",
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("unresolved review threads", result.stderr)

    def test_ignores_other_commands(self):
        self.assertAllowed("gh pr view 7")
        self.assertEqual(self.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()

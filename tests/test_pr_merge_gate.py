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
REPO = "example-org/example-repo"

# Stands in for gh. Logs "<cwd>|<args>" per call. `pr view <N>` answers with
# base GH_STUB_BASE_<N> (else GH_STUB_BASE; FAIL exits 1), and `api graphql`
# with the fixture unresolved threads.
STUB = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$GH_STUB_LOG"
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

# Each of these must be refused while a protected list is set. The PR they
# name has an unprotected base, so only the form decides.
BYPASSES = {
    "hash mid-word": f"gh pr merge 8 -R {REPO} --subject a#b; gh pr merge 7 -R {REPO}",
    "double space, no repo": "gh  pr merge 7",
    "tabs, no repo": "gh\tpr\tmerge 7",
    "quoted gh, no repo": '"gh" pr merge 7',
    "repo before merge": f"gh pr -R {REPO} merge 7",
    "attached repo flag": f"gh pr merge 7 -R{REPO}",
    "attached repo flag first": f"gh pr merge -R{REPO} 7",
    "clustered short flags": f"gh pr merge -sb 7 -R {REPO}",
    "backslash-newline": f"gh pr merge 8 -R {REPO} \\\n  && gh pr merge 7 -R {REPO}",
    "checkout then merge": "gh pr checkout 7 && gh pr merge",
    "xargs": f"echo 7 | xargs gh pr merge -R {REPO}",
    "subshell cd": f"(cd /tmp) && gh pr merge 7 -R {REPO}",
    "pushd": f"pushd /tmp && gh pr merge 7 -R {REPO}",
    "backticks": f"gh pr merge `echo 7` -R {REPO}",
    "command substitution": f"gh pr merge $(echo 7) -R {REPO}",
    "here-string": f"gh pr merge 7 -R {REPO} <<< y",
    "pipe to sh": f"echo 'gh pr merge 7 -R {REPO}' | sh",
    "sh -c": f"bash -lc 'gh pr merge 7 -R {REPO}'",
    "compound after canonical": f"gh pr merge 8 -R {REPO} && gh pr merge 7 -R {REPO}",
    "background": f"gh pr merge 7 -R {REPO} &",
    "redirect": f"gh pr merge 8 -R {REPO} > out.txt",
    "api merge": f"gh api -X PUT repos/{REPO}/pulls/7/merge",
    "unlisted flag": f"gh pr merge 8 -R {REPO} --admin",
    "flag with = value": f"gh pr merge 8 -R {REPO} --subject=x",
    "extra argument": f"gh pr merge 8 -R {REPO} 7",
    "url selector": f"gh pr merge {URL}7",
    "env prefix": f"GH_REPO={REPO} gh pr merge 7",
    "unbalanced quote": f"gh pr merge 8 -R {REPO} --subject 'oops",
    "subject missing value": f"gh pr merge 8 -R {REPO} --subject",
    "two cds": f"cd /tmp && cd / && gh pr merge 8 -R {REPO}",
    "heredoc with apostrophe": "cat <<'EOF'\nit's gh's merge text\nEOF",
    "git merge next to gh": "git merge origin/main && gh pr checks 8",
}

CANONICAL = [
    f"gh pr merge 8 -R {REPO}",
    f"gh pr merge 8 -R {REPO} --merge --delete-branch",
    f"gh pr merge 8 -R {REPO} --squash --auto --subject 'Ship it, #12 now' --body \"a b\"",
    f"cd /tmp && gh pr merge 8 -R {REPO} --rebase",
    f"cd 'a dir' && gh pr merge 8 -R {REPO} --subject '#1 fix'",
    f"gh  pr\tmerge 8 -R {REPO}",
]


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
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        return [c for c in lines if c.startswith(("/|", f"{self.dir}|"))]

    def base_lookups(self):
        return [c for c in self.gh_calls() if "baseRefName" in c]

    # Protected list set: the form gate.

    def test_every_bypass_is_refused(self):
        for name, command in BYPASSES.items():
            with self.subTest(name):
                result = self.run_hook(command, base="dev", protected="main")
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
                self.assertIn("one merge form the gate checks", result.stderr)
                self.assertIn("rephrase it", result.stderr)
        self.assertEqual(self.base_lookups(), [], "refused forms never reach gh")

    def test_canonical_forms_into_unprotected_base_are_allowed(self):
        for command in CANONICAL:
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(command, base="dev", protected="main")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.base_lookups()[0],
                    f"/|pr view 8 -R {REPO} --json baseRefName,url "
                    '-q .baseRefName + " " + .url',
                )

    def test_canonical_form_into_protected_base_is_refused(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO} --merge", base="main", protected="main"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("protected base branch", result.stderr)
        self.assertIn(URL + "8", result.stderr)
        self.assertIn(f"gh pr merge 8 -R {REPO} --merge --delete-branch", result.stderr)
        self.assertFalse(any("graphql" in c for c in self.gh_calls()))

    def test_list_matches_whole_branch_names(self):
        cmd = f"gh pr merge 8 -R {REPO}"
        self.assertEqual(
            self.run_hook(cmd, base="release", protected="main release").returncode, 2
        )
        self.assertEqual(
            self.run_hook(cmd, base="main-old", protected="main").returncode, 0
        )

    def test_lookup_failure_is_refused(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}", base="FAIL", protected="main"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("couldn't read the base branch", result.stderr)

    def test_commands_without_both_words_skip_the_gate(self):
        for command in (
            "ls -la",
            "gh pr view 7",
            "git merge origin/main",
            "gh pr list --state merged",
            "gh pr view 7 --json mergeable",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command, protected="main")
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_threads_still_checked_after_an_allowed_merge(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}",
            base="dev",
            protected="main",
            threads="T1\tf.py:3",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved review threads", result.stderr)

    # Empty list: behaviour identical to the hook before protected bases.

    def test_default_protects_nothing(self):
        for command in (
            "gh pr merge 7",
            "gh pr merge 7 && gh pr merge 8",
            BYPASSES["pipe to sh"],
        ):
            for protected in (None, ""):
                with self.subTest(command=command, protected=protected):
                    self.assertEqual(
                        self.run_hook(command, protected=protected).returncode, 0
                    )
        self.assertEqual(self.run_hook("gh pr merge 7", base="FAIL").returncode, 0)

    def test_default_makes_the_same_gh_calls_as_before(self):
        self.run_hook("gh pr merge 7 -R o/r")
        calls = [c.split("|", 1)[1] for c in self.gh_calls()]
        self.assertEqual(calls[0], "pr view 7 -R o/r --json url -q .url")
        self.assertTrue(calls[1].startswith("api graphql"))
        self.assertEqual(len(calls), 2)

    def test_default_still_blocks_unresolved_threads(self):
        result = self.run_hook("gh pr merge 7", base="dev", threads="T1\tf.py:3")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved review threads", result.stderr)

    def test_default_ignores_other_commands(self):
        self.assertEqual(self.run_hook("gh pr view 7").returncode, 0)
        self.assertEqual(self.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()

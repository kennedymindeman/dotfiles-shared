"""pr-merge-gate.sh: the merge conditions and the one command form it checks."""

import contextlib
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "claude/hooks/pr-merge-gate.sh"
URL = "https://github.com/example-org/example-repo/pull/"
REPO = "example-org/example-repo"
SHA = "0123456789abcdef0123456789abcdef01234567"
OLD_SHA = "fedcba9876543210fedcba9876543210fedcba98"
VERDICT = f"Blind review: pass {SHA}"
PIN = f" --match-head-commit {SHA}"
PR_FILTER = '.html_url + " " + .head.ref + " " + .head.sha + " " + (.head.repo.full_name // "")'

# Stands in for gh, printing what gh's -q filter would. Logs "<cwd>|<args>"
# per call. Each GH_STUB_* fact set to FAIL makes that lookup exit 1.
# `api repos/.../pulls/N` answers "<url><N> feature <head sha>" (or
# GH_STUB_VIEW_OUT verbatim when that is set), `api graphql` the unresolved
# threads, and `api repos/...` the comments' first lines. The commit's check
# runs and statuses have no -q filter: they answer GH_STUB_RUNS and
# GH_STUB_STATUSES as raw JSON pages, as the compare answers GH_STUB_COMPARE
# and the list of PRs based on a branch GH_STUB_STACKED.
# GH_STUB_THREADS_PAGE2 reaches only a caller that paginates on $endCursor, and
# GH_STUB_RUNS_PAGE2, GH_STUB_STATUSES_PAGE2, and GH_STUB_COMPARE_PAGE2 only
# one that passes --paginate. GH_STUB_VIEW=RATE fails the PR read with a rate
# limit.
STUB = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$GH_STUB_LOG"
printf '%s\\036' "$PWD" "$@" >> "$GH_STUB_ARGS"
printf '\\035' >> "$GH_STUB_ARGS"
case "$1 $2" in
  "api repos/"*"/pulls/"*)
    case "$*" in *additions*)
      [ "$GH_STUB_SIZE" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
      printf '%s %s\\n' "${GH_STUB_SIZE_SHA-$GH_STUB_SHA}" "${GH_STUB_SIZE-10 1}"; exit 0 ;;
    esac
    [ "$GH_STUB_VIEW" = FAIL ] && { echo "gh: Not Found (HTTP 404)" >&2; exit 1; }
    [ "$GH_STUB_VIEW" = RATE ] && { echo "gh: API rate limit exceeded (HTTP 403)" >&2; exit 1; }
    [ -n "${GH_STUB_VIEW_OUT+set}" ] && { printf '%s' "$GH_STUB_VIEW_OUT"; exit 0; }
    sel=${2##*/}; case "$sel" in ''|*[!0-9]*) sel=7 ;; esac
    printf '%s%s feature %s %s\\n' "$GH_STUB_URL" "$sel" "$GH_STUB_SHA" \\
      "${GH_STUB_HEAD_REPO-example-org/example-repo}" ;;
  "api repos/"*"/pulls?"*)
    [ "$GH_STUB_STACKED" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    [ "$GH_STUB_STACKED" = EMPTY ] && exit 0
    printf '%s' "${GH_STUB_STACKED:-[]}" ;;
  "api repos/"*"/compare/"*)
    [ "$GH_STUB_COMPARE" = FAIL ] && { echo "HTTP 404" >&2; exit 1; }
    [ "$GH_STUB_COMPARE" = RATE ] && { echo "API rate limit exceeded" >&2; exit 1; }
    printf '%s' "$GH_STUB_COMPARE"
    case "$*" in *--paginate*) printf '%s' "$GH_STUB_COMPARE_PAGE2" ;; esac ;;
  "api graphql")
    [ "$GH_STUB_THREADS" = FAIL ] && { echo "API rate limit exceeded" >&2; exit 1; }
    printf '%s' "$GH_STUB_THREADS"
    case "$*" in *--paginate*'$endCursor'*) printf '%s' "$GH_STUB_THREADS_PAGE2" ;; esac ;;
  "api repos/"*"/commits/"*"/check-runs"*)
    [ "$GH_STUB_RUNS" = FAIL ] && { echo "API rate limit exceeded" >&2; exit 1; }
    printf '%s' "$GH_STUB_RUNS"
    case "$*" in *--paginate*) printf '%s' "$GH_STUB_RUNS_PAGE2" ;; esac ;;
  "api repos/"*"/commits/"*"/status"*)
    [ "$GH_STUB_STATUSES" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    printf '%s' "$GH_STUB_STATUSES"
    case "$*" in *--paginate*) printf '%s' "$GH_STUB_STATUSES_PAGE2" ;; esac ;;
  "api repos/"*)
    [ "$GH_STUB_COMMENTS" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    printf '%s' "$GH_STUB_COMMENTS" ;;
esac
"""



def runs_page(*runs):
    """One page of a commit's check runs, as gh api prints it: (name, status,
    conclusion) each."""
    return json.dumps({
        "total_count": len(runs),
        "check_runs": [{"name": n, "status": s, "conclusion": c} for n, s, c in runs],
    })


def statuses_page(*statuses):
    """One page of a commit's combined status: (context, state) each. GitHub
    reports the combined state as pending when there are no statuses."""
    return json.dumps({
        "state": "success" if statuses else "pending",
        "statuses": [{"context": c, "state": s} for c, s in statuses],
        "total_count": len(statuses),
    })


NO_RUNS = runs_page()
NO_STATUSES = statuses_page()

# Each of these must be refused. Every fact holds for the PR they name, so
# only the form decides.
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
    "pin with = value": f"gh pr merge 8 -R {REPO} --match-head-commit={SHA}",
    "short pin": f"gh pr merge 8 -R {REPO} --match-head-commit {SHA[:12]}",
    "uppercase pin": f"gh pr merge 8 -R {REPO} --match-head-commit {SHA.upper()}",
    "two pins": f"gh pr merge 8 -R {REPO}{PIN} --match-head-commit {OLD_SHA}",
    "pin missing value": f"gh pr merge 8 -R {REPO} --match-head-commit",
}

# Final red-team cases. Each runs through the hook and, if allowed, through
# real bash with a logging fake gh; no merge may reach gh.
RED_TEAM = {
    "split gh with quotes": f"g''h pr merge 7 -R {REPO}",
    "split merge with quotes": f'gh pr mer""ge 7 -R {REPO}',
    "backslash in gh": f"g\\h pr merge 7 -R {REPO}",
    "uppercase GH": f"GH pr merge 7 -R {REPO}",
    "empty expansion in gh": f"g${{x}}h pr merge 7 -R {REPO}",
    "second -R": f"gh pr merge 8 -R {REPO} -R example-org/other",
    "second --repo=": f"gh pr merge 8 -R {REPO} --repo=example-org/other",
    "attached second -R": f"gh pr merge 8 -R {REPO} -Rexample-org/other",
    "auto merge": f"gh pr merge 8 -R {REPO} --auto",
    "glob star": f"gh pr merge 8 -R {REPO} --subject *",
    "glob question": f"gh pr merge 8 -R {REPO} --body ?",
    "bracket glob": f"gh pr merge 8 -R {REPO} --body [ab]",
    "tilde cd": f"cd ~ && gh pr merge 8 -R {REPO}",
    "brace group": f"{{ gh pr merge 8 -R {REPO}; }}",
    "history bang": f"gh pr merge 8 -R {REPO} --body !!",
    "graphql merge mutation": "gh api graphql -f query='mutation{mergePullRequest(input:{pullRequestId:\"PR_x\"}){clientMutationId}}'",
    "graphql merge mutation, no braces": "gh api graphql -f query=@q -F mergePullRequest=1",
}

# Merges whose program word or merge path is spelled indirectly. Each must be
# refused.
OBFUSCATED = [
    "${x:-gh} pr merge 7 -R o/r",
    "$'\\x67h' pr merge 7 -R o/r",
    "$'\\147h' pr merge 7 -R o/r",
    "x=h; g$x pr merge 7 -R o/r",
    "g=g; ${g}h pr merge 7 -R o/r",
    "$(printf '\\147\\150') pr merge 7 -R o/r",
    "/some/dir/g[h] pr merge 7 -R o/r",
    "gh api -X PUT repos/o/r/pulls/7/mer${x:-g}e",
    "echo '${'; gh pr merge 7 -R o/r",
    "echo '${' && gh pr merge 7 -R o/r",
]

# Commands that mention the gated words but cannot run a merge.
HARMLESS = [
    "echo 'run gh pr merge 7 -R o/r once CI passes'",
    "git commit -m 'Let the gate refuse gh pr merge into main'",
    "grep -n 'gh pr merge' claude/hooks/pr-merge-gate.sh",
    "gh pr view 7 -R o/r --json body -q '.body | test(\"gh pr merge\")'",
    "printf '%s\\n' \"gh api repos/o/r/pulls/7/merge\"",
    "git log --oneline --grep='pr merge' | head -5",
    "gh issue comment 5 --body 'Use gh pr merge 7 -R o/r after review'",
    "gh pr create --title 'Gate gh pr merge' --body 'Refuses merges into main.'",
    "gh api repos/o/r/pulls/7 --jq '.mergeable, .mergeable_state'",
    "gh api repos/o/r/pulls/7 --jq .mergeable 2>&1 | head -5",
    'gh api -X PATCH repos/o/r/issues/comments/1 -f body="Ready to merge" >/dev/null',
    'grep -n "gh pr merge\\|pr view" claude/hooks/pr-merge-gate.sh',
    # An apostrophe inside the quoted text: not a command, and not misread.
    'git commit -m "don\'t gh pr merge 7 yet"',
    'echo "it\'s fine to gh pr merge later"',
]

CANONICAL = [
    f"gh pr merge 8 -R {REPO}{PIN}",
    f"gh pr merge 8 -R {REPO} --merge --delete-branch{PIN}",
    f"gh pr merge 8 -R {REPO}{PIN} --squash --subject 'Ship it, #12 now' --body \"a b\"",
    f"cd /tmp && gh pr merge 8 -R {REPO} --rebase{PIN}",
    f"cd 'a dir' && gh pr merge 8 -R {REPO} --subject '#1 fix'{PIN}",
    f"gh  pr\tmerge 8 -R {REPO}{PIN}",
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
        self.args_log = self.dir / "gh.args"

    def run_hook(
        self,
        command,
        view="",
        runs=NO_RUNS,
        runs_page2="",
        statuses=NO_STATUSES,
        statuses_page2="",
        threads="",
        threads_page2="",
        comments=VERDICT,
        extra=None,
        path=None,
        view_out=None,
        home=None,
        env_extra=None,
    ):
        # Defaults: every fact holds. extra: optional "cwd" and "description"
        # for the payload, "raw" text sent instead of the JSON payload, or
        # "proc_cwd", the directory the hook process itself starts in.
        # view_out: the exact text `gh pr view` prints. env_extra: more
        # environment for the hook.
        extra = extra or {}
        env = {
            **os.environ,
            "PATH": path or f"{self.dir}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_LOG": str(self.log),
            "GH_STUB_ARGS": str(self.args_log),
            "GH_STUB_URL": URL,
            "GH_STUB_SHA": SHA,
            "GH_STUB_VIEW": view,
            "GH_STUB_RUNS": runs,
            "GH_STUB_RUNS_PAGE2": runs_page2,
            "GH_STUB_STATUSES": statuses,
            "GH_STUB_STATUSES_PAGE2": statuses_page2,
            "GH_STUB_THREADS": threads,
            "GH_STUB_THREADS_PAGE2": threads_page2,
            "GH_STUB_COMMENTS": comments,
            **(env_extra or {}),
        }
        for key in ("GH_REPO", "GH_STUB_VIEW_OUT"):
            env.pop(key, None)
        if view_out is not None:
            env["GH_STUB_VIEW_OUT"] = view_out
        if home is not None:
            env["HOME"] = str(home)
        tool_input = {"command": command}
        if "description" in extra:
            # Placed first so its text precedes the command in the JSON.
            tool_input = {"description": extra["description"], **tool_input}
        # Field order as Claude Code sends it: cwd before tool_input.
        payload = {
            "cwd": str(extra.get("cwd", self.dir)),
            "tool_name": "Bash",
            "tool_input": tool_input,
        }
        return subprocess.run(
            [str(HOOK)],
            input=extra.get("raw", json.dumps(payload)),
            env=env,
            cwd=extra.get("proc_cwd"),
            capture_output=True,
            check=False,
            text=True,
        )

    def gh_calls(self):
        if not self.log.exists():
            return []
        lines = self.log.read_text().splitlines()
        return [c for c in lines if c.startswith(("/|", f"{self.dir}|"))]

    def pr_lookups(self):
        return [c for c in self.gh_calls() if "/pulls/" in c]

    def gh_argvs(self):
        """Every gh call as its full argument list, cwd first."""
        if not self.args_log.exists():
            return []
        return [
            c.split("\036")[:-1] for c in self.args_log.read_text().split("\035") if c
        ]

    # The conditions.

    def test_all_conditions_hold_allows_a_merge_into_main(self):
        result = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}")
        self.assertEqual(result.returncode, 0, result.stderr)

    def printed_command(self, result):
        """The command a pin refusal prints: alone on the line after the
        explanation, indented two spaces."""
        lines = result.stderr.rstrip("\n").split("\n")
        self.assertEqual(len(lines), 2, result.stderr)
        self.assertTrue(lines[1].startswith("  "), result.stderr)
        return lines[1][2:]

    def test_merge_must_pin_the_checked_head(self):
        # The refusal prints the agent's own command, every flag kept, with the
        # pin added; fed back to the gate, it is a recognized, pinned merge.
        for command in (
            f"gh pr merge 8 -R {REPO}",
            f"cd /tmp && gh pr merge 8 -R {REPO} --squash --subject 'a b'",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("doesn't pin the head commit", result.stderr)
                fixed = self.printed_command(result)
                self.assertEqual(fixed.replace(PIN, "", 1), command)
                again = self.run_hook(fixed)
                self.assertEqual(again.returncode, 0, again.stderr)
        # A pin for another commit: the same command with the head's SHA, in
        # the pin's place even when the old SHA also appears in the subject.
        for command, fixed_command in (
            (
                f"gh pr merge 8 -R {REPO} --match-head-commit {OLD_SHA} --squash",
                f"gh pr merge 8 -R {REPO} --match-head-commit {SHA} --squash",
            ),
            (
                f"gh pr merge 8 -R {REPO} --subject '{OLD_SHA}' --match-head-commit {OLD_SHA}",
                f"gh pr merge 8 -R {REPO} --subject '{OLD_SHA}'{PIN}",
            ),
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(f"pins --match-head-commit {OLD_SHA}", result.stderr)
                fixed = self.printed_command(result)
                self.assertEqual(fixed, fixed_command)
                again = self.run_hook(fixed)
                self.assertEqual(again.returncode, 0, again.stderr)

    def test_each_condition_failing_alone_is_refused(self):
        cases = {
            "pending check": (
                {"runs": runs_page(("ci", "in_progress", None))},
                "that are pending or failed",
            ),
            "failed check": (
                {"runs": runs_page(("ci", "completed", "failure"))},
                "that are pending or failed",
            ),
            "failed status": (
                {"statuses": statuses_page(("ci/legacy", "failure"))},
                "that are pending or failed",
            ),
            "unresolved thread": (
                {"threads": "T1\tf.py:3"},
                "unresolved review threads",
            ),
            "no verdict": ({"comments": "Looks good\n"}, "no blind-review verdict"),
        }
        for name, (facts, why) in cases.items():
            with self.subTest(case=name):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}", **facts)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(why, result.stderr)

    def test_refusal_names_the_verdict_line_to_post(self):
        result = self.run_hook(f"gh pr merge 8 -R {REPO}", comments="")
        self.assertEqual(result.returncode, 2)
        self.assertIn(f"\n  {VERDICT}\n", result.stderr)
        self.assertIn("older commit doesn't count", result.stderr)

    def test_verdict_for_an_older_commit_is_refused(self):
        for comments in (
            f"Blind review: pass {OLD_SHA}\n",
            f"Blind review: pass {SHA[:12]}\n",
            f"Blind review: pass {SHA} \n",
            f"blind review: pass {SHA}\n",
            f"Blind review: fail {SHA}\n",
        ):
            with self.subTest(comments=comments):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}", comments=comments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("no blind-review verdict", result.stderr)
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}{PIN}",
            comments=f"Blind review: pass {OLD_SHA}\nnote\n{VERDICT}\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_self_review_verdict_counts_only_for_a_small_pr(self):
        merge = f"gh pr merge 8 -R {REPO}{PIN}"
        self_verdict = f"Self review: pass {SHA}\n"
        for size in ("10 1", "80 5", "0 0"):
            with self.subTest(size=size):
                result = self.run_hook(
                    merge, comments=self_verdict, env_extra={"GH_STUB_SIZE": size}
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        for size in ("81 1", "10 6", "9999999 1", "1 9999999"):
            with self.subTest(size=size):
                result = self.run_hook(
                    merge, comments=self_verdict, env_extra={"GH_STUB_SIZE": size}
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("A self review covers at most 80", result.stderr)
                self.assertIn(f"\n  {VERDICT}\n", result.stderr)
        # An unreadable size refuses.
        for size in ("FAIL", "", "10", "10 1 2", "x 1", "-1 1", "null 1"):
            with self.subTest(unreadable=size):
                result = self.run_hook(
                    merge, comments=self_verdict, env_extra={"GH_STUB_SIZE": size}
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("couldn't read the size", result.stderr)
        # Counts that describe another head, as just after a push, refuse.
        result = self.run_hook(
            merge, comments=self_verdict, env_extra={"GH_STUB_SIZE_SHA": OLD_SHA}
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("couldn't read the size", result.stderr)

    def test_size_filter_refuses_a_missing_count(self):
        # The hook's own jq filter, on GitHub-shaped JSON.
        if shutil.which("jq") is None:
            self.skipTest("jq not installed")
        source = HOOK.read_text()
        start = source.index("-q 'if ([.additions") + 4
        size_filter = source[start : source.index("' 2>&1", start)]
        for pr, expected in (
            ({"additions": 3, "deletions": 4, "changed_files": 2}, f"{SHA} 7 2"),
            ({"additions": None, "deletions": 30, "changed_files": 1}, "unreadable"),
            ({"additions": "3", "deletions": 4, "changed_files": 1}, "unreadable"),
            ({"additions": 3, "deletions": 4}, "unreadable"),
        ):
            with self.subTest(pr=pr):
                out = subprocess.run(
                    ["jq", "-r", size_filter],
                    input=json.dumps({"head": {"sha": SHA}, **pr}),
                    capture_output=True,
                    check=True,
                    text=True,
                )
                self.assertEqual(out.stdout.strip(), expected)

    def test_self_review_verdict_must_name_the_head(self):
        merge = f"gh pr merge 8 -R {REPO}{PIN}"
        for comments in (
            f"Self review: pass {OLD_SHA}\n",
            f"self review: pass {SHA}\n",
            f"Self review: pass {SHA[:12]}\n",
            f"note\nSelf review: fail {SHA}\n",
        ):
            with self.subTest(comments=comments):
                result = self.run_hook(merge, comments=comments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("no blind-review verdict", result.stderr)
                self.assertEqual(len(self.pr_lookups()), 1)
                self.log.unlink()

    def test_blind_verdict_needs_no_size_lookup(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}{PIN}",
            comments=f"Self review: pass {SHA}\n{VERDICT}\n",
            env_extra={"GH_STUB_SIZE": "5000 90"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.pr_lookups()), 1)

    def test_no_checks_at_all_passes(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}{PIN}", runs=NO_RUNS, statuses=NO_STATUSES
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_checks_are_read_for_the_head_commit(self):
        merge = f"gh pr merge 8 -R {REPO}{PIN}"
        passing = runs_page(
            ("build", "completed", "success"),
            ("lint", "completed", "neutral"),
            ("docs", "completed", "skipped"),
        )
        with self.subTest(case="passing"):
            result = self.run_hook(
                merge, runs=passing, statuses=statuses_page(("ci/legacy", "success"))
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        refused_runs = [
            ("queued", None),
            ("in_progress", None),
            ("waiting", None),
            ("requested", None),
            ("pending", None),
            ("mystery", None),
            ("completed", "failure"),
            ("completed", "cancelled"),
            ("completed", "timed_out"),
            ("completed", "action_required"),
            ("completed", "stale"),
            ("completed", "startup_failure"),
            ("completed", "mystery"),
            ("completed", None),
        ]
        for status, conclusion in refused_runs:
            state = conclusion if status == "completed" else status
            with self.subTest(status=status, conclusion=conclusion):
                result = self.run_hook(
                    merge, runs=runs_page(("build", "completed", "success"),
                                          ("bad", status, conclusion))
                )  # fmt: skip
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("that are pending or failed", result.stderr)
                self.assertIn(f"\n  bad ({'null' if state is None else state})\n", result.stderr)
                self.assertNotIn("build", result.stderr)
        for state in ("pending", "failure", "error", "mystery"):
            with self.subTest(status_state=state):
                result = self.run_hook(
                    merge, statuses=statuses_page(("ci/ok", "success"), ("ci/bad", state))
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(f"\n  ci/bad ({state})\n", result.stderr)
        # Every page is read: a failure on the second page refuses.
        late = self.run_hook(
            merge, runs=passing, runs_page2=runs_page(("late", "completed", "failure"))
        )
        self.assertEqual(late.returncode, 2, late.stderr)
        self.assertIn("  late (failure)", late.stderr)
        late = self.run_hook(merge, statuses_page2=statuses_page(("ci/late", "pending")))
        self.assertEqual(late.returncode, 2, late.stderr)
        self.assertIn("  ci/late (pending)", late.stderr)

    def test_fewer_check_runs_than_total_count_are_refused(self):
        # Every page carries the commit's total_count; reading fewer runs than
        # that means a page went missing.
        merge = f"gh pr merge 8 -R {REPO}{PIN}"
        one = {"name": "build", "status": "completed", "conclusion": "success"}
        for name, runs, page2 in (
            ("one page", json.dumps({"total_count": 2, "check_runs": [one]}), ""),
            (
                "two pages",
                json.dumps({"total_count": 3, "check_runs": [one]}),
                json.dumps({"total_count": 3, "check_runs": [one]}),
            ),
            ("no total_count", json.dumps({"check_runs": [one]}), ""),
        ):
            with self.subTest(name):
                result = self.run_hook(merge, runs=runs, runs_page2=page2)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("couldn't read the check runs", result.stderr)
        full = json.dumps({"total_count": 2, "check_runs": [one]})
        result = self.run_hook(merge, runs=full, runs_page2=full)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_check_reads_are_refused(self):
        merge = f"gh pr merge 8 -R {REPO}{PIN}"
        bad = {
            "empty": "",
            "not json": "API rate limit exceeded",
            "array": "[]",
            "error object": '{"message": "Not Found"}',
            "null list": '{"check_runs": null, "statuses": null}',
            "list of strings": '{"check_runs": ["x"], "statuses": ["x"]}',
            "trailing junk": NO_RUNS[:-1] + ', "statuses": []} garbage',
        }
        for name, out in bad.items():
            for field, why in (
                ("runs", "couldn't read the check runs"),
                ("statuses", "couldn't read the commit statuses"),
            ):
                with self.subTest(case=name, field=field):
                    result = self.run_hook(merge, **{field: out})
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(why, result.stderr)

    def test_unreadable_facts_are_refused(self):
        cases = {
            "pr view": ({"view": "FAIL"}, "couldn't read the head commit"),
            "check runs": ({"runs": "FAIL"}, "couldn't read the check runs"),
            "commit statuses": ({"statuses": "FAIL"}, "couldn't read the commit statuses"),
            "threads": ({"threads": "FAIL"}, "couldn't read the review threads"),
            "comments": ({"comments": "FAIL"}, "couldn't read the comments"),
        }
        for name, (facts, why) in cases.items():
            with self.subTest(case=name):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}", **facts)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(why, result.stderr)

    def test_malformed_pr_lookup_is_refused(self):
        cases = {
            "empty": "",
            "missing sha": f"{URL}8 feature\n",
            "short sha": f"{URL}8 feature {SHA[:39]}\n",
            "long sha": f"{URL}8 feature {SHA}0\n",
            "null": "null\n",
            "null sha": f"{URL}8 feature null\n",
            "uppercase sha": f"{URL}8 feature {SHA.upper()}\n",
            "non-numeric number": f"{URL}ab feature {SHA}\n",
        }
        for name, out in cases.items():
            with self.subTest(case=name):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", view_out=out)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("couldn't read the head commit", result.stderr)

    def test_modules_in_the_working_directory_are_not_imported(self):
        # The hook runs in the session's checkout; a json.py or shlex.py there
        # must not replace the standard-library module the gate's Python
        # imports. The planted shlex.py would, if imported, report "no merge"
        # and let the unreviewed merge through.
        start = self.dir / "start"
        start.mkdir()
        marker = self.dir / "shadow-ran"
        (start / "json.py").write_text(f"open({str(marker)!r}, 'a').write('json')\n")
        (start / "shlex.py").write_text(
            f"import sys\nopen({str(marker)!r}, 'a').write('shlex')\n"
            "print('none=1')\nsys.exit(0)\n"
        )
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}{PIN}",
            comments="",
            extra={"cwd": start, "proc_cwd": start},
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no blind-review verdict", result.stderr)
        self.assertFalse(marker.exists(), "a module from the working directory ran")

    def test_inline_python_runs_isolated_and_without_site(self):
        # A python3 on PATH that logs its first two arguments, then runs the
        # real one: every call the hook makes must start with -I -S.
        real = shutil.which("python3")
        log = self.dir / "python.log"
        shim = self.dir / "python3"
        shim.write_text(
            f"#!/bin/sh\nprintf '%s %s\\n' \"$1\" \"$2\" >> {str(log)!r}\nexec {real!r} \"$@\"\n"
        )
        shim.chmod(0o755)
        for command in (f"gh pr merge 8 -R {REPO}{PIN}", "ls -la"):
            with self.subTest(command=command):
                log.unlink(missing_ok=True)
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = log.read_text().splitlines()
                self.assertTrue(calls, "the hook never ran python3")
                self.assertEqual(set(calls), {"-I -S"})

    def test_missing_jq_refuses_merges(self):
        tools = self.dir / "tools"
        tools.mkdir()
        for name in ("cat", "python3", "head", "tr", "cut", "grep", "sed", "awk", "git"):
            found = shutil.which(name)
            if found:
                (tools / name).symlink_to(found)
        path = f"{self.dir}{os.pathsep}{tools}"
        result = self.run_hook(f"gh pr merge 8 -R {REPO}", path=path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("jq is missing", result.stderr)
        self.assertEqual(self.run_hook("ls -la", path=path).returncode, 0)

    def test_jq_ignores_a_home_jq_file(self):
        # jq sources $HOME/.jq into every program; one that empties select
        # would hide every failing check from the gate's filters.
        home = self.dir / "home"
        home.mkdir()
        (home / ".jq").write_text("def select(f): empty;\n")
        version = subprocess.run(["jq", "--version"], capture_output=True, text=True).stdout
        loaded = subprocess.run(
            ["jq", "-nc", "[1, 2] | map(select(. > 1))"],
            env={**os.environ, "HOME": str(home)},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(loaded.stdout.strip(), "[]", f"{version} didn't load $HOME/.jq")
        for facts in (
            {"runs": runs_page(("ci", "completed", "failure"))},
            {"statuses": statuses_page(("ci/legacy", "failure"))},
        ):
            with self.subTest(facts=facts):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", home=home, **facts)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("that are pending or failed", result.stderr)

    def test_a_comment_is_read_as_bash_reads_it(self):
        # An unquoted # that starts a word comments out the rest of its line,
        # quote characters included, so the merge on the next line runs.
        for quote in ("'", '"'):
            for flags in ("", " --squash -d"):
                command = f"echo a #{quote}\ngh pr merge 5 -R o/r{flags} #{quote}"
                with self.subTest(command=command):
                    strict = self.run_hook(command, comments="")
                    self.assertEqual(strict.returncode, 2, strict.stderr)
                    self.assertIn("one merge form the gate checks", strict.stderr)
        # A # inside quotes or mid-word starts no comment, and a merge that
        # only appears in a comment never runs.
        self.log.unlink(missing_ok=True)
        for command in (
            "echo a#'\ngh pr merge 5 -R o/r'",
            "echo '#'\"\ngh pr merge 5 -R o/r\"",
            "echo hi # gh pr merge 5 -R o/r",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command, comments="")
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_readings_that_disagree_count_as_a_merge(self):
        # Passing unchecked needs no merge in either reading (as written, and
        # without comments), or inert programs only in both.
        for command in (
            "npm test # then gh pr merge 5",
            "echo hi # (gh pr merge 5)",
            "make # gh pr merge 5 -R o/r",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("one merge form the gate checks", result.stderr)
        self.assertEqual(self.run_hook("npm test # then ship it").returncode, 0)
        self.assertEqual(self.gh_calls(), [])

    def test_gh_with_an_expanded_subcommand_is_refused(self):
        for command in (
            "gh pr $(cat sub.txt) 5 -R o/r",
            "gh $a $b 5",
            "gh pr `cat sub.txt` 5 -R o/r",
            'gh pr "${s}" 5',
        ):
            with self.subTest(command=command):
                strict = self.run_hook(command)
                self.assertEqual(strict.returncode, 2, strict.stderr)
                self.assertIn("one merge form the gate checks", strict.stderr)
        for command in ("gh pr view $n", "gh api repos/o/r/pulls/$n", 'git commit -m "x; gh $a"'):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook(command).returncode, 0)
        self.assertEqual(self.gh_calls(), [])

    def test_threads_past_the_first_page_are_read(self):
        result = self.run_hook(f"gh pr merge 8 -R {REPO}", threads_page2="T101\tf.py:9")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("T101", result.stderr)

    def test_merges_into_other_bases_meet_the_same_conditions(self):
        # The hook never looks the base up: the conditions apply to any PR.
        ok = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertFalse(any("baseRefName" in c for c in self.gh_calls()))
        refused = self.run_hook(f"gh pr merge 8 -R {REPO}", comments="")
        self.assertEqual(refused.returncode, 2, refused.stderr)

    def test_gh_calls(self):
        self.run_hook("gh pr merge 7 -R o/r")
        calls = self.gh_argvs()
        self.assertEqual([c[0] for c in calls], [str(self.dir)] * 5)
        view, runs, statuses, threads, comments = (c[1:] for c in calls)
        # The PR over REST, outside the GraphQL budget.
        self.assertEqual(
            view,
            ["api", "repos/o/r/pulls/7", "-q", PR_FILTER],
        )
        # Later calls name the repo from the PR's url.
        # Checks are read by the head SHA from the PR read, never by PR number, at
        # the largest page size, every page.
        self.assertEqual(
            runs, ["api", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100", "--paginate"]
        )
        self.assertEqual(
            statuses, ["api", f"repos/{REPO}/commits/{SHA}/status?per_page=100", "--paginate"]
        )
        self.assertEqual(threads[:3], ["api", "graphql", "--paginate"])
        self.assertIn("owner=example-org", threads)
        self.assertIn("num=7", threads)
        query = next(a for a in threads if a.startswith("query="))
        self.assertIn("after:$endCursor", query)
        self.assertIn("pageInfo{ hasNextPage endCursor }", query)
        # The largest page size, so a long thread costs few requests.
        self.assertEqual(
            comments[:3],
            ["api", f"repos/{REPO}/issues/7/comments?per_page=100", "--paginate"],
        )

    def test_gh_filters_pick_the_right_facts(self):
        # gh applies each -q filter itself; run them through jq on fixtures.
        self.run_hook(f"gh pr merge 7 -R {REPO}")
        filters = [c[c.index("-q") + 1] for c in self.gh_argvs()[3:]]
        threads, comments = filters

        def jq(expression, data):
            return subprocess.run(
                ["jq", "-r", expression],
                input=json.dumps(data),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()

        node = {"path": "f.py", "line": 3,
                "comments": {"nodes": [{"author": {"login": "bot"}, "body": "a\nb",
                                        "url": "u"}]}}  # fmt: skip
        page = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "T1", "isResolved": True, **node},
                      {"id": "T2", "isResolved": False, **node}],
            "pageInfo": {"hasNextPage": False, "endCursor": "x"}}}}}}  # fmt: skip
        self.assertEqual(jq(threads, page), ["T2\tf.py:3\tbot\tu", "    a b"])
        self.assertEqual(
            jq(
                comments,
                [
                    {"author_association": "NONE", "body": VERDICT},
                    {"author_association": "CONTRIBUTOR", "body": VERDICT},
                    {"author_association": "COLLABORATOR", "body": VERDICT + "\r\nok"},
                    {"author_association": "OWNER", "body": "note\n" + VERDICT},
                    {"author_association": "MEMBER", "body": "x"},
                ],
            ),
            [VERDICT, "note", "x"],
        )

    def test_verdict_survives_deleting_review_evidence(self):
        # A verdict for an older commit counts when the head is ahead of it and
        # every file changed since is a removed file under .pr-assets/.
        def page(status="ahead", files=(("removed", ".pr-assets/issue-5/a.png"),)):
            return json.dumps(
                {"status": status, "files": [{"status": s, "filename": f} for s, f in files]}
            )

        old = f"Blind review: pass {OLD_SHA}\n"
        merge = f"gh pr merge 8 -R {REPO}{PIN}"
        ok = self.run_hook(merge, comments=old, env_extra={"GH_STUB_COMPARE": page()})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn(
            ["api", f"repos/{REPO}/compare/{OLD_SHA}...{SHA}?per_page=100", "--paginate"],
            [c[1:] for c in self.gh_argvs()],
        )
        # Still pinned to the current head, not the reviewed commit.
        unpinned = self.run_hook(
            f"gh pr merge 8 -R {REPO}", comments=old, env_extra={"GH_STUB_COMPARE": page()}
        )
        self.assertEqual(unpinned.returncode, 2, unpinned.stderr)
        self.assertEqual(self.printed_command(unpinned), merge)
        many = [("removed", f".pr-assets/{i}.png") for i in range(300)]
        refused = {
            "modified evidence": (page(files=[("modified", ".pr-assets/a.png")]), ""),
            "removed elsewhere": (page(files=[("removed", "src/a.py")]), ""),
            "lookalike directory": (page(files=[("removed", ".pr-assets-x/a.png")]), ""),
            "removal plus a change": (
                page(files=[("removed", ".pr-assets/a.png"), ("modified", "src/a.py")]), ""),
            "renamed": (page(files=[("renamed", ".pr-assets/b.png")]), ""),
            "diverged": (page("diverged"), ""),
            "behind": (page("behind"), ""),
            "identical": (page("identical", ()), ""),
            "no files": (page(files=()), ""),
            "300 files, maybe cut short": (page(files=many), ""),
            "a later page changes code": (page(), page(files=[("added", "src/a.py")])),
            "unreadable": ("FAIL", ""),
            "not json": ("Not Found", ""),
            "error object": ('{"message": "Not Found"}', ""),
        }  # fmt: skip
        for name, (first, second) in refused.items():
            with self.subTest(name):
                env = {"GH_STUB_COMPARE": first, "GH_STUB_COMPARE_PAGE2": second}
                result = self.run_hook(merge, comments=old, env_extra=env)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("no blind-review verdict", result.stderr)
        limited = self.run_hook(merge, comments=old, env_extra={"GH_STUB_COMPARE": "RATE"})
        self.assertEqual(limited.returncode, 2, limited.stderr)
        self.assertIn("rateLimit{remaining resetAt}", limited.stderr)

    def test_delete_branch_is_refused_while_a_pr_is_based_on_the_head(self):
        stacked = json.dumps([{"number": 9, "html_url": f"{URL}9"}])
        merge = f"gh pr merge 8 -R {REPO} --merge --delete-branch{PIN}"
        result = self.run_hook(merge, env_extra={"GH_STUB_STACKED": stacked})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(f"#9 {URL}9", result.stderr)
        self.assertIn(
            ["api", f"repos/{REPO}/pulls?state=open&base=feature&per_page=100"],
            [c[1:] for c in self.gh_argvs()],
        )
        # The refusal prints the same command without the flag, which passes.
        fixed = result.stderr.rstrip("\n").split("\n")[-1]
        self.assertEqual(fixed, f"  gh pr merge 8 -R {REPO} --merge{PIN}")
        again = self.run_hook(fixed.strip(), env_extra={"GH_STUB_STACKED": stacked})
        self.assertEqual(again.returncode, 0, again.stderr)
        # A branch name is sent URL-encoded.
        self.args_log.unlink()
        self.run_hook(
            merge,
            view_out=f"{URL}8 agent/issue-5&x {SHA} {REPO}",
            env_extra={"GH_STUB_STACKED": stacked},
        )
        self.assertIn(
            ["api", f"repos/{REPO}/pulls?state=open&base=agent%2Fissue-5%26x&per_page=100"],
            [c[1:] for c in self.gh_argvs()],
        )
        self.assertEqual(self.run_hook(merge, env_extra={"GH_STUB_STACKED": "[]"}).returncode, 0)
        for out in ("FAIL", '{"message": "Not Found"}', "Not Found"):
            with self.subTest(out=out):
                bad = self.run_hook(merge, env_extra={"GH_STUB_STACKED": out})
                self.assertEqual(bad.returncode, 2, bad.stderr)
                self.assertIn("couldn't read the open PRs based on feature", bad.stderr)

    def test_stacked_read_must_be_exactly_one_list(self):
        # An empty answer, or more than one list, is unreadable, not "none".
        merge = f"gh pr merge 8 -R {REPO} --delete-branch{PIN}"
        for out in ("EMPTY", "[][]", "[] {}"):
            with self.subTest(out=out):
                bad = self.run_hook(merge, env_extra={"GH_STUB_STACKED": out})
                self.assertEqual(bad.returncode, 2, bad.stderr)
                self.assertIn("couldn't read the open PRs based on feature", bad.stderr)

    def test_stacked_check_skips_a_fork_head(self):
        # A fork's branch named like a base-repo branch is another branch.
        stacked = json.dumps([{"number": 9, "html_url": f"{URL}9"}])
        merge = f"gh pr merge 8 -R {REPO} --delete-branch{PIN}"
        fork = self.run_hook(
            merge, env_extra={"GH_STUB_STACKED": stacked, "GH_STUB_HEAD_REPO": "someone/example-repo"}
        )
        self.assertEqual(fork.returncode, 0, fork.stderr)
        self.assertFalse(any("/pulls?" in c for c in self.gh_calls()))
        # No head repo (a deleted fork) or an unreadable one refuses the flag.
        for out in (f"{URL}8 feature {SHA}", f"{URL}8 feature {SHA} "):
            with self.subTest(out=out):
                bad = self.run_hook(merge, view_out=out)
                self.assertEqual(bad.returncode, 2, bad.stderr)
                self.assertIn("couldn't read the head repository", bad.stderr)
        self.assertEqual(
            self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", view_out=f"{URL}8 feature {SHA}").returncode,
            0,
        )
        extra = self.run_hook(merge, view_out=f"{URL}8 feature {SHA} {REPO} x")
        self.assertEqual(extra.returncode, 2, extra.stderr)
        self.assertIn("couldn't read the head commit", extra.stderr)

    def test_rate_limited_reads_point_at_both_budgets(self):
        # gh can replay a cached rate-limit error after the budget recovers.
        for facts in ({"view": "RATE"}, {"threads": "FAIL"}, {"runs": "FAIL"}):
            with self.subTest(facts=facts):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", **facts)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("`gh api rate_limit` for REST (core)", result.stderr)
                self.assertIn(
                    "gh api graphql -f query='query{rateLimit{remaining resetAt}}'",
                    result.stderr,
                )
                self.assertIn("If the failing read's counter has points left", result.stderr)
                self.assertIn("gh config clear-cache", result.stderr)
                self.assertIn("wait until that counter resets", result.stderr)
        other = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", comments="FAIL")
        self.assertEqual(other.returncode, 2, other.stderr)
        self.assertNotIn("clear-cache", other.stderr)

    # The form gate.

    def test_every_bypass_is_refused(self):
        for name, command in BYPASSES.items():
            with self.subTest(name):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
                self.assertIn("one merge form the gate checks", result.stderr)
                self.assertIn("rephrase it", result.stderr)
        self.assertEqual(self.gh_calls(), [], "refused forms never reach gh")

    def test_loose_merge_commands_are_refused_with_the_form(self):
        # Merge commands the gate once checked in other forms are refused and
        # shown the one form to run, before any lookup.
        for command in (
            f"gh pr merge 7{PIN}",
            "gh pr merge 7 --squash -d",
            f'bash -c "gh pr merge 7 --squash{PIN}"',
            f'ssh host "gh pr merge 12 --squash{PIN}"',
            f"gh pr merge 7 -R {REPO}{PIN} && gh pr merge 8 -R {REPO}{PIN}",
            "python3 -c \"import os; os.system('gh pr merge 6 --squash')\"",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(
                    "To merge, run only:\n  [cd <path> && ] gh pr merge <number> -R "
                    "<owner>/<repo> --match-head-commit <head sha>",
                    result.stderr,
                )
        self.assertEqual(self.gh_calls(), [])

    def test_the_old_protected_bases_variable_has_no_effect(self):
        # PR_MERGE_GATE_PROTECTED_BASES used to switch modes; any value,
        # including ones the old list check refused, now changes nothing.
        for value in ("", "main", "main,release", ",", "-x"):
            env = {"PR_MERGE_GATE_PROTECTED_BASES": value}
            with self.subTest(value=value):
                loose = self.run_hook("gh pr merge 8", env_extra=env)
                self.assertEqual(loose.returncode, 2, loose.stderr)
                self.assertIn("one merge form the gate checks", loose.stderr)
                self.assertNotIn("PR_MERGE_GATE_PROTECTED_BASES", loose.stderr)
                ok = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", env_extra=env)
                self.assertEqual(ok.returncode, 0, ok.stderr)
                self.assertEqual(self.run_hook("ls -la", env_extra=env).returncode, 0)

    def test_canonical_forms_are_allowed(self):
        for command in CANONICAL:
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.pr_lookups()[0], f"{self.dir}|api repos/{REPO}/pulls/8 -q {PR_FILTER}"
                )

    def test_canonical_forms_still_meet_the_conditions(self):
        # Including spellings that don't contain the literal "gh pr merge".
        for command in CANONICAL:
            with self.subTest(command=command):
                result = self.run_hook(command, comments="")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("no blind-review verdict", result.stderr)

    def test_commands_without_both_words_skip_the_gate(self):
        for command in (
            "ls -la",
            "gh pr view 7",
            "git merge origin/main",
            "gh pr list --state merged",
            "gh pr view 7 --json mergeable",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_obfuscated_merges_are_refused(self):
        for command in OBFUSCATED:
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
                self.assertIn("one merge form the gate checks", result.stderr)
        self.assertEqual(self.pr_lookups(), [])

    def test_verdict_depends_only_on_the_command(self):
        # Text in other fields, such as an unclosed ${, must not hide the
        # command from the check.
        weird = self.dir / "a ${b"
        weird.mkdir()
        for extra in ({}, {"description": "see ${HOME"}, {"cwd": weird}):
            with self.subTest(extra=extra):
                loose = self.run_hook("gh pr merge 7", extra=extra)
                self.assertEqual(loose.returncode, 2, loose.stderr)
                self.assertIn("one merge form the gate checks", loose.stderr)
                unreviewed = self.run_hook(f"gh pr merge 7 -R {REPO}", comments="", extra=extra)
                self.assertEqual(unreviewed.returncode, 2, unreviewed.stderr)
                self.assertIn("no blind-review verdict", unreviewed.stderr)
                ok = self.run_hook(f"gh pr merge 7 -R {REPO}{PIN}", extra=extra)
                self.assertEqual(ok.returncode, 0, ok.stderr)
                harmless = self.run_hook("ls -la", extra=extra)
                self.assertEqual(harmless.returncode, 0, harmless.stderr)

    def test_harmless_mentions_are_allowed(self):
        for command in HARMLESS:
            with self.subTest(command=command):
                result = self.run_hook(command, comments="")
                self.assertEqual(result.returncode, 0, result.stderr)
        # The words sit inside one quoted argument, so no PR is looked up.
        self.assertEqual(self.gh_calls(), [])

    def test_harmless_shape_still_refuses_what_could_run(self):
        for command in (
            "echo 'gh pr merge 7 -R o/r' | sh",
            "echo 'gh pr merge 7 -R o/r' > x.sh",
            "git commit -m 'gh pr merge' && sh x.sh",
            "git -c alias.x='!gh pr merge 7' x",
            "gh pr view 7 && gh pr merge 7",
            'echo "$(gh pr merge 7 -R o/r)"',
            "gh alias set m 'pr merge'",
            "gh api 'repos/o/r/pulls/7/merge' -X PUT",
            "gh api -X PUT repos/o/r/pulls/7/merge --jq .sha",
            "gh api -X POST repos/o/r/merges -f base=main -f head=x",
            "gh api graphql -f query='mutation{mergePullRequest}'",
            "gh api graphql -f query='mutation{mergePullRequest}' 2>/dev/null",
            "echo 'gh pr merge 7' > /dev/nullx",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")

    def test_strict_form_refuses_an_unquoted_comment(self):
        # The shell would drop everything after the #word, pin included.
        for command in (
            f"cd #x && gh pr merge 8 -R {REPO}{PIN}",
            f"gh pr merge 8 -R {REPO} --subject #x{PIN}",
            f"gh pr merge 8 -R {REPO} --body #x{PIN}",
            f"gh pr merge 8 -R {REPO} --subject x --body #x --squash{PIN}",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
                self.assertIn("starts with #", result.stderr)
        self.assertEqual(self.pr_lookups(), [])

    def test_canonical_number_and_repo_are_ascii(self):
        for command in (
            f"gh pr merge ٨ -R {REPO}",  # Arabic-Indic eight
            f"gh pr merge ８ -R {REPO}",  # fullwidth eight
            f"gh pr merge ² -R {REPO}",  # superscript two
            "gh pr merge 8 -R о-org/example-repo",  # Cyrillic o
            "gh pr merge 8 -R example-org/répo",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("one merge form the gate checks", result.stderr)
        self.assertEqual(self.pr_lookups(), [])

    def test_malformed_payload_is_refused_unless_another_tool(self):
        good = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        for name, raw in [
            ("truncated", json.dumps(good)[:-5]),
            ("empty", ""),
            ("missing tool_input", json.dumps({"tool_name": "Bash"})),
            ("no tool name, no command", json.dumps({"tool_input": {}})),
            ("array", json.dumps([good])),
            ("two objects", json.dumps(good) + json.dumps(good)),
            ("null command", json.dumps({"tool_input": {"command": None}})),
        ]:
            with self.subTest(name):
                result = self.run_hook("", extra={"raw": raw})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("pr-merge-gate can't check it", result.stderr)
        for raw in (
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}}),
            json.dumps({"tool_name": "Edit"}),
        ):
            with self.subTest(raw=raw):
                result = self.run_hook("", extra={"raw": raw})
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_red_team_cases_never_merge(self):
        # No PR has a verdict here, so any merge reaching gh got past the gate.
        for name, command in RED_TEAM.items():
            with self.subTest(name):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(command, comments="")
                if result.returncode == 2:
                    continue
                subprocess.run(
                    ["bash", "-c", command],
                    cwd=self.dir,
                    env={
                        **os.environ,
                        "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
                        "GH_STUB_LOG": str(self.log),
                        "GH_STUB_ARGS": str(self.args_log),
                        "GH_STUB_URL": URL,
                        "GH_STUB_SHA": SHA,
                    },
                    capture_output=True,
                    check=False,
                )
                merges = [
                    c
                    for c in (
                        self.log.read_text() if self.log.exists() else ""
                    ).splitlines()
                    if "|pr merge" in c or ("|api" in c and "merge" in c.lower())
                ]
                self.assertEqual(merges, [], f"{command!r} reached gh as a merge")

    def test_text_nothing_executes_passes(self):
        # A merge mentioned only as text for inert programs isn't checked.
        for command in (
            'rg "gh pr merge" dir/',
            'rg "gh pr merge" --type sh',
            'git grep "gh pr merge"',
            'cat notes.md | grep "gh pr merge"',
            'gh pr edit 12 --body "After review: gh pr merge 12 --squash"',
            'git commit -m "don\'t gh pr merge 7 yet"',
            'echo "it\'s fine to gh pr merge later"',
        ):
            with self.subTest(command=command):
                result = self.run_hook(command, comments="")
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), [])


class CommentStripperTests(unittest.TestCase):
    """The form check's comment stripper, run from the hook's own source."""

    @classmethod
    def setUpClass(cls):
        code = HOOK.read_text(encoding="utf-8").split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        # Another tool's payload: the script defines everything, then exits.
        namespace = {"payload": json.dumps({"tool_name": "Read"})}
        with contextlib.suppress(SystemExit):
            exec(code, namespace)
        cls.strip = staticmethod(namespace["strip_comments"])

    def test_hash_inside_a_parameter_expansion_is_text(self):
        for command in ("echo ${x:- #y} z", "echo ${#x} z", "echo ${x#*/} z","echo ${x:-${y- #}} z"):
            with self.subTest(command=command):
                self.assertEqual(self.strip(command), command)

    def test_hash_inside_arithmetic_is_text(self):
        for command in ("echo $(( 1 #2 )) z", "echo $(( (1) #2 )) z"):
            with self.subTest(command=command):
                self.assertEqual(self.strip(command), command)

    def test_hash_after_a_backslash_is_text(self):
        for command in ("echo \\#x z", "echo a\\ #x z"):
            with self.subTest(command=command):
                self.assertEqual(self.strip(command), command)

    def test_hash_starting_a_word_is_a_comment(self):
        for command, stripped in (
            ("echo a #b\nls", "echo a \nls"),
            ("#x\nls", "\nls"),
            ("echo a;#b c", "echo a;"),
            ("echo ${x} #b", "echo ${x} "),
            ("echo $((1)) #b", "echo $((1)) "),
            ("echo $(# c\nls)", "echo $(\nls)"),
            ("echo a#b", "echo a#b"),
        ):
            with self.subTest(command=command):
                self.assertEqual(self.strip(command), stripped)


# For the replay test: every check passes and the head has a verdict unless
# FAKE_VERDICT says otherwise; FAKE_THREAD is an unresolved thread.
FAKE_GH = f"""#!/bin/sh
case "$*" in
  "api repos/"*"/pulls/"*) echo "$*" >> "$GH_LOG"; echo "https://github.com/o/r/pull/1 feature {SHA}" ;;
  "api graphql"*) [ -n "${{FAKE_THREAD:-}}" ] && echo "$FAKE_THREAD" ;;
  "api repos/"*"/check-runs"*) echo '{{"total_count": 0, "check_runs": []}}' ;;
  "api repos/"*"/status"*) echo '{{"statuses": []}}' ;;
  "api repos/"*) echo "${{FAKE_VERDICT-{VERDICT}}}" ;;
esac
exit 0
"""


@unittest.skipUnless(
    os.name != "nt"
    and shutil.which("sh")
    and shutil.which("jq")
    and shutil.which("git"),
    "needs sh, jq, and git",
)
class ReplayIsReadOnlyTests(unittest.TestCase):
    """The replay script runs the hook on real transcript commands; it must
    never act on the directories those commands ran in."""

    def test_replay_leaves_a_real_worktree_in_place(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
        main, wt = root / "main", root / "wt"
        for args in (
            ["init", "-q", "--bare", str(root / "remote.git")],
            ["clone", "-q", str(root / "remote.git"), str(main)],
            ["-C", str(main), "commit", "-q", "--allow-empty", "-m", "base"],
            ["-C", str(main), "push", "-q", "origin", "HEAD"],
            ["-C", str(main), "branch", "feature"],
            ["-C", str(main), "push", "-q", "origin", "feature"],
            ["-C", str(main), "remote", "add", "github", "https://github.com/o/r"],
            ["-C", str(main), "worktree", "add", "-q", str(wt), "feature"],
        ):
            subprocess.run([*git, *args], check=True, capture_output=True)
        home = root / "home"
        transcript = home / ".claude/projects/p/t.jsonl"
        transcript.parent.mkdir(parents=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        transcript.write_text(
            "".join(
                json.dumps(
                    {
                        "timestamp": stamp,
                        "cwd": str(main),
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "name": "Bash",
                                    "input": {"command": command},
                                }
                            ]
                        },
                    }
                )
                + "\n"
                for command in (
                    "gh pr merge 7 --merge -d",
                    "gh pr merge 7 -R o/r --merge --delete-branch",
                )
            )
        )
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GH_LOG": str(root / "gh.log"),
        }
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tests/replay_pr_merge_gate.py"), "--days", "1"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 Bash calls", result.stdout)
        self.assertTrue(wt.exists(), result.stdout + result.stderr)
        listed = subprocess.run(
            ["git", "-C", str(main), "worktree", "list"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertIn(wt.as_posix(), listed)


if __name__ == "__main__":
    unittest.main()

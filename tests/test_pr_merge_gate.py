"""pr-merge-gate.sh: the three merge conditions, the strict command form, and
freeing the head branch's worktree before --delete-branch."""

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

# Stands in for gh, printing what gh's -q filter would. Logs "<cwd>|<args>"
# per call. Each GH_STUB_* fact set to FAIL makes that lookup exit 1.
# `pr view` answers "<url><N> feature <head sha>" (or GH_STUB_VIEW_OUT verbatim
# when that is set), `pr checks` the non-passing
# checks (exit GH_STUB_CHECKS_RC), `api graphql` the unresolved threads, and
# `api repos/...` the comments' first lines. GH_STUB_THREADS_PAGE2 reaches only
# a caller that paginates on $endCursor.
STUB = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$GH_STUB_LOG"
printf '%s\\036' "$PWD" "$@" >> "$GH_STUB_ARGS"
printf '\\035' >> "$GH_STUB_ARGS"
case "$1 $2" in
  "pr view")
    [ "$GH_STUB_VIEW" = FAIL ] && { echo "no pull requests found" >&2; exit 1; }
    [ -n "${GH_STUB_VIEW_OUT+set}" ] && { printf '%s' "$GH_STUB_VIEW_OUT"; exit 0; }
    sel=$3; case "$sel" in ''|-*|*[!0-9]*) sel=7 ;; esac
    printf '%s%s feature %s\\n' "$GH_STUB_URL" "$sel" "$GH_STUB_SHA" ;;
  "pr checks")
    [ "$GH_STUB_CHECKS" = FAIL ] && { echo "API rate limit exceeded" >&2; exit 1; }
    printf '%s' "$GH_STUB_CHECKS"; exit "$GH_STUB_CHECKS_RC" ;;
  "api graphql")
    [ "$GH_STUB_THREADS" = FAIL ] && { echo "API rate limit exceeded" >&2; exit 1; }
    printf '%s' "$GH_STUB_THREADS"
    case "$*" in *--paginate*'$endCursor'*) printf '%s' "$GH_STUB_THREADS_PAGE2" ;; esac ;;
  "api repos/"*)
    [ "$GH_STUB_COMMENTS" = FAIL ] && { echo "HTTP 502" >&2; exit 1; }
    printf '%s' "$GH_STUB_COMMENTS" ;;
esac
"""

# Each of these must be refused while a protected list is set. Every fact
# holds for the PR they name, so only the form decides.
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
# refused while a protected list is set.
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
        protected=None,
        view="",
        checks="",
        checks_rc=0,
        threads="",
        threads_page2="",
        comments=VERDICT,
        extra=None,
        path=None,
        view_out=None,
    ):
        # Defaults: every fact holds. extra: optional "cwd" and "description"
        # for the payload, "raw" text sent instead of the JSON payload, or
        # "proc_cwd", the directory the hook process itself starts in.
        # view_out: the exact text `gh pr view` prints.
        extra = extra or {}
        env = {
            **os.environ,
            "PATH": path or f"{self.dir}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_LOG": str(self.log),
            "GH_STUB_ARGS": str(self.args_log),
            "GH_STUB_URL": URL,
            "GH_STUB_SHA": SHA,
            "GH_STUB_VIEW": view,
            "GH_STUB_CHECKS": checks,
            "GH_STUB_CHECKS_RC": str(checks_rc),
            "GH_STUB_THREADS": threads,
            "GH_STUB_THREADS_PAGE2": threads_page2,
            "GH_STUB_COMMENTS": comments,
        }
        for key in ("PR_MERGE_GATE_PROTECTED_BASES", "GH_REPO", "GH_STUB_VIEW_OUT"):
            env.pop(key, None)
        if protected is not None:
            env["PR_MERGE_GATE_PROTECTED_BASES"] = protected
        if view_out is not None:
            env["GH_STUB_VIEW_OUT"] = view_out
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
        return [c for c in self.gh_calls() if "|pr view" in c]

    def gh_argvs(self):
        """Every gh call as its full argument list, cwd first."""
        if not self.args_log.exists():
            return []
        return [
            c.split("\036")[:-1] for c in self.args_log.read_text().split("\035") if c
        ]

    # The three conditions, with and without the strict form.

    def test_all_conditions_hold_allows_a_merge_into_main(self):
        for protected in (None, "main"):
            with self.subTest(protected=protected):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", protected=protected)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_merge_must_pin_the_checked_head(self):
        for protected in (None, "main"):
            with self.subTest(protected=protected):
                missing = self.run_hook(f"gh pr merge 8 -R {REPO}", protected=protected)
                self.assertEqual(missing.returncode, 2, missing.stderr)
                self.assertIn("doesn't pin the head commit", missing.stderr)
                self.assertIn(f"--match-head-commit {SHA}", missing.stderr)
                stale = self.run_hook(
                    f"gh pr merge 8 -R {REPO} --match-head-commit {OLD_SHA}",
                    protected=protected,
                )
                self.assertEqual(stale.returncode, 2, stale.stderr)
                self.assertIn(f"pins --match-head-commit {OLD_SHA}", stale.stderr)
                self.assertIn(f"\n  --match-head-commit {SHA}\n", stale.stderr)
        # The refusal prints the exact command to run.
        strict = self.run_hook(f"cd /tmp && gh pr merge 8 -R {REPO} --squash", protected="main")
        self.assertIn(f"\n  cd /tmp && gh pr merge 8 -R {REPO} --squash{PIN}\n", strict.stderr)
        loose = self.run_hook("gh pr merge 8 --squash")
        self.assertIn(f"\n  gh pr merge 8 -R {REPO}{PIN} (plus your other flags)\n", loose.stderr)
        ok = self.run_hook(f"gh pr merge 8 --squash --match-head-commit={SHA}")
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_each_condition_failing_alone_is_refused(self):
        cases = {
            "pending check": (
                {"checks": "  ci (pending)\n"},
                "checks that are pending or failed",
            ),
            "failed check": (
                {"checks": "  ci (fail)\n"},
                "checks that are pending or failed",
            ),
            "unresolved thread": (
                {"threads": "T1\tf.py:3"},
                "unresolved review threads",
            ),
            "no verdict": ({"comments": "Looks good\n"}, "no blind-review verdict"),
        }
        for protected in (None, "main"):
            for name, (facts, why) in cases.items():
                with self.subTest(protected=protected, case=name):
                    result = self.run_hook(
                        f"gh pr merge 8 -R {REPO}", protected=protected, **facts
                    )
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

    def test_no_checks_at_all_passes(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}{PIN}",
            protected="main",
            checks="no checks reported on the 'feature' branch\n",
            checks_rc=1,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unreadable_facts_are_refused(self):
        cases = {
            "pr view": ({"view": "FAIL"}, "couldn't read the head commit"),
            "checks": ({"checks": "FAIL"}, "couldn't read the checks"),
            "checks, other error": (
                {"checks": "HTTP 502\n", "checks_rc": 1},
                "couldn't read the checks",
            ),
            "checks, pending exit": (
                {"checks": "", "checks_rc": 8},
                "couldn't read the checks",
            ),
            "threads": ({"threads": "FAIL"}, "couldn't read the review threads"),
            "comments": ({"comments": "FAIL"}, "couldn't read the comments"),
        }
        for protected in (None, "main"):
            for name, (facts, why) in cases.items():
                with self.subTest(protected=protected, case=name):
                    result = self.run_hook(
                        f"gh pr merge 8 -R {REPO}", protected=protected, **facts
                    )
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
        for protected in (None, "main"):
            for name, out in cases.items():
                with self.subTest(protected=protected, case=name):
                    result = self.run_hook(
                        f"gh pr merge 8 -R {REPO}{PIN}", protected=protected, view_out=out
                    )
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
        for protected in (None, "main"):
            with self.subTest(protected=protected):
                result = self.run_hook(
                    f"gh pr merge 8 -R {REPO}{PIN}",
                    protected=protected,
                    comments="",
                    extra={"cwd": start, "proc_cwd": start},
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("no blind-review verdict", result.stderr)
                self.assertFalse(marker.exists(), "a module from the working directory ran")

    def test_missing_jq_refuses_merges(self):
        tools = self.dir / "tools"
        tools.mkdir()
        for name in ("cat", "python3", "head", "tr", "cut", "grep", "sed", "awk", "git"):
            found = shutil.which(name)
            if found:
                (tools / name).symlink_to(found)
        path = f"{self.dir}{os.pathsep}{tools}"
        for protected in (None, "main"):
            with self.subTest(protected=protected):
                result = self.run_hook(
                    f"gh pr merge 8 -R {REPO}", protected=protected, path=path
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("jq is missing", result.stderr)
                self.assertEqual(
                    self.run_hook("ls -la", protected=protected, path=path).returncode,
                    0,
                )

    def test_threads_past_the_first_page_are_read(self):
        result = self.run_hook(
            f"gh pr merge 8 -R {REPO}", protected="main", threads_page2="T101\tf.py:9"
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("T101", result.stderr)

    def test_merges_into_other_bases_meet_the_same_conditions(self):
        # The hook never looks the base up: the conditions apply to any PR.
        for protected in (None, "main", "main release"):
            with self.subTest(protected=protected):
                self.log.unlink(missing_ok=True)
                ok = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", protected=protected)
                self.assertEqual(ok.returncode, 0, ok.stderr)
                self.assertFalse(any("baseRefName" in c for c in self.gh_calls()))
                refused = self.run_hook(
                    f"gh pr merge 8 -R {REPO}", protected=protected, comments=""
                )
                self.assertEqual(refused.returncode, 2, refused.stderr)

    def test_gh_calls(self):
        self.run_hook("gh pr merge 7 -R o/r")
        calls = self.gh_argvs()
        self.assertEqual([c[0] for c in calls], [str(self.dir)] * 4)
        view, checks, threads, comments = (c[1:] for c in calls)
        self.assertEqual(
            view,
            ["pr", "view", "7", "-R", "o/r", "--json", "url,headRefName,headRefOid",
             "-q", '.url + " " + .headRefName + " " + .headRefOid'],
        )  # fmt: skip
        # Later calls name the repo from the PR's url.
        self.assertEqual(checks[:7], ["pr", "checks", "7", "-R", REPO, "--json", "name,bucket"])
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
        filters = [c[c.index("-q") + 1] for c in self.gh_argvs()[1:]]
        checks, threads, comments = filters

        def jq(expression, data):
            return subprocess.run(
                ["jq", "-r", expression],
                input=json.dumps(data),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()

        buckets = ["pass", "skipping", "pending", "fail", "cancel"]
        self.assertEqual(
            jq(checks, [{"name": b, "bucket": b} for b in buckets]),
            ["  pending (pending)", "  fail (fail)", "  cancel (cancel)"],
        )
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

    # Protected list set: the form gate.

    def test_every_bypass_is_refused(self):
        for name, command in BYPASSES.items():
            with self.subTest(name):
                result = self.run_hook(command, protected="main")
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
                self.assertIn("one merge form the gate checks", result.stderr)
                self.assertIn("rephrase it", result.stderr)
        self.assertEqual(self.gh_calls(), [], "refused forms never reach gh")

    def test_canonical_forms_are_allowed(self):
        for command in CANONICAL:
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(command, protected="main")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.pr_lookups()[0],
                    f"{self.dir}|pr view 8 -R {REPO} --json url,headRefName,headRefOid "
                    '-q .url + " " + .headRefName + " " + .headRefOid',
                )

    def test_canonical_forms_still_meet_the_conditions(self):
        # Including spellings that don't contain the literal "gh pr merge".
        for command in CANONICAL:
            with self.subTest(command=command):
                result = self.run_hook(command, protected="main", comments="")
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
                result = self.run_hook(command, protected="main")
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.gh_calls(), [])

    def test_obfuscated_merges_are_refused(self):
        for command in OBFUSCATED:
            with self.subTest(command=command):
                result = self.run_hook(command, protected="main")
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
                loose = self.run_hook("gh pr merge 7", protected="main", extra=extra)
                self.assertEqual(loose.returncode, 2, loose.stderr)
                self.assertIn("one merge form the gate checks", loose.stderr)
                unreviewed = self.run_hook(
                    f"gh pr merge 7 -R {REPO}", protected="main", comments="", extra=extra
                )
                self.assertEqual(unreviewed.returncode, 2, unreviewed.stderr)
                self.assertIn("no blind-review verdict", unreviewed.stderr)
                ok = self.run_hook(f"gh pr merge 7 -R {REPO}{PIN}", protected="main", extra=extra)
                self.assertEqual(ok.returncode, 0, ok.stderr)
                harmless = self.run_hook("ls -la", protected="main", extra=extra)
                self.assertEqual(harmless.returncode, 0, harmless.stderr)

    def test_harmless_mentions_are_allowed(self):
        for protected in (None, "main"):
            for command in HARMLESS:
                with self.subTest(protected=protected, command=command):
                    result = self.run_hook(command, protected=protected, comments="")
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
                result = self.run_hook(command, protected="main")
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")

    def test_list_accepts_commas_semicolons_and_spaces(self):
        for protected in (
            "main,release",
            "main;release",
            " main , release ",
            "main\trelease",
        ):
            with self.subTest(protected=protected):
                loose = self.run_hook("gh pr merge 8", protected=protected)
                self.assertEqual(loose.returncode, 2, loose.stderr)
                self.assertIn("one merge form the gate checks", loose.stderr)
                ok = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", protected=protected)
                self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_list_keeps_names_git_allows(self):
        for name in ("release+2", "user@host", "fix#12"):
            for protected in (f"main {name}", f"main,{name}", f"{name};main"):
                with self.subTest(protected=protected):
                    result = self.run_hook(f"gh pr merge 8 -R {REPO}{PIN}", protected=protected)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_list_without_branch_names_fails_closed(self):
        for protected in (",", " ; ", "   "):
            with self.subTest(protected=protected):
                result = self.run_hook(f"gh pr merge 8 -R {REPO}", protected=protected)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("PR_MERGE_GATE_PROTECTED_BASES", result.stderr)
                self.assertIn("no valid branch name", result.stderr)
                self.assertEqual(
                    self.run_hook("ls -la", protected=protected).returncode, 0
                )

    def test_list_with_an_invalid_name_fails_closed(self):
        for bad in (
            "-x",
            "a..b",
            "a~1",
            "a^",
            "a:b",
            "a?",
            "a*",
            "a[1]",
            "a\\b",
            "a@{1}",
            "a\x01b",
        ):
            with self.subTest(bad=bad):
                result = self.run_hook(
                    f"gh pr merge 8 -R {REPO}", protected=f"main,{bad}"
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("PR_MERGE_GATE_PROTECTED_BASES", result.stderr)
                self.assertIn(repr(bad), result.stderr)
                self.assertEqual(
                    self.run_hook("ls -la", protected=f"main,{bad}").returncode, 0
                )

    def test_canonical_number_and_repo_are_ascii(self):
        for command in (
            f"gh pr merge ٨ -R {REPO}",  # Arabic-Indic eight
            f"gh pr merge ８ -R {REPO}",  # fullwidth eight
            f"gh pr merge ² -R {REPO}",  # superscript two
            "gh pr merge 8 -R о-org/example-repo",  # Cyrillic o
            "gh pr merge 8 -R example-org/répo",
        ):
            with self.subTest(command=command):
                result = self.run_hook(command, protected="main")
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
                result = self.run_hook("", protected="main", extra={"raw": raw})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("PR_MERGE_GATE_PROTECTED_BASES", result.stderr)
        for raw in (
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x"}}),
            json.dumps({"tool_name": "Edit"}),
        ):
            with self.subTest(raw=raw):
                result = self.run_hook("", protected="main", extra={"raw": raw})
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_red_team_cases_never_merge(self):
        # No PR has a verdict here, so any merge reaching gh got past the gate.
        for name, command in RED_TEAM.items():
            with self.subTest(name):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(command, protected="main", comments="")
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

    # Empty list: any command containing "gh pr merge" as words is checked.

    def test_default_checks_merges_in_any_form(self):
        for command in (f"gh pr merge 7{PIN}", f"gh pr merge 7{PIN} && gh pr merge 8"):
            for protected in (None, ""):
                with self.subTest(command=command, protected=protected):
                    self.assertEqual(
                        self.run_hook(command, protected=protected).returncode, 0
                    )
                    refused = self.run_hook(command, protected=protected, comments="")
                    self.assertEqual(refused.returncode, 2)

    def test_default_checks_merges_inside_quoted_shell(self):
        # A merge inside quoted text for a shell is checked, never skipped.
        for command in (
            'bash -c "gh pr merge 7 --squash{}"',
            "echo 'gh pr merge 7 -R o/r{}' | sh",
            "sh -c 'gh pr merge 7 -d{}' && echo done",
        ):
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                refused = self.run_hook(command.format(PIN), comments="")
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertIn("no blind-review verdict", refused.stderr)
                self.assertIn("|pr view 7 ", self.pr_lookups()[0])
                unpinned = self.run_hook(command.format(""))
                self.assertEqual(unpinned.returncode, 2, unpinned.stderr)
                self.assertIn("doesn't pin the head commit", unpinned.stderr)
                ok = self.run_hook(command.format(PIN))
                self.assertEqual(ok.returncode, 0, ok.stderr)
        # No PR number to read: refused rather than passed.
        result = self.run_hook("bash -c 'gh pr merge --squash'")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("inside quoted text", result.stderr)

    def test_default_ignores_other_commands(self):
        self.assertEqual(self.run_hook("gh pr view 7").returncode, 0)
        self.assertEqual(self.gh_calls(), [])


# The gate's Python runs gh through subprocess, which on Windows finds the
# real gh.exe rather than the sh fake, as for PrMergeGateTests.
POSIX_GH = unittest.skipIf(os.name == "nt", "needs the sh fake gh")


# Every check passes and the head has a verdict unless FAKE_VERDICT says
# otherwise; FAKE_THREAD is an unresolved thread.
FAKE_GH = f"""#!/bin/sh
case "$*" in
  "pr view"*) echo "$*" >> "$GH_LOG"; echo "https://github.com/o/r/pull/1 feature {SHA}" ;;
  "api graphql"*) [ -n "${{FAKE_THREAD:-}}" ] && echo "$FAKE_THREAD" ;;
  "api repos/"*) echo "${{FAKE_VERDICT-{VERDICT}}}" ;;
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
        self.env.pop("PR_MERGE_GATE_PROTECTED_BASES", None)
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
        # Every merge here pins the fake's head, so only the worktree decides:
        # right after `gh pr merge` without the list, at the end under it,
        # where the strict form wants the number and -R first.
        if self.env.get("PR_MERGE_GATE_PROTECTED_BASES"):
            command += PIN
        else:
            command = command.replace("gh pr merge", "gh pr merge" + PIN, 1)
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
        # jq -r on Windows writes text mode, so \n arrives as \r\n and \r\n as \r\r\n.
        for newline in ["\n", "\r\n", "\r\r\n"]:
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

    def test_missing_verdict_blocks_and_leaves_worktree(self):
        self.env["FAKE_VERDICT"] = f"Blind review: pass {OLD_SHA}"
        result = self.hook()
        self.assertEqual(result.returncode, 2)
        self.assertIn("no blind-review verdict", result.stderr)
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

    # Protected list set together with a worktree: the canonical form names
    # its repo with -R, so gh never deletes a local branch and the hook never
    # removes a worktree, whatever the remotes.

    def protect(self, remote="https://github.com/o/r.git"):
        self.env["PR_MERGE_GATE_PROTECTED_BASES"] = "main"
        if remote:
            self.git(self.main, "remote", "add", "github", remote)

    @POSIX_GH
    def test_protected_list_never_removes_a_worktree(self):
        for remote in (
            "https://github.com/o/r.git",
            "git@github.com:O/R.git",
            "https://example.invalid/o/r.git",
            "",
        ):
            for command in (
                "gh pr merge 1 -R o/r --squash --delete-branch",
                "gh pr merge 1 -R o/r --merge --delete-branch --subject x",
                f"cd '{self.main}' && gh pr merge 1 -R o/r --delete-branch",
            ):
                with self.subTest(remote=remote, command=command):
                    self.env.pop("PR_MERGE_GATE_PROTECTED_BASES", None)
                    subprocess.run(
                        ["git", "-C", str(self.main), "remote", "remove", "github"],
                        capture_output=True,
                        check=False,
                    )
                    self.protect(remote)
                    result = self.hook(command)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("removed", result.stderr)
                    self.assertTrue(self.wt.exists())
                    self.assertIn(
                        self.wt.as_posix(),
                        self.git(self.main, "worktree", "list"),
                    )

    @POSIX_GH
    def test_protected_list_fork_pr_with_a_local_branch_name_keeps_worktree(self):
        # An outside fork's PR whose head branch is also called "feature": the
        # local worktree on feature has nothing to do with it.
        self.protect()
        result = self.hook("gh pr merge 1 -R o/r --squash --delete-branch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())

    @POSIX_GH
    def test_protected_list_never_removes_before_a_refusal(self):
        self.protect()
        for command, env, why in [
            ("gh pr merge 1 --squash -d", {}, "one merge form"),
            (
                "gh pr merge 1 -R o/r --delete-branch",
                {"FAKE_VERDICT": ""},
                "no blind-review verdict",
            ),
            (
                "gh pr merge 1 -R o/r --delete-branch",
                {"FAKE_THREAD": "T1\tf.py:1\tbot\thttps://example.invalid/c1"},
                "unresolved review threads",
            ),
        ]:
            with self.subTest(command=command, why=why):
                self.env.update(env)
                result = self.hook(command)
                for key in env:
                    self.env.pop(key)
                self.assertEqual(result.returncode, 2)
                self.assertIn(why, result.stderr)
                self.assertTrue(self.wt.exists())

    @POSIX_GH
    def test_protected_list_keeps_dirty_worktree(self):
        self.protect()
        (self.wt / "notes.txt").write_text("keep me")
        result = self.hook("gh pr merge 1 -R o/r --merge --delete-branch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.wt / "notes.txt").exists())

    @POSIX_GH
    def test_protected_list_canonical_without_delete_leaves_worktree(self):
        self.protect()
        result = self.hook("gh pr merge 1 -R o/r --squash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.wt.exists())


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
        for protected in ("", "main"):
            with self.subTest(protected=protected):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(ROOT / "tests/replay_pr_merge_gate.py"),
                        "--days",
                        "1",
                        "--protected",
                        protected,
                    ],
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

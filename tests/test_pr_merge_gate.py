"""pr-merge-gate.sh: protected bases, and freeing the head branch's worktree
before --delete-branch."""

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

# Stands in for gh. Logs "<cwd>|<args>" per call. `pr view <N>` answers with
# base GH_STUB_BASE_<N> (else GH_STUB_BASE; FAIL exits 1), and `api graphql`
# with the fixture unresolved threads.
STUB = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$GH_STUB_LOG"
printf '%s\\036' "$PWD" "$@" >> "$GH_STUB_ARGS"
printf '\\035' >> "$GH_STUB_ARGS"
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
    f"gh pr merge 8 -R {REPO}",
    f"gh pr merge 8 -R {REPO} --merge --delete-branch",
    f"gh pr merge 8 -R {REPO} --squash --subject 'Ship it, #12 now' --body \"a b\"",
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
        self.args_log = self.dir / "gh.args"

    def run_hook(
        self,
        command,
        base="main",
        protected=None,
        threads="",
        extra=None,
        **bases,
    ):
        # extra: optional "cwd" and "description" for the payload.
        extra = extra or {}
        env = {
            **os.environ,
            "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
            "GH_STUB_LOG": str(self.log),
            "GH_STUB_ARGS": str(self.args_log),
            "GH_STUB_URL": URL,
            "GH_STUB_BASE": base,
            "GH_STUB_THREADS": threads,
            **{f"GH_STUB_BASE_{n.lstrip('_')}": b for n, b in bases.items()},
        }
        for key in ("PR_MERGE_GATE_PROTECTED_BASES", "GH_REPO"):
            env.pop(key, None)
        if protected is not None:
            env["PR_MERGE_GATE_PROTECTED_BASES"] = protected
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

    def test_obfuscated_merges_are_refused(self):
        for command in OBFUSCATED:
            with self.subTest(command=command):
                result = self.run_hook(command, base="dev", protected="main")
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
                self.assertIn("one merge form the gate checks", result.stderr)
        self.assertEqual(self.base_lookups(), [])

    def test_verdict_depends_only_on_the_command(self):
        # Text in other fields, such as an unclosed ${, must not hide the
        # command from the check.
        weird = self.dir / "a ${b"
        weird.mkdir()
        for extra in ({}, {"description": "see ${HOME"}, {"cwd": weird}):
            with self.subTest(extra=extra):
                into_main = self.run_hook(
                    f"gh pr merge 7 -R {REPO}", protected="main", extra=extra
                )
                self.assertEqual(into_main.returncode, 2, into_main.stderr)
                self.assertIn("protected base branch", into_main.stderr)
                into_dev = self.run_hook(
                    f"gh pr merge 7 -R {REPO}",
                    base="dev",
                    protected="main",
                    extra=extra,
                )
                self.assertEqual(into_dev.returncode, 0, into_dev.stderr)
                harmless = self.run_hook("ls -la", protected="main", extra=extra)
                self.assertEqual(harmless.returncode, 0, harmless.stderr)

    def test_harmless_mentions_are_allowed(self):
        for command in HARMLESS:
            with self.subTest(command=command):
                result = self.run_hook(command, protected="main")
                self.assertEqual(result.returncode, 0, result.stderr)
        # Text containing "gh pr merge" still reaches the original thread
        # check, as without a list; the gate itself looks nothing up.
        self.assertEqual(self.base_lookups(), [])

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
                result = self.run_hook(command, base="dev", protected="main")
                self.assertEqual(result.returncode, 2, f"{command!r} was allowed")

    def test_list_accepts_commas_semicolons_and_spaces(self):
        cmd = f"gh pr merge 8 -R {REPO}"
        for protected in (
            "main,release",
            "main;release",
            " main , release ",
            "main\trelease",
        ):
            with self.subTest(protected=protected):
                result = self.run_hook(cmd, base="release", protected=protected)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("protected base branch", result.stderr)
                self.assertEqual(
                    self.run_hook(cmd, base="dev", protected=protected).returncode,
                    0,
                )

    def test_list_without_branch_names_fails_closed(self):
        for protected in (",", " ; ", "   ", "-x"):
            with self.subTest(protected=protected):
                result = self.run_hook(
                    f"gh pr merge 8 -R {REPO}", base="dev", protected=protected
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("PR_MERGE_GATE_PROTECTED_BASES", result.stderr)
                self.assertIn("no valid branch name", result.stderr)
                self.assertEqual(
                    self.run_hook("ls -la", protected=protected).returncode, 0
                )

    def test_red_team_cases_never_merge(self):
        # Every PR's base is main here, so any merge reaching gh is protected.
        for name, command in RED_TEAM.items():
            with self.subTest(name):
                self.log.unlink(missing_ok=True)
                result = self.run_hook(command, base="main", _8="dev", protected="main")
                if result.returncode == 2:
                    continue
                subprocess.run(
                    ["bash", "-c", command],
                    cwd=self.dir,
                    env={
                        **os.environ,
                        "PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
                        "GH_STUB_LOG": str(self.log),
                        "GH_STUB_URL": URL,
                        "GH_STUB_BASE": "main",
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
        # Every call from any directory, each as its full argument list.
        calls = [
            c.split("\036")[:-1] for c in self.args_log.read_text().split("\035") if c
        ]
        query = (
            "\n  query($owner:String!,$name:String!,$num:Int!){ repository("
            "owner:$owner,name:$name){\n    pullRequest(number:$num){ "
            "reviewThreads(first:100){ nodes{\n      id isResolved path line "
            "comments(first:1){ nodes{ author{login} body url } } } } } } }"
        )
        jq = (
            ".data.repository.pullRequest.reviewThreads.nodes[] | "
            "select(.isResolved|not)\n      | "
            r'"\(.id)\t\(.path):\(.line // "?")\t\(.comments.nodes[0].author.login)'
            r"\t\(.comments.nodes[0].url)\n    \(.comments.nodes[0].body | "
            r'gsub("\n";" ") | .[:160])"'
        )
        self.assertEqual(
            calls,
            [
                [
                    str(self.dir),
                    "pr",
                    "view",
                    "7",
                    "-R",
                    "o/r",
                    "--json",
                    "url,headRefName",
                    "-q",
                    '.url + " " + .headRefName',
                ],
                [
                    str(self.dir),
                    "api",
                    "graphql",
                    "-f",
                    "owner=example-org",
                    "-f",
                    "name=example-repo",
                    "-F",
                    "num=7",
                    "-f",
                    "query=" + query,
                    "-q",
                    jq,
                ],
            ],
        )

    def test_default_still_blocks_unresolved_threads(self):
        result = self.run_hook("gh pr merge 7", base="dev", threads="T1\tf.py:3")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unresolved review threads", result.stderr)

    def test_default_ignores_other_commands(self):
        self.assertEqual(self.run_hook("gh pr view 7").returncode, 0)
        self.assertEqual(self.gh_calls(), [])


# The gate's Python runs gh through subprocess, which on Windows finds the
# real gh.exe rather than the sh fake, as for PrMergeGateTests.
POSIX_GH = unittest.skipIf(os.name == "nt", "needs the sh fake gh")


FAKE_GH = """#!/bin/sh
case "$*" in
  "pr view"*baseRefName*) echo "$*" >> "$GH_LOG"; echo "${FAKE_BASE:-dev} https://github.com/o/r/pull/1" ;;
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

    # Protected list set together with a worktree: the canonical form with
    # --delete-branch gets the same safe removal as the lone form above.

    def protect(self, remote="https://github.com/o/r.git"):
        self.env["PR_MERGE_GATE_PROTECTED_BASES"] = "main"
        if remote:
            self.git(self.main, "remote", "add", "github", remote)

    @POSIX_GH
    def test_protected_list_canonical_delete_removes_clean_worktree(self):
        for remote in ("https://github.com/o/r.git", "git@github.com:O/R.git"):
            with self.subTest(remote=remote):
                subprocess.run(
                    ["git", "-C", str(self.main), "remote", "remove", "github"],
                    capture_output=True,
                    check=False,
                )
                if not self.wt.exists():
                    self.git(
                        self.main, "worktree", "add", "-q", str(self.wt), "feature"
                    )
                self.protect(remote)
                result = self.hook("gh pr merge 1 -R o/r --squash --delete-branch")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("removed clean worktree", result.stderr)
                self.assertFalse(self.wt.exists())
                self.assertTrue(self.main.exists())

    @POSIX_GH
    def test_protected_list_never_removes_before_a_refusal(self):
        self.protect()
        for command, env, why in [
            ("gh pr merge 1 --squash -d", {}, "one merge form"),
            (
                "gh pr merge 1 -R o/r --delete-branch",
                {"FAKE_BASE": "main"},
                "protected base branch",
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
    def test_protected_list_blocks_unsafe_canonical_delete_with_working_steps(self):
        for name, command, remote in [
            (
                "cd prefix",
                f"cd '{self.main}' && gh pr merge 1 -R o/r --delete-branch",
                "https://github.com/o/r.git",
            ),
            (
                "other repo",
                "gh pr merge 1 -R o/r --delete-branch",
                "https://github.com/o/other.git",
            ),
            ("no github remote", "gh pr merge 1 -R o/r --delete-branch", ""),
        ]:
            with self.subTest(name):
                self.env.pop("PR_MERGE_GATE_PROTECTED_BASES", None)
                subprocess.run(
                    ["git", "-C", str(self.main), "remote", "remove", "github"],
                    capture_output=True,
                    check=False,
                )
                self.protect(remote)
                result = self.hook(command)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(
                    f"git worktree remove '{self.wt.as_posix()}'", result.stderr
                )
                self.assertIn(
                    "gh pr merge <number> -R <owner>/<repo> --delete-branch",
                    result.stderr,
                )
                self.assertNotIn("lone", result.stderr)
                self.assertTrue(self.wt.exists())
        # The steps it gives work under the gate.
        self.git(self.main, "worktree", "remove", str(self.wt))
        result = self.hook("gh pr merge 1 -R o/r --delete-branch")
        self.assertEqual(result.returncode, 0, result.stderr)

    @POSIX_GH
    def test_protected_list_keeps_dirty_worktree(self):
        self.protect()
        (self.wt / "notes.txt").write_text("keep me")
        result = self.hook("gh pr merge 1 -R o/r --merge --delete-branch")
        self.assertEqual(result.returncode, 2)
        self.assertIn("notes.txt", result.stderr)
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

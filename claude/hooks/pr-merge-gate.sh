#!/bin/sh
# PreToolUse hook (matcher: Bash). Blocks `gh pr merge` while the PR has
# unresolved review threads, so Copilot/human comments get a reply and a
# resolve before the merge lands. Exit 2 = block; anything else = allow.
#
# When PR_MERGE_GATE_PROTECTED_BASES lists base branches (space-separated,
# default empty), merges into them stay with the owner: any command containing
# the words `gh` and `merge` is refused unless it is exactly
#   [cd <path> && ] gh pr merge <number> -R <owner>/<repo> [allowed flags]
# and that PR's base is not listed. Parsing arbitrary shell can't be made
# complete, so everything else that mentions both words is refused.
set -uf  # -f: the merge command is word-split below and must not glob
input=$(cat)
if [ -n "${PR_MERGE_GATE_PROTECTED_BASES:-}" ]; then
  # Cheap pre-filter: strip quotes, backslashes and $ expansions, then look for
  # both words in any case, so g''h, mer""ge, g\h and GH all reach Python.
  bare=$(printf '%s' "$input" | sed -e 's/\${[^}]*}//g' -e 's/\$[A-Za-z0-9_]*//g' -e "s/[\"'\\\\\`]//g")
  case "$bare" in *[gG][hH]*) case "$bare" in *[mM][eE][rR][gG][eE]*)
    command -v python3 >/dev/null 2>&1 || {
      echo "BLOCKED: python3 is missing, so pr-merge-gate can't check this command for a merge." >&2
      exit 2
    }
    PR_MERGE_GATE_INPUT=$input python3 - <<'PY' || exit 2
import json
import os
import re
import shlex
import subprocess
import sys

protected = os.environ["PR_MERGE_GATE_PROTECTED_BASES"].split()
FORM = ("gh pr merge <number> -R <owner>/<repo> [--merge|--squash|--rebase] "
        "[--delete-branch] [--subject '<text>'] [--body '<text>']")
# No --auto: the PR's base can change after auto-merge is armed.
SWITCHES = {"--merge", "--squash", "--rebase", "--delete-branch"}


def possible_merge(text):
    if not re.search(r"\bgh\b", text, re.I):
        return False
    return bool(re.search(r"\bmerge\b", text, re.I)
                or (re.search(r"\bapi\b", text, re.I) and re.search("merge", text, re.I)))


def refuse(text):
    print("BLOCKED: " + text, file=sys.stderr)
    sys.exit(2)


def noncanonical(why):
    refuse("PR_MERGE_GATE_PROTECTED_BASES is set, and this command contains the "
           f"words gh and merge without being the one merge form the gate checks "
           f"({why}). To merge, run only:\n  [cd <path> && ] {FORM}\n"
           "If the command only mentions those words (echo, grep, a heredoc, a "
           "commit message), rephrase it so it doesn't contain both, or write "
           "the text with a file tool.")


try:
    cmd = json.loads(os.environ["PR_MERGE_GATE_INPUT"])["tool_input"]["command"]
except (ValueError, KeyError, TypeError):
    sys.exit(0)  # not a shell command
if not isinstance(cmd, str):
    noncanonical("the command is not a single string")
bare = re.sub(r"\$\{[^}]*\}|\$\w+|[\"'\\`]", "", cmd)
if not (possible_merge(cmd) or possible_merge(bare)):
    sys.exit(0)
if re.search(r"[\n\r\\`$;|<>(){}*?\[\]~!]", cmd):
    noncanonical("it has a newline, backslash, backtick, or one of $ ; | < > ( ) { } * ? [ ] ~ !")
lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
lexer.whitespace_split = True
lexer.commenters = ""
try:
    words = list(lexer)
except ValueError:
    noncanonical("unbalanced quotes")
if words[:1] == ["cd"]:
    if len(words) < 3 or words[2] != "&&" or cmd.count("&") != 2:
        noncanonical("only `cd <path> && ` may come before the merge")
    words = words[3:]
elif "&" in cmd:
    noncanonical("it has an &")
if (words[:3] != ["gh", "pr", "merge"] or len(words) < 6
        or not words[3].isdigit() or words[4] != "-R"
        or not re.fullmatch(r"[\w.-]+/[\w.-]+", words[5])):
    noncanonical("it must start with gh pr merge <number> -R <owner>/<repo>")
num, repo, rest = words[3], words[5], words[6:]
if any(w.startswith(("-R", "--repo")) for w in rest):
    noncanonical("-R may appear only once, as -R <owner>/<repo> after the number")
while rest:
    word = rest.pop(0)
    if word in SWITCHES:
        continue
    if word in ("--subject", "--body") and rest:
        rest.pop(0)
        continue
    noncanonical(f"{word!r} is not an allowed flag here")

# -R names the repo, so the lookup doesn't depend on the working directory.
view = ["gh", "pr", "view", num, "-R", repo, "--json", "baseRefName,url",
        "-q", '.baseRefName + " " + .url']
try:
    result = subprocess.run(view, cwd="/", capture_output=True, text=True,
                            timeout=20, check=False)
    base, _, url = result.stdout.strip().partition(" ")
    failed = result.returncode or not base
    reason = result.stderr.strip() or "no base branch returned"
except (OSError, subprocess.TimeoutExpired) as error:
    failed, reason = True, str(error)
if failed:
    refuse(f"couldn't read the base branch of {repo}#{num} ({reason}), and "
           "PR_MERGE_GATE_PROTECTED_BASES is set, so the merge is refused.")
if base in protected:
    refuse(f"{repo}#{num} merges into {base}, a protected base branch "
           "(PR_MERGE_GATE_PROTECTED_BASES). Agents don't merge into it. Hand "
           "the PR to the owner as a link plus the merge command:\n"
           f"  {url}\n  gh pr merge {num} -R {repo} --merge --delete-branch")
PY
  ;; esac ;; esac
fi
command -v jq >/dev/null 2>&1 || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
case "$cmd" in *"gh pr merge"*) ;; *) exit 0 ;; esac

# Parse the merge command: PR number/url (first bare arg) and -R/--repo.
set -- $(printf '%s' "$cmd" | sed -n 's/.*gh pr merge//p')
pr=""; repo=""
while [ $# -gt 0 ]; do
  case "$1" in
    -R|--repo) repo=${2:-}; [ $# -gt 1 ] && shift ;;
    --repo=*) repo=${1#--repo=} ;;
    -*) ;;
    *) [ -z "$pr" ] && pr=$1 ;;
  esac
  shift
done
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] && cd "$cwd" 2>/dev/null
repoflag=""; [ -n "$repo" ] && repoflag="-R $repo"
# The PR url names the base repo even when the head is a fork.
url=$(gh pr view ${pr:+"$pr"} $repoflag --json url -q .url 2>/dev/null) || exit 0
owner=$(printf '%s' "$url" | cut -d/ -f4)
name=$(printf '%s' "$url" | cut -d/ -f5)
num=$(printf '%s' "$url" | cut -d/ -f7)

threads=$(gh api graphql -f owner="$owner" -f name="$name" -F num="$num" -f query='
  query($owner:String!,$name:String!,$num:Int!){ repository(owner:$owner,name:$name){
    pullRequest(number:$num){ reviewThreads(first:100){ nodes{
      id isResolved path line comments(first:1){ nodes{ author{login} body url } } } } } } }' \
  -q '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)
      | "\(.id)\t\(.path):\(.line // "?")\t\(.comments.nodes[0].author.login)\t\(.comments.nodes[0].url)\n    \(.comments.nodes[0].body | gsub("\n";" ") | .[:160])"' 2>/dev/null) || exit 0
[ -z "$threads" ] && exit 0

cat >&2 <<MSG
BLOCKED: $owner/$name#$num has unresolved review threads. Read each one, then
reply on the thread (accept + name the fixing commit, or say why you reject it)
and resolve it. Reply with:
  gh api repos/$owner/$name/pulls/$num/comments -f body='...' -F in_reply_to=<comment id>
Resolve with:
  gh api graphql -f query='mutation{resolveReviewThread(input:{threadId:"<thread id>"}){thread{isResolved}}}'
Unresolved threads (thread id, location, author, url):
$threads
MSG
exit 2

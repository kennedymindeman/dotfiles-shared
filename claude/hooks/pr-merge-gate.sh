#!/bin/sh
# PreToolUse hook (matcher: Bash). Blocks `gh pr merge` while the PR has
# unresolved review threads, so Copilot/human comments get a reply and a
# resolve before the merge lands. With --delete-branch it also removes a clean
# linked worktree holding the head branch, since gh can't delete a branch that
# is checked out (dotfiles#125). Exit 2 = block; anything else = allow.
set -uf  # -f: $repoflag and $cmd are word-split below and must not glob
command -v jq >/dev/null 2>&1 || exit 0
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
case "$cmd" in *"gh pr merge"*) ;; *) exit 0 ;; esac

# Tokenize with shell rules and parse the merge command up to the next
# separator, redirection, newline, or comment: PR number/url (first bare arg),
# -R/--repo, -d/--delete-branch, and --auto/--disable-auto. delete is 1 for an
# exact -d/--delete-branch and ? for a valued form like -d=false. safe=1 only
# for a lone `gh pr merge <number> ...` with exact delete and nothing that moves
# the cwd, repo, or timing; only then may the hook remove a worktree. A fd
# number before a redirect (2>&1) is dropped so it isn't read as the PR.
pr=""; repo=""; delete=""; auto=""; safe=""
if out=$(python3 -c '
import re, shlex, sys
p = ";&|()<>\n"
c = re.sub(r"(^|\s)\d+(?=[<>])", r"\1", sys.argv[1].replace("\r", "").replace("\\\n", ""))
l = shlex.shlex(c, posix=True, punctuation_chars=p)
l.whitespace, l.whitespace_split, l.commenters = " \t\r", True, ""
t = list(l)
i = next(i for i in range(len(t)) if t[i:i + 3] == ["gh", "pr", "merge"])
v = dict(pr="", repo="", delete="", auto="", safe="")
args = iter(t[i + 3:])
for a in args:
    if set(a) <= set(p) or a.startswith("#"):
        break
    if a in ("-R", "--repo"):
        v["repo"] = next(args, "")
    elif a.startswith("--repo="):
        v["repo"] = a[7:]
    elif a in ("--body", "--subject", "--author-email", "--body-file", "--match-head-commit"):
        next(args, "")
    elif a.startswith("--delete-branch"):
        v["delete"] = "?" if "=" in a or v["delete"] == "?" else "1"
    elif a.split("=")[0] in ("--auto", "--disable-auto"):
        v["auto"] = "1"
    elif a.startswith("-") and not a.startswith("--"):
        for j, ch in enumerate(a[1:], 2):
            if ch in "RbtAF":
                val = a[j:] or next(args, "")
                if ch == "R":
                    v["repo"] = val
                break
            if ch == "d":
                v["delete"] = "?" if "=" in a or v["delete"] == "?" else "1"
    elif not a.startswith("-") and not v["pr"]:
        v["pr"] = a
if (i == 0 and v["delete"] == "1" and v["pr"].isdigit() and not v["repo"]
        and not v["auto"] and not re.search("[$`]", c)
        and not any(set(x) <= set(p) for x in t)):
    v["safe"] = "1"
print(" ".join(k + "=" + shlex.quote(x) for k, x in v.items()))
' "$cmd" 2>/dev/null); then
  eval "$out"
else
  # Unparseable or no python3: the old word-split extraction for the PR and
  # repo, and treat any delete-looking word anywhere as a possible delete.
  set -- $(printf '%s' "$cmd" | sed -n 's/.*gh pr merge//p')
  while [ $# -gt 0 ]; do
    case "$1" in
      -R|--repo) repo=${2:-}; [ $# -gt 1 ] && shift ;;
      --repo=*) repo=${1#--repo=} ;;
      -*) ;;
      *) [ -z "$pr" ] && pr=$1 ;;
    esac
    shift
  done
  for w in $cmd; do
    case "$w" in --delete-branch*) delete="?" ;; --*) ;; -*d*) delete="?" ;; esac
  done
fi
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] && { cd "$cwd" 2>/dev/null || safe=""; }
repoflag=""; [ -n "$repo" ] && repoflag="-R $repo"
# The PR url names the base repo even when the head is a fork.
info=$(gh pr view ${pr:+"$pr"} $repoflag --json url,headRefName \
  -q '.url + " " + .headRefName' 2>/dev/null) || exit 0
url=${info%% *}; head=${info#* }
owner=$(printf '%s' "$url" | cut -d/ -f4)
name=$(printf '%s' "$url" | cut -d/ -f5)
num=$(printf '%s' "$url" | cut -d/ -f7)

threads=$(gh api graphql -f owner="$owner" -f name="$name" -F num="$num" -f query='
  query($owner:String!,$name:String!,$num:Int!){ repository(owner:$owner,name:$name){
    pullRequest(number:$num){ reviewThreads(first:100){ nodes{
      id isResolved path line comments(first:1){ nodes{ author{login} body url } } } } } } }' \
  -q '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)
      | "\(.id)\t\(.path):\(.line // "?")\t\(.comments.nodes[0].author.login)\t\(.comments.nodes[0].url)\n    \(.comments.nodes[0].body | gsub("\n";" ") | .[:160])"' 2>/dev/null) || threads=""
if [ -n "$threads" ]; then
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
fi

[ -n "$delete" ] || exit 0
# The first worktree listed is the main checkout; only linked ones are removed.
wts=$(git worktree list --porcelain 2>/dev/null) || exit 0
main=$(printf '%s\n' "$wts" | sed -n '1s/^worktree //p')
wt=$(printf '%s\n' "$wts" | awk -v ref="refs/heads/$head" '
  /^worktree / { path = substr($0, 10); n++ }
  $0 == "branch " ref && n > 1 { print path }')
[ -n "$wt" ] || exit 0
# Prune is repo-wide, so it stays the user's call.
if [ ! -d "$wt" ]; then
cat >&2 <<MSG
BLOCKED: $head is registered to the worktree $wt, whose directory is gone, so
--delete-branch can't delete it. Check \`git worktree list\`, then run
\`git worktree prune\` (it prunes every stale worktree in this repo) and retry.
MSG
exit 2
fi
if [ -z "$safe" ]; then
cat >&2 <<MSG
BLOCKED: this merge may delete $head, which is checked out in the worktree
$wt. The hook only removes it for a lone \`gh pr merge <number> ... -d\`
command. If that worktree is clean and pushed, remove it first, then retry:
  git worktree remove '$wt'
MSG
exit 2
fi

why=""
case "$(pwd -P)/" in "$(cd "$wt" 2>/dev/null && pwd -P || echo "$wt")"/*)
  why="$why
  this command runs inside it; rerun gh pr merge from $main" ;;
esac
dirty=$(git -C "$wt" status --porcelain --untracked-files=all --ignored 2>&1)
[ -n "$dirty" ] && why="$why
  uncommitted changes, untracked or ignored files:
$(printf '%s\n' "$dirty" | sed 's/^/    /')"
unpushed=$(git -C "$wt" log --oneline HEAD --not --remotes 2>&1)
[ -n "$unpushed" ] && why="$why
  unpushed commits:
$(printf '%s\n' "$unpushed" | sed 's/^/    /')"
[ -z "$why" ] && err=$(git worktree remove "$wt" 2>&1) && {
  echo "pr-merge-gate: removed clean worktree $wt so --delete-branch can delete $head" >&2
  exit 0
}
[ -z "$why" ] && why="
  git worktree remove failed: $err"
cat >&2 <<MSG
BLOCKED: --delete-branch can't delete $head while it is checked out in the
worktree $wt, and that worktree was left in place:$why
Commit and push or move that work, then retry the merge.
MSG
exit 2

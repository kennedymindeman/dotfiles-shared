#!/bin/sh
# PreToolUse hook (matcher: Bash). Blocks `gh pr merge` while the PR has
# unresolved review threads, so Copilot/human comments get a reply and a
# resolve before the merge lands. With --delete-branch it also removes a clean
# linked worktree holding the head branch, since gh can't delete a branch that
# is checked out (dotfiles#125). Exit 2 = block; anything else = allow.
set -uf  # -f: $repoflag is word-split below and must not glob
command -v jq >/dev/null 2>&1 || exit 0
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
case "$cmd" in *"gh pr merge"*) ;; *) exit 0 ;; esac

# Tokenize with shell rules and parse the merge command up to the next
# separator, redirection, newline, or comment: PR number/url (first bare arg),
# -R/--repo, -d/--delete-branch, and --auto/--disable-auto. A `cd` or GH_REPO=
# before gh breaks the cwd/repo assumptions, so delete stays empty; so it does
# when python3 is missing or the quoting is unbalanced.
pr=""; repo=""; delete=""; auto=""
eval "$(python3 -c '
import shlex, sys
p = ";&|()<>\n"
l = shlex.shlex(sys.argv[1].replace("\\\n", ""), posix=True, punctuation_chars=p)
l.whitespace, l.whitespace_split, l.commenters = " \t\r", True, ""
t = list(l)
i = next(i for i in range(len(t)) if t[i:i + 3] == ["gh", "pr", "merge"])
v = dict(pr="", repo="", delete="", auto="")
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
    elif a in ("--delete-branch", "--delete-branch=true"):
        v["delete"] = "1"
    elif a.split("=")[0] in ("--auto", "--disable-auto"):
        v["auto"] = "1"
    elif a.startswith("-") and not a.startswith("--"):
        for j, c in enumerate(a[1:], 2):
            if c in "RbtAF":
                val = a[j:] or next(args, "")
                if c == "R":
                    v["repo"] = val
                break
            if c == "d":
                v["delete"] = "1"
    elif not a.startswith("-") and not v["pr"]:
        v["pr"] = a
if "cd" in t[:i] or any(x.startswith("GH_REPO=") for x in t[:i]):
    v["delete"] = ""
print(" ".join(k + "=" + shlex.quote(x) for k, x in v.items()))
' "$cmd" 2>/dev/null)"
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] && cd "$cwd" 2>/dev/null
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

# gh only deletes the local branch when -R is absent and the merge isn't queued.
[ -n "$delete" ] && [ -z "$repo" ] && [ -z "$auto" ] || exit 0
# The first worktree listed is the main checkout; only linked ones are removed.
wts=$(git worktree list --porcelain 2>/dev/null) || exit 0
main=$(printf '%s\n' "$wts" | sed -n '1s/^worktree //p')
wt=$(printf '%s\n' "$wts" | awk -v ref="refs/heads/$head" '
  /^worktree / { path = substr($0, 10); n++ }
  $0 == "branch " ref && n > 1 { print path }')
[ -n "$wt" ] || exit 0
# A worktree whose directory is gone is prunable; pruning frees the branch.
[ -d "$wt" ] || { git worktree prune; exit 0; }

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

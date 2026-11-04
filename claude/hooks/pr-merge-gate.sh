#!/bin/sh
# PreToolUse hook (matcher: Bash). Blocks `gh pr merge` while the PR has
# unresolved review threads, so Copilot/human comments get a reply and a
# resolve before the merge lands. Also blocks it when the PR's base branch is
# listed in PR_MERGE_GATE_PROTECTED_BASES (space-separated, default empty), so
# merges into those branches stay with the owner. Exit 2 = block; anything
# else = allow.
set -uf  # -f: the merge command is word-split below and must not glob
command -v jq >/dev/null 2>&1 || exit 0
input=$(cat)
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
pr_info=$(gh pr view ${pr:+"$pr"} $repoflag --json url,baseRefName \
  -q '.url + " " + .baseRefName' 2>/dev/null) || exit 0
url=${pr_info%% *}
base=${pr_info#* }
owner=$(printf '%s' "$url" | cut -d/ -f4)
name=$(printf '%s' "$url" | cut -d/ -f5)
num=$(printf '%s' "$url" | cut -d/ -f7)

case " ${PR_MERGE_GATE_PROTECTED_BASES:-} " in
  *" $base "*)
    cat >&2 <<MSG
BLOCKED: $owner/$name#$num merges into $base, a protected base branch
(PR_MERGE_GATE_PROTECTED_BASES). Agents don't merge into it. Hand the PR to
the owner as a link plus the merge command:
  $url
  gh pr merge $num -R $owner/$name
MSG
    exit 2 ;;
esac

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

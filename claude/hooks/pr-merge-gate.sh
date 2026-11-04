#!/bin/sh
# PreToolUse hook (matcher: Bash). Blocks `gh pr merge` while the PR has
# unresolved review threads, so Copilot/human comments get a reply and a
# resolve before the merge lands. When PR_MERGE_GATE_PROTECTED_BASES lists base
# branches (space-separated, default empty), it also blocks every `gh pr merge`
# in the command whose base is listed or can't be determined, so merges into
# those branches stay with the owner. Exit 2 = block; anything else = allow.
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

if [ -n "${PR_MERGE_GATE_PROTECTED_BASES:-}" ]; then
  # shlex follows the command's quoting, `cd` prefixes, GH_REPO= assignments,
  # and every merge in a compound command; the word split below does not.
  command -v python3 >/dev/null 2>&1 || {
    echo "BLOCKED: python3 is missing, so pr-merge-gate can't check the merge's base branch." >&2
    exit 2
  }
  PR_MERGE_GATE_CMD=$cmd python3 - <<'PY' || exit 2
import os
import re
import shlex
import subprocess
import sys

protected = os.environ["PR_MERGE_GATE_PROTECTED_BASES"].split()
cmd = os.environ["PR_MERGE_GATE_CMD"]
VALUE_FLAGS = {"-A", "--author-email", "-b", "--body", "-F", "--body-file",
               "--match-head-commit", "-t", "--subject", "-R", "--repo"}
SHELLS = {"sh", "bash", "zsh", "dash"}


def refuse(text):
    print("BLOCKED: " + text, file=sys.stderr)
    sys.exit(2)


def unknown(merge, reason):
    refuse(f"can't tell which branch `{merge}` merges into ({reason}), and "
           "PR_MERGE_GATE_PROTECTED_BASES protects " + " ".join(protected) +
           ". Run it as its own command with the PR number and -R owner/repo, "
           "or hand it to the owner.")


def tokens(text):
    lexer = shlex.shlex(text.replace("\n", ";"), posix=True,
                        punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError as error:
        unknown(text.strip(), f"unparseable: {error}")


def segments(words):
    current = []
    for word in words:
        if word and set(word) <= set("();|&"):
            yield current
            current = []
        else:
            current.append(word)
    yield current


def check(text, cwd, env):
    for words in segments(tokens(text)):
        if not words:
            continue
        if words[0] == "cd":
            target = words[1] if len(words) > 1 else "~"
            cwd = os.path.join(cwd, os.path.expanduser(target))
            continue
        if words[0] == "export":
            env.update(w.split("=", 1) for w in words[1:] if "=" in w)
            continue
        local = dict(env)
        for i, word in enumerate(words):
            if i + 1 < len(words) and (word == "eval" or (
                re.fullmatch(r"-[a-z]*c", word)
                and os.path.basename(words[i - 1]) in SHELLS
            )):
                check(words[i + 1], cwd, dict(local))
            if "=" in word and word.split("=", 1)[0].isidentifier():
                key, value = word.split("=", 1)
                local[key] = value
            elif (os.path.basename(word) == "gh"
                  and words[i + 1:i + 3] == ["pr", "merge"]):
                merge(words[i + 3:], cwd, local)


def merge(args, cwd, env):
    selector, repo, rest = None, None, list(args)
    text = shlex.join(["gh", "pr", "merge", *args])
    while rest:
        word = rest.pop(0)
        if word in VALUE_FLAGS:
            if not rest:
                unknown(text, f"{word} has no value")
            value = rest.pop(0)
            if word in ("-R", "--repo"):
                repo = value
        elif word.startswith(("--repo=", "-R=")):
            repo = word.split("=", 1)[1]
        elif word[:1] in ("<", ">"):
            rest[:1] = []  # the redirection's target
        elif word.isdigit() and rest and rest[0][:1] in ("<", ">"):
            continue  # a file descriptor, as in 2>&1
        elif word.startswith("-"):
            continue
        elif selector is None:
            selector = word
        else:
            unknown(text, f"unexpected argument {word!r}")
    view = ["gh", "pr", "view", *([selector] if selector else []),
            *(["-R", repo] if repo else []),
            "--json", "baseRefName,url", "-q", '.baseRefName + " " + .url']
    try:
        result = subprocess.run(view, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        unknown(text, f"PR lookup failed: {error}")
    base, _, url = result.stdout.strip().partition(" ")
    if result.returncode or not base or not url:
        unknown(text, "PR lookup failed: " + (result.stderr.strip() or "no base"))
    if base in protected:
        parts = url.split("/")
        refuse(f"{parts[3]}/{parts[4]}#{parts[6]} merges into {base}, a protected "
               "base branch (PR_MERGE_GATE_PROTECTED_BASES). Agents don't merge "
               "into it. Hand the PR to the owner as a link plus the merge command:\n"
               f"  {url}\n  gh pr merge {parts[6]} -R {parts[3]}/{parts[4]}")


check(cmd, os.getcwd(), dict(os.environ))
PY
fi
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

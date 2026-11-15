#!/bin/sh
# PreToolUse hook (matcher: Bash). Allows a `gh pr merge`, into any base,
# only when all three hold, and refuses (exit 2) naming the one that failed:
#   1. no check on the PR's head commit is pending or failed (no checks at all
#      passes), read by that commit's SHA: its check runs and commit statuses;
#   2. no review thread is unresolved (every page of threads is read);
#   3. a blind-review verdict is recorded for the PR's current head commit: a
#      PR comment by the repo's owner, a member, or a collaborator whose first
#      line is exactly
#        Blind review: pass <full 40-character head commit SHA>
#      A verdict naming an older commit doesn't count.
# The merge must also carry --match-head-commit <that head SHA>, so GitHub
# refuses it if the head moves after these checks.
# If it can't read one of these (gh fails, rate limit, jq missing), it refuses.
# With --delete-branch it also removes a clean linked worktree holding the
# head branch, since gh can't delete a branch that is checked out
# (dotfiles#125). Exit 2 = block; anything else = allow.
#
# It reads command text, so it catches mistakes, not an agent set on getting
# around it. When PR_MERGE_GATE_PROTECTED_BASES is set (any non-empty value;
# it is still read as a list of branch names separated by spaces, commas, or
# semicolons), the command form is strict: a command that names a merge (gh
# and merge, `pr merge`, or a gh api merge path, in any spelling the check
# understands) is refused unless it is exactly
#   [cd <path> && ] gh pr merge <number> -R <owner>/<repo> [allowed flags]
# where the allowed flags must include --match-head-commit <head sha>,
# or it only passes the words as quoted text to a program that can't run them
# (echo, grep, git commit, gh pr view, ...). Parsing arbitrary shell can't be
# made complete, so everything else that names a merge is refused, as is any
# merge when the list names no branch or a name git forbids. The list no longer
# keeps any base from agents: the three conditions apply to every base. Under
# the list the hook never removes a worktree.
set -uf  # -f: $repoflag and $cmd are word-split below and must not glob
input=$(cat)
canonical=""
# The form check. The script, a NUL, then the payload, all on stdin: an
# environment variable caps the payload's size, and Windows Python can't read
# another fd. -I keeps the working directory and user site-packages off the
# import path. Exit 0 = not a merge (or only quoted text for an inert
# program), 3 = the checked merge form, 4 = a merge to check (no list set),
# anything else = refused.
formcheck() {
  { cat <<'PY'
import codecs
import json
import os
import re
import shlex
import sys
import warnings

listed = os.environ.get("PR_MERGE_GATE_PROTECTED_BASES", "")
protected = [b for b in re.split(r"[\s,;]+", listed) if b]
# Names git itself forbids in a ref; one of these means the list is a typo.
invalid = [b for b in protected
           if b.startswith("-") or re.search(r"[\x00-\x20\x7f~^:?*\[\\]|\.\.|@\{", b)]
FORM = ("gh pr merge <number> -R <owner>/<repo> --match-head-commit <head sha> "
        "[--merge|--squash|--rebase] [--delete-branch] [--subject '<text>'] "
        "[--body '<text>']")
# No --auto: the PR's base can change after auto-merge is armed.
SWITCHES = {"--merge", "--squash", "--rebase", "--delete-branch"}
# Expansions, substitutions, and globs: each may stand for any text.
EXPANSION = re.compile(r"\$\{[^}]*\}|\$\([^()]*\)|`[^`]*`|\$\w+|\$[?#@*!$-]|[*?]|\[[^\]]*\]")
WILD = "\0"
# Programs that print or store their arguments and can't run them.
INERT = {"echo", "printf", "grep", "egrep", "fgrep", "head", "tail", "wc"}
INERT_GIT = {"commit", "log", "show", "status"}
INERT_GH = {("pr", s) for s in ("view", "list", "checks", "diff", "create", "comment")}
INERT_GH |= {("issue", s) for s in ("view", "list", "create", "comment")}
API_VALUE_FLAGS = {"-f", "-F", "--field", "--raw-field", "-q", "--jq", "-t",
                   "--template", "-H", "--header", "-X", "--method",
                   "--hostname", "--cache", "--input"}
QUOTED = "\1"


def possible_merge(text):
    if not re.search(r"\bgh\b", text, re.I):
        return False
    return bool(re.search(r"\bmerge\b", text, re.I)
                or (re.search(r"\bapi\b", text, re.I) and re.search("merge", text, re.I)))


def ansi_c(match):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return codecs.decode(match.group(1), "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            return WILD


def skeleton(cmd):
    """Lowercased command with $'...' decoded, every expansion or glob turned
    into WILD, and quotes and backslashes removed."""
    text, prev = re.sub(r"\$'((?:[^'\\]|\\.)*)'", ansi_c, cmd), None
    while prev != text:  # inner $(...) first
        prev, text = text, EXPANSION.sub(WILD, text)
    return re.sub(r"[\"'\\]", "", text).lower()


def could_be(word, target):
    """word, with WILD standing for any text, can spell target. Needs at least
    one literal character, so a bare $x matches nothing."""
    if not word.strip(WILD):
        return False
    return re.fullmatch(".*".join(map(re.escape, word.split(WILD))), target) is not None


def could_contain_merge(word):
    parts = word.split(WILD)
    if any("merge" in p for p in parts):
        return True
    return any(left.endswith("merge"[:k]) or right.startswith("merge"[k:])
               for left, right in zip(parts, parts[1:]) for k in range(1, 5))


def names_merge(cmd):
    bare = re.sub(r"\$\{[^}]*\}|\$\w+|[\"'\\`]", "", cmd)
    if possible_merge(cmd) or possible_merge(bare):
        return True
    words = [w for w in re.split(r"[\s;&|()<>{}]+", skeleton(cmd)) if w]
    gh = any(could_be(w.rsplit("/", 1)[-1], "gh") for w in words)
    if any(could_be(a, "pr") and could_be(b, "merge") for a, b in zip(words, words[1:])):
        return True
    if gh and any(could_be(w, "merge") for w in words):
        return True
    return (gh and any(could_be(w, "api") for w in words)
            and any(could_contain_merge(w) for w in words))


def api_without_merge(words):
    """A gh api call whose endpoint can't be a merge: outside flag values, no
    word is quoted, names graphql, or contains merge."""
    words = iter(words)
    for word in words:
        if word in API_VALUE_FLAGS:
            next(words, None)
        elif QUOTED in word or re.search("graphql|merge", word, re.I):
            return False
    return True


def inert(words):
    if not words or words[0] in INERT:
        return True
    if words[0] == "git":
        return words[1:2] != [] and words[1] in INERT_GIT
    if words[0] == "gh" and tuple(words[1:3]) in INERT_GH:
        return True
    return words[:2] == ["gh", "api"] and api_without_merge(words[2:])


def only_quoted_text(cmd):
    """Every simple command is an inert program, and quoting, chaining, pipes,
    and discarding output are the only shell syntax: the words can't reach a
    shell or a gh merge."""
    masked, i = [], 0
    while i < len(cmd):
        quote = cmd[i]
        if quote not in "'\"":
            masked.append(quote)
            i += 1
            continue
        j = i + 1
        while j < len(cmd) and cmd[j] != quote:
            j += 2 if quote == '"' and cmd[j] == "\\" else 1
        if j >= len(cmd) or (quote == '"' and re.search(r"[$`]", cmd[i + 1:j])):
            return False
        masked.append(QUOTED)
        i = j + 1
    masked = re.sub(r"\d?>\s*(?:&\d|/dev/null)(?=\s|$|[;&|])", " ", "".join(masked))
    if re.search(r"[$`\\<>(){}]|(?<!&)&(?!&)", masked):
        return False
    return all(inert(s.split()) for s in re.split(r"&&|\|\|?|;|\n", masked))


def refuse(text):
    print("BLOCKED: " + text, file=sys.stderr)
    sys.exit(2)


def noncanonical(why):
    refuse("PR_MERGE_GATE_PROTECTED_BASES is set, and this command names a "
           f"merge without being the one merge form the gate checks "
           f"({why}). To merge, run only:\n  [cd <path> && ] {FORM}\n"
           "If the command only mentions merging, rephrase it: put the words in quotes as "
           "an argument to echo, printf, grep, git commit/log, or gh pr/issue "
           "view/create/comment, with no other shell syntax than && || ; |, "
           "or write the text with a file tool.")


try:
    data = json.loads(payload)  # noqa: F821  set by the runner
except ValueError:
    data = None
tool_input = data.get("tool_input") if isinstance(data, dict) else None
cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
if cmd is None:
    tool = data.get("tool_name") if isinstance(data, dict) else None
    if isinstance(tool, str) and tool not in ("", "Bash"):
        sys.exit(0)  # another tool, nothing to run
    refuse("PR_MERGE_GATE_PROTECTED_BASES is set, and the hook input isn't one "
           "JSON object with a string tool_input.command, so pr-merge-gate "
           "can't check it for a merge.")
if not isinstance(cmd, str):
    noncanonical("the command is not a single string")
if not names_merge(cmd) or only_quoted_text(cmd):
    sys.exit(0)
if not listed:
    sys.exit(4)  # no list, no strict form: the shell below checks the merge
if invalid:
    refuse(f"PR_MERGE_GATE_PROTECTED_BASES lists {invalid[0]!r}, which git "
           "doesn't allow as a branch name, so the gate refuses every merge. "
           "Fix the list; separate branches with spaces, commas, or semicolons.")
if not protected:
    refuse("PR_MERGE_GATE_PROTECTED_BASES is set but names no valid branch name, so "
           "the gate refuses every merge. List branches separated by spaces, "
           "commas, or semicolons.")
if re.search(r"[\n\r\\`$;|<>(){}*?\[\]~!]", cmd):
    noncanonical("it has a newline, backslash, backtick, or one of $ ; | < > ( ) { } * ? [ ] ~ !")
quote = ""
for i, ch in enumerate(cmd):  # no backslash got here, so quotes nest simply
    if quote:
        quote = "" if ch == quote else quote
    elif ch in "'\"":
        quote = ch
    elif ch == "#" and (i == 0 or cmd[i - 1] in " \t&"):
        noncanonical("a word starts with # outside quotes, and the shell drops "
                     "the rest of the line as a comment")
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
        or not re.fullmatch(r"[0-9]+", words[3]) or words[4] != "-R"
        or not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", words[5])):
    noncanonical("it must start with gh pr merge <number> -R <owner>/<repo>")
num, repo, rest = words[3], words[5], words[6:]
if any(w.startswith(("-R", "--repo")) for w in rest):
    noncanonical("-R may appear only once, as -R <owner>/<repo> after the number")
pinned = False
while rest:
    word = rest.pop(0)
    if word in SWITCHES:
        continue
    if word in ("--subject", "--body") and rest:
        rest.pop(0)
        continue
    if word == "--match-head-commit" and rest and not pinned:
        if not re.fullmatch(r"[0-9a-f]{40}", rest.pop(0)):
            noncanonical("--match-head-commit takes the full 40-character head SHA in lowercase")
        pinned = True
        continue
    noncanonical(f"{word!r} is not an allowed flag here")
# The one checked form: the shell below checks the PR's facts, and that
# --match-head-commit is present and names the head it read.
sys.exit(3)
PY
    printf '\0%s' "$input"
  } | python3 -I -c 'import sys
code, _, payload = sys.stdin.buffer.read().decode("utf-8", "replace").partition("\0")
exec(code)'
}
if [ -n "${PR_MERGE_GATE_PROTECTED_BASES:-}" ]; then
  # Every command goes to Python, which reads the command field itself: a
  # text filter on the raw JSON can't soundly tell which commands to skip.
  command -v python3 >/dev/null 2>&1 || {
    echo "BLOCKED: PR_MERGE_GATE_PROTECTED_BASES is set but python3 is missing, so pr-merge-gate can't check this command for a merge." >&2
    exit 2
  }
  formcheck
  status=$?
  [ $status -eq 3 ] && canonical=1
  [ $status -eq 0 ] || [ -n "$canonical" ] || exit 2
fi
block() { printf 'BLOCKED: %s\n' "$*" >&2; exit 2; }
if ! command -v jq >/dev/null 2>&1; then
  [ -n "$canonical" ] && block "jq is missing, so pr-merge-gate can't read this merge's PR. Install jq, then retry."
  case "$input" in *"gh pr merge"*) block "jq is missing, so pr-merge-gate can't check this command for a merge. Install jq, then retry." ;; esac
  exit 0
fi
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
case "$cmd" in *"gh pr merge"*) ;; *) [ -n "$canonical" ] || exit 0 ;; esac

# Tokenize with shell rules and parse the merge command up to the next
# separator, redirection, newline, or comment: PR number/url (first bare arg),
# -R/--repo, -d/--delete-branch, and --auto/--disable-auto. delete is 1 for an
# exact -d/--delete-branch and ? for a valued form like -d=false. safe=1 only
# for a lone `gh pr merge <number> ...` with exact delete and nothing that moves
# the cwd, repo, or timing; only then may the hook remove a worktree. A fd
# number before a redirect (2>&1) is dropped so it isn't read as the PR. When
# the words only appear inside one quoted word (bash -c "gh pr merge 7"), that
# word is parsed as a command and nested=1; none=1 if no merge is found there.
# run=1 when such a word is handed to something that executes it: a shell or
# eval in its pipeline, or a command substitution in the word. fixpre and
# fixpost are the agent's own command split where the head SHA goes to pin it.
pr=""; repo=""; delete=""; auto=""; safe=""; match=""; nested=""; none=""
run=""; fixpre=""; fixpost=""
if out=$(python3 -I -c '
import re, shlex, sys
p = ";&|()<>\n"
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh", "fish", "eval"}
PIN = "0" * 40
def lex(c):
    l = shlex.shlex(c, posix=True, punctuation_chars=p)
    l.whitespace, l.whitespace_split, l.commenters = " \t\r", True, ""
    return list(l)
def sublex(w):
    try:
        return lex(w)
    except ValueError:  # prose with an unbalanced quote: not a command
        return []
def find(t):
    return next((i for i in range(len(t)) if t[i:i + 3] == ["gh", "pr", "merge"]), None)
def stop(x):
    return set(x) <= set(p) and (x == "&" or bool(re.search(r"[;\n]|&&|\|\|", x)))
def runs(t, k):
    if re.search(r"\$\(|`", t[k]):
        return True
    a = b = k
    while a > 0 and not stop(t[a - 1]):
        a -= 1
    while b < len(t) and not stop(t[b]):
        b += 1
    return any(t[j].rsplit("/", 1)[-1] in SHELLS for j in range(a, b) if j != k)
def parse(raw):
    c = re.sub(r"(^|\s)\d+(?=[<>])", r"\1", raw.replace("\r", "").replace("\\n", ""))
    t = lex(c)
    i, nested, run = find(t), "", ""
    if i is None:
        quoted = [k for k, w in enumerate(t) if "gh pr merge" in w]
        run = "1" if any(runs(t, k) for k in quoted) else ""
        for k in quoted:
            if find(sublex(t[k])) is not None:
                t, nested = sublex(t[k]), "1"
                i = find(t)
                break
    if i is None:
        return dict(none="1", run=run)
    v = dict(pr="", repo="", delete="", auto="", safe="", match="", nested=nested, run=run)
    args = iter(t[i + 3:])
    for a in args:
        if set(a) <= set(p) or a.startswith("#"):
            break
        if a in ("-R", "--repo"):
            v["repo"] = next(args, "")
        elif a.startswith("--repo="):
            v["repo"] = a[7:]
        elif a == "--match-head-commit":
            v["match"] = next(args, "")
        elif a.startswith("--match-head-commit="):
            v["match"] = a[20:]
        elif a in ("--body", "--subject", "--author-email", "--body-file"):
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
    if (i == 0 and not nested and v["delete"] == "1" and v["pr"].isdigit() and not v["repo"]
            and not v["auto"] and not re.search("[$`]", c)
            and not any(set(x) <= set(p) for x in t)):
        v["safe"] = "1"
    return v
def fix(raw, v):
    # Where the head SHA goes in the agent command: after a gh pr merge with
    # the flag added, or in place of the pin value. A spot counts only if the
    # result parses as this same merge pinned to that SHA.
    if v["match"]:
        flag = ""
        spots = [m.span(1) for m in re.finditer(
            r"--match-head-commit(?:[ \t]+|=)[\x27\"]?([^\s;&|()<>\x27\"]+)", raw)]
    else:
        flag = " --match-head-commit "
        spots = [(m.end(), m.end()) for m in re.finditer("gh pr merge", raw)]
    for a, b in spots:
        pre, post = raw[:a] + flag, raw[b:]
        try:
            w = parse(pre + PIN + post)
        except ValueError:
            continue
        if w.get("match") == PIN and all(w[k] == v[k] for k in ("pr", "repo", "nested")):
            return pre, post
    return "", ""
v = parse(sys.argv[1])
if not v.get("none"):
    v["fixpre"], v["fixpost"] = fix(sys.argv[1], v)
print(" ".join(k + "=" + shlex.quote(x) for k, x in v.items()))
' "$cmd" 2>/dev/null); then
  eval "$out"
else
  [ -n "$canonical" ] && block "pr-merge-gate couldn't parse the merge it just checked, so the merge is refused."
  # Unparseable or no python3: the old word-split extraction for the PR and
  # repo, and treat any delete-looking word anywhere as a possible delete.
  set -- $(printf '%s' "$cmd" | sed -n 's/.*gh pr merge//p')
  while [ $# -gt 0 ]; do
    case "$1" in
      -R|--repo) repo=${2:-}; [ $# -gt 1 ] && shift ;;
      --repo=*) repo=${1#--repo=} ;;
      --match-head-commit) match=${2:-}; [ $# -gt 1 ] && shift ;;
      --match-head-commit=*) match=${1#--match-head-commit=} ;;
      -*) ;;
      *) [ -z "$pr" ] && pr=$1 ;;
    esac
    shift
  done
  for w in $cmd; do
    case "$w" in --delete-branch*) delete="?" ;; --*) ;; -*d*) delete="?" ;; esac
  done
fi
if [ -n "$canonical" ] && { [ -n "$none$nested" ] || [ -z "$pr" ] || [ -z "$repo" ]; }; then
  block "pr-merge-gate couldn't parse the merge it just checked, so the merge is refused."
fi
if [ -n "$none$nested" ] && [ -z "$canonical" ]; then
  # The words sit inside quoted text. Text that nothing in the command
  # executes passes unchecked (rg "gh pr merge", a PR body, a commit message).
  # Text handed to a shell or eval passes only as quoted text for a program
  # that can't run it; a merge whose PR number can be read is checked; anything
  # else is refused.
  [ -n "$run" ] || exit 0
  formcheck 2>/dev/null && exit 0
  [ -z "$none" ] && [ -n "$pr" ] || block "this command runs gh pr merge from inside quoted text, and pr-merge-gate can't tell which PR it merges, so it is refused. Run the merge as a plain command: gh pr merge <number> -R <owner>/<repo> --match-head-commit <head sha> [flags]"
fi
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] && { cd "$cwd" 2>/dev/null || safe=""; }
repoflag=""; [ -n "$repo" ] && repoflag="-R $repo"
cantread() {
  block "pr-merge-gate couldn't read the $1 of ${2:-this PR} ($(printf '%s' "$3" | head -n 3 | tr '\n' ' ')), so the merge is refused. Retry once gh can read it: check \`gh auth status\` and \`gh api rate_limit\`."
}
# The PR url names the base repo even when the head is a fork.
info=$(gh pr view ${pr:+"$pr"} $repoflag --json url,headRefName,headRefOid \
  -q '.url + " " + .headRefName + " " + .headRefOid' 2>&1) \
  || cantread "head commit" "PR ${pr:-for this branch}${repo:+ in $repo}" "$info"
url=${info%% *}; head=${info#* }; sha=${head#* }; head=${head%% *}
owner=$(printf '%s' "$url" | cut -d/ -f4)
name=$(printf '%s' "$url" | cut -d/ -f5)
num=$(printf '%s' "$url" | cut -d/ -f7)
# Spelled-out classes: a range like a-f can match uppercase in some locales.
case "$num" in *[!0123456789]*) cantread "head commit" "PR ${pr:-for this branch}" "$info" ;; esac
case "$sha" in *[!0123456789abcdef]*) cantread "head commit" "PR ${pr:-for this branch}" "$info" ;; esac
[ -n "$owner" ] && [ -n "$name" ] && [ -n "$num" ] && [ ${#sha} -eq 40 ] \
  || cantread "head commit" "PR ${pr:-for this branch}" "$info"

# 1. Checks on the head commit read above, by its SHA, so a later push can't
# swap in another commit's checks: its check runs and its commit statuses,
# every page of each at the largest page size. Each check run must be
# completed as success, neutral, or skipped, and each status success; none at
# all passes. gh prints one JSON object per page; anything else is unreadable.
at="$owner/$name commit $sha"
runs=$(gh api "repos/$owner/$name/commits/$sha/check-runs?per_page=100" --paginate 2>&1) \
  || cantread "check runs" "$at" "$runs"
failing=$(printf '%s' "$runs" | jq -rs '
  if length > 0 and all(.[]; type == "object" and (.check_runs | type) == "array")
  then .[].check_runs[]
    | select(.status != "completed" or (.conclusion != "success"
        and .conclusion != "neutral" and .conclusion != "skipped"))
    | "  \(.name) (\(if .status == "completed" then .conclusion else .status end))"
  else error("not a page of check runs") end' 2>&1) \
  || cantread "check runs" "$at" "$failing"
statuses=$(gh api "repos/$owner/$name/commits/$sha/status?per_page=100" --paginate 2>&1) \
  || cantread "commit statuses" "$at" "$statuses"
failed=$(printf '%s' "$statuses" | jq -rs '
  if length > 0 and all(.[]; type == "object" and (.statuses | type) == "array")
  then .[].statuses[] | select(.state != "success") | "  \(.context) (\(.state))"
  else error("not a page of commit statuses") end' 2>&1) \
  || cantread "commit statuses" "$at" "$failed"
failing=$(printf '%s\n%s' "$failing" "$failed" | sed '/^$/d')
[ -z "$failing" ] || block "$owner/$name#$num has checks on its head commit $sha that are pending or failed:
$failing
Wait for them to finish (gh pr checks $num -R $owner/$name --watch), fix any failure and push, then retry."

# 2. Review threads, every page: --paginate follows endCursor until the last.
threads=$(gh api graphql --paginate -f owner="$owner" -f name="$name" -F num="$num" -f query='
  query($owner:String!,$name:String!,$num:Int!,$endCursor:String){ repository(owner:$owner,name:$name){
    pullRequest(number:$num){ reviewThreads(first:100,after:$endCursor){ nodes{
      id isResolved path line comments(first:1){ nodes{ author{login} body url } } }
      pageInfo{ hasNextPage endCursor } } } } }' \
  -q '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)
      | "\(.id)\t\(.path):\(.line // "?")\t\(.comments.nodes[0].author.login)\t\(.comments.nodes[0].url)\n    \(.comments.nodes[0].body | gsub("\n";" ") | .[:160])"' 2>&1) \
  || cantread "review threads" "$owner/$name#$num" "$threads"
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

# 3. Blind-review verdict for the current head: the first line of a comment by
# the repo's owner, a member, or a collaborator, since anyone can comment on a
# public repo.
verdict="Blind review: pass $sha"
# per_page=100, the maximum, so a long thread costs as few requests as possible.
firsts=$(gh api "repos/$owner/$name/issues/$num/comments?per_page=100" --paginate \
  -q '.[] | select(.author_association == "OWNER" or .author_association == "MEMBER" or .author_association == "COLLABORATOR")
      | .body | split("\n")[0] | rtrimstr("\r")' 2>&1) \
  || cantread "comments" "$owner/$name#$num" "$firsts"
printf '%s\n' "$firsts" | grep -Fxq "$verdict" || block "$owner/$name#$num has no blind-review verdict for its head commit $sha.
After a blind review of this commit passes, post a PR comment (as the repo's
owner, a member, or a collaborator) whose first line is exactly:
  $verdict
for example: gh pr comment $num -R $owner/$name --body-file <file>
A verdict for an older commit doesn't count: a new commit needs a new review."

# 4. Pin the merge to the head checked above: with --match-head-commit, GitHub
# refuses the merge if the head moves after this check.
# The refusal prints the agent's own command, pinned: the strict form takes
# the pin at its end; otherwise it goes where the parser placed it, or, if the
# parser found no spot, after the first gh pr merge or in place of the value.
if [ "$match" != "$sha" ]; then
  if [ -n "$canonical" ] && [ -z "$match" ]; then fix="$cmd --match-head-commit $sha"
  elif [ -n "$fixpre$fixpost" ]; then fix="$fixpre$sha$fixpost"
  elif [ -z "$match" ]; then fix="${cmd%%"gh pr merge"*}gh pr merge --match-head-commit $sha${cmd#*"gh pr merge"}"
  else fix="${cmd%%"$match"*}$sha${cmd#*"$match"}"
  fi
  [ -z "$match" ] && block "this merge of $owner/$name#$num doesn't pin the head commit the gate checked. Run it with --match-head-commit, so GitHub refuses the merge if the head moves:
  $fix"
  block "this merge of $owner/$name#$num pins --match-head-commit $match, but the PR's head is $sha. Run it pinned to the head the gate checked:
  $fix"
fi

# Under the gate the only merge allowed names its repo with -R, and gh never
# deletes a local branch then, so no worktree stands in its way: never touch one.
[ -n "${PR_MERGE_GATE_PROTECTED_BASES:-}" ] && exit 0
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

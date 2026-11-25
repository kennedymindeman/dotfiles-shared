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
#      A verdict naming an older commit doesn't count, unless the only change
#      from it to the head is deleting files under .pr-assets/. A PR that
#      changes at most 80 lines in at most 5 files may carry instead
#        Self review: pass <full 40-character head commit SHA>
# The merge must also carry --match-head-commit <that head SHA>, so GitHub
# refuses it if the head moves after these checks. With --delete-branch, no
# other open PR may have this PR's head branch as its base.
# If it can't read one of these (gh fails, rate limit, jq missing), it refuses.
# Exit 2 = block; anything else = allow.
#
# It reads command text, so it catches mistakes, not an agent set on getting
# around it. A command that names a merge (gh and merge, `pr merge`, or a gh
# api merge path, in any spelling the check understands, or a gh command whose
# command or subcommand word is entirely an expansion) is refused unless it is
# exactly
#   [cd <path> && ] gh pr merge <number> -R <owner>/<repo> [allowed flags]
# where the allowed flags must include --match-head-commit <head sha>,
# or it only passes the words as quoted text to a program that can't run them
# (echo, grep, rg, git commit, gh pr view, ...). The command is read twice, as
# written and with its comments dropped as bash drops them, and passes without
# checks only when neither reading names a merge or both are inert-only.
# Parsing arbitrary shell can't be made complete, so everything else that
# names a merge is refused.
set -u
input=$(cat)
# The form check. The script, a NUL, then the payload, all on stdin: an
# environment variable caps the payload's size, and Windows Python can't read
# another fd. -I keeps the working directory and user site-packages off the
# import path; -S skips the site module and its .pth files. Exit 0 = not a
# merge (or only quoted text for an inert program), 3 = the checked merge
# form, 2 = refused. For 3 it prints shell assignments of cmd, pr, repo,
# match (the pin's value), and fixpre and fixpost (the command split around
# that value).
formcheck() {
  { cat <<'PY'
import codecs
import json
import re
import shlex
import sys
import warnings

FORM = ("gh pr merge <number> -R <owner>/<repo> --match-head-commit <head sha> "
        "[--merge|--squash|--rebase] [--delete-branch] [--subject '<text>'] "
        "[--body '<text>']")
# No --auto: the PR's base can change after auto-merge is armed.
SWITCHES = {"--merge", "--squash", "--rebase", "--delete-branch"}
# Expansions, substitutions, and globs: each may stand for any text.
EXPANSION = re.compile(r"\$\{[^}]*\}|\$\([^()]*\)|`[^`]*`|\$\w+|\$[?#@*!$-]|[*?]|\[[^\]]*\]")
WILD = "\0"
# Programs that print or store their arguments and can't run them.
INERT = {"echo", "printf", "grep", "egrep", "fgrep", "head", "tail", "wc", "cat", "rg"}
INERT_GIT = {"commit", "log", "show", "status", "grep"}
INERT_GH = {("pr", s) for s in ("view", "list", "checks", "diff", "create", "comment", "edit")}
INERT_GH |= {("issue", s) for s in ("view", "list", "create", "comment")}
# rg --pre and git grep -O/--open-files-in-pager run a program they are given.
RUNS_PROGRAM = re.compile(r"--pre\b|--open-files-in-pager|(?<![\w-])-[A-Za-z]*O")
# Words that may come before a program name in a simple command.
PREFIXES = {"do", "then", "else", "elif", "if", "while", "until", "!", "time",
            "command", "exec", "env", "nohup"}
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


def strip_comments(cmd):
    """cmd without its comments: as bash reads it, an unquoted # that starts a
    word runs to the end of its line; inside ${...} and $((...)) a # is text.
    From a heredoc, a double-quoted $(...) or backtick, or an unclosed quote,
    brace, or parenthesis on, the rest is kept as written."""
    out, i, start, subst = [], 0, True, []
    while i < len(cmd):
        c = cmd[i]
        if c == "\\":  # an escaped character; backslash-newline joins lines
            out.append(cmd[i:i + 2])
            start = start and cmd[i + 1:i + 2] == "\n"
            i += 2
            continue
        if cmd.startswith("${", i) or cmd.startswith("$((", i):
            opening = cmd[i + 1]
            closing = "}" if opening == "{" else ")"
            depth, j = 0, i + 1
            while j < len(cmd):
                depth += (cmd[j] == opening) - (cmd[j] == closing)
                j += 1
                if depth == 0:
                    break
            if depth:
                break
            out.append(cmd[i:j])
            i, start = j, False
            continue
        if c == "#" and start:
            end = cmd.find("\n", i)
            i = len(cmd) if end < 0 else end
            continue
        if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
            break
        if c in "'\"`" or (c == "$" and cmd[i + 1:i + 2] in ("'", '"')):
            j = i + 1 + (c == "$")
            q, escapes = cmd[j - 1], cmd[j - 1] != "'" or c == "$"
            while j < len(cmd) and cmd[j] != q:
                j += 2 if escapes and cmd[j] == "\\" else 1
            if j >= len(cmd) or (q == '"' and re.search(r"\$\(|`", cmd[i:j])):
                break
            out.append(cmd[i:j + 1])
            i, start = j + 1, False
            continue
        out.append(c)
        if c == "(":
            subst.append(cmd[i - 1:i] in ("$", "<", ">"))
            start = True
        elif c == ")":  # the end of $(...) is still inside a word
            start = not (subst and subst.pop())
        else:
            start = c in " \t\n;&|<>"
        i += 1
    return "".join(out) + cmd[i:]


def commands(cmd):
    """cmd split at unquoted ; & | newline ( ) { }, keeping $(...), ${...},
    and backticks whole: roughly its simple commands."""
    parts, cur, i = [], [], 0
    while i < len(cmd):
        c, j = cmd[i], i + 1
        if c == "\\":
            j = i + 2
        elif c in "'\"`":
            while j < len(cmd) and cmd[j] != c:
                j += 2 if c != "'" and cmd[j] == "\\" else 1
            j += 1
        elif c == "$" and cmd[i + 1:i + 2] in ("(", "{"):
            depth, j = 0, i + 1
            while j < len(cmd):
                depth += {"(": 1, "{": 1, ")": -1, "}": -1}.get(cmd[j], 0)
                j += 1
                if depth == 0:
                    break
        elif c in ";&|\n(){}":
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(cmd[i:j])
        i = j
    return parts + ["".join(cur)]


def gh_expansion(cmd):
    """A gh command whose command or subcommand word is entirely an expansion
    (gh pr $(cat f) 5, gh $a $b 5): it can't be known not to be a merge."""
    for part in commands(cmd):
        words = skeleton(part).split()
        while words and (words[0] in PREFIXES or re.fullmatch(r"\w+=\S*", words[0])):
            words.pop(0)
        if words[:1] and words[0].rsplit("/", 1)[-1] == "gh":
            if words[1:2] == [WILD] or (words[1:2] != ["api"] and words[2:3] == [WILD]):
                return True
    return False


def names_merge(cmd):
    bare = re.sub(r"\$\{[^}]*\}|\$\w+|[\"'\\`]", "", cmd)
    if possible_merge(cmd) or possible_merge(bare) or gh_expansion(cmd):
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


def inert(words, cmd):
    if words[:1] == ["rg"] or words[:2] == ["git", "grep"]:
        return not RUNS_PROGRAM.search(cmd)
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
    return all(inert(s.split(), cmd) for s in re.split(r"&&|\|\|?|;|\n", masked))


def refuse(text):
    print("BLOCKED: " + text, file=sys.stderr)
    sys.exit(2)


def noncanonical(why):
    refuse("this command names a merge without being the one merge form the gate checks "
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
if not isinstance(cmd, str):
    tool = data.get("tool_name") if isinstance(data, dict) else None
    if cmd is None and isinstance(tool, str) and tool not in ("", "Bash"):
        sys.exit(0)  # another tool, nothing to run
    if cmd is not None:
        noncanonical("the command is not a single string")
    refuse("the hook input isn't one JSON object with a string "
           "tool_input.command, so pr-merge-gate can't check it for a merge.")
# Two readings: as written, and without comments. When they disagree, the
# command is treated as naming a merge.
text = strip_comments(cmd)
if ((not names_merge(cmd) and not names_merge(text))
        or (only_quoted_text(cmd) and only_quoted_text(text))):
    sys.exit(0)
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
def lex(c):
    lexer = shlex.shlex(c, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


try:
    full = lex(cmd)
except ValueError:
    noncanonical("unbalanced quotes")
words = full
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
match, at, dels = "", None, set()
while rest:
    word = rest.pop(0)
    if word in SWITCHES:
        if word == "--delete-branch":
            dels.add(len(full) - len(rest) - 1)
        continue
    if word in ("--subject", "--body") and rest:
        rest.pop(0)
        continue
    if word == "--match-head-commit" and rest and not match:
        at, match = len(full) - len(rest), rest.pop(0)
        if not re.fullmatch(r"[0-9a-f]{40}", match):
            noncanonical("--match-head-commit takes the full 40-character head SHA in lowercase")
        continue
    noncanonical(f"{word!r} is not an allowed flag here")
# Where a head SHA replaces the pin's value in the agent's command: the first
# 40-hex run that, swapped, changes only the pin's word.
fixpre = fixpost = ""
if match:
    want = full[:at] + ["0" * 40] + full[at + 1:]
    for m in re.finditer(r"[0-9a-f]{40}", cmd):
        if lex(cmd[:m.start()] + "0" * 40 + cmd[m.end():]) == want:
            fixpre, fixpost = cmd[:m.start()], cmd[m.end():]
            break
# The command without --delete-branch, for a stacked PR's refusal; empty when
# dropping the flag's text would change another word.
nodelete = re.sub(r"[ \t]+--delete-branch(?=[ \t]|$)", "", cmd) if dels else ""
if nodelete and lex(nodelete) != [w for i, w in enumerate(full) if i not in dels]:
    nodelete = ""
# The one checked form: the shell below checks the PR's facts, and that
# --match-head-commit is present and names the head it read.
fields = dict(cmd=cmd, pr=num, repo=repo, match=match, fixpre=fixpre, fixpost=fixpost,
              delete="1" if dels else "", nodelete=nodelete)
sys.stdout.buffer.write(" ".join(f"{k}={shlex.quote(v)}" for k, v in fields.items()).encode())
sys.exit(3)
PY
    printf '\0%s' "$input"
  } | python3 -I -S -c 'import sys
code, _, payload = sys.stdin.buffer.read().decode("utf-8", "replace").partition("\0")
exec(code)'
}
block() { printf 'BLOCKED: %s\n' "$*" >&2; exit 2; }
# Every command goes to Python, which reads the command field itself: a text
# filter on the raw JSON can't soundly tell which commands to skip.
command -v python3 >/dev/null 2>&1 \
  || block "python3 is missing, so pr-merge-gate can't check this command for a merge."
out=$(formcheck)
case $? in
  0) exit 0 ;;
  3) eval "$out" ;;
  *) exit 2 ;;
esac
command -v jq >/dev/null 2>&1 \
  || block "jq is missing, so pr-merge-gate can't read this merge's PR. Install jq, then retry."
# jq sources $HOME/.jq into every program; with HOME=/dev/null no such file
# can exist, so a definition there can't change these filters.
jq() { env HOME=/dev/null jq "$@"; }
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] && cd "$cwd" 2>/dev/null
cantread() {
  # gh keeps GraphQL replies on disk, a rate-limit error included, and can
  # replay one after the budget has recovered.
  printf '%s' "$3" | grep -qi 'rate limit' && block "pr-merge-gate couldn't read the $1 of $2 ($(printf '%s' "$3" | head -n 3 | tr '\n' ' ')), so the merge is refused. Check both counters: \`gh api rate_limit\` for REST (core), and \`gh api graphql -f query='query{rateLimit{remaining resetAt}}'\` for GraphQL.
If the failing read's counter has points left, run \`gh config clear-cache\` and retry once; otherwise wait until that counter resets, then retry."
  block "pr-merge-gate couldn't read the $1 of $2 ($(printf '%s' "$3" | head -n 3 | tr '\n' ' ')), so the merge is refused. Retry once gh can read it: check \`gh auth status\` and \`gh api rate_limit\`."
}
# Over REST, which doesn't spend the GraphQL budget. The PR url names the
# base repo even when the head is a fork.
# The head repo is empty when GitHub has none, as for a deleted fork.
info=$(gh api "repos/$repo/pulls/$pr" \
  -q '.html_url + " " + .head.ref + " " + .head.sha + " " + (.head.repo.full_name // "")' 2>&1) \
  || cantread "head commit" "PR $pr in $repo" "$info"
set -f; set -- $info; set +f
[ $# -le 4 ] || cantread "head commit" "PR $pr" "$info"
url=${1-}; branch=${2-}; sha=${3-}; headrepo=${4-}
owner=$(printf '%s' "$url" | cut -d/ -f4)
name=$(printf '%s' "$url" | cut -d/ -f5)
num=$(printf '%s' "$url" | cut -d/ -f7)
# Spelled-out classes: a range like a-f can match uppercase in some locales.
case "$num" in *[!0123456789]*) cantread "head commit" "PR $pr" "$info" ;; esac
case "$sha" in *[!0123456789abcdef]*) cantread "head commit" "PR $pr" "$info" ;; esac
[ -n "$owner" ] && [ -n "$name" ] && [ -n "$num" ] && [ -n "$branch" ] && [ ${#sha} -eq 40 ] \
  || cantread "head commit" "PR $pr" "$info"

# 1. Checks on the head commit read above, by its SHA, so a later push can't
# swap in another commit's checks: its check runs and its commit statuses,
# every page of each at the largest page size. Each check run must be
# completed as success, neutral, or skipped, and each status success; none at
# all passes. gh prints one JSON object per page; anything else is unreadable,
# as is reading fewer check runs than a page's total_count.
at="$owner/$name commit $sha"
runs=$(gh api "repos/$owner/$name/commits/$sha/check-runs?per_page=100" --paginate 2>&1) \
  || cantread "check runs" "$at" "$runs"
failing=$(printf '%s' "$runs" | jq -rs '
  if length > 0 and all(.[]; type == "object" and (.check_runs | type) == "array"
      and (.total_count | type) == "number")
  then ([.[].check_runs[]] | length) as $read | (map(.total_count) | max) as $total
    | if $read < $total then error("read \($read) of \($total) check runs") else
      .[].check_runs[]
      | select(.status != "completed" or (.conclusion != "success"
          and .conclusion != "neutral" and .conclusion != "skipped"))
      | "  \(.name) (\(if .status == "completed" then .conclusion else .status end))"
      end
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
SELF_LINES=80
SELF_FILES=5
# per_page=100, the maximum, so a long thread costs as few requests as possible.
firsts=$(gh api "repos/$owner/$name/issues/$num/comments?per_page=100" --paginate \
  -q '.[] | select(.author_association == "OWNER" or .author_association == "MEMBER" or .author_association == "COLLABORATOR")
      | .body | split("\n")[0] | rtrimstr("\r")' 2>&1) \
  || cantread "comments" "$owner/$name#$num" "$firsts"
# A verdict for an older commit still counts when the head is ahead of it and
# every file changed since is a removed file under .pr-assets/. A compare
# lists at most 300 files, so a list that long may be cut short.
covered=
printf '%s\n' "$firsts" | grep -Fxq "$verdict" && covered=$sha
if [ -z "$covered" ]; then
  for old in $(printf '%s\n' "$firsts" | sed -n 's/^Blind review: pass \([0123456789abcdef]\{40\}\)$/\1/p' | sort -u); do
    diff=$(gh api "repos/$owner/$name/compare/$old...$sha?per_page=100" --paginate 2>&1) || {
      printf '%s' "$diff" | grep -qi 'rate limit' && cantread "compare from $old" "$owner/$name#$num" "$diff"
      continue
    }
    printf '%s' "$diff" | jq -es 'length > 0 and all(.[]; type == "object" and .status == "ahead")
      and ([.[] | .files // [] | .[]] | length > 0 and length < 300 and all(.[]; type == "object"
        and .status == "removed" and (.filename | type) == "string"
        and (.filename | startswith(".pr-assets/"))))' >/dev/null 2>&1 && { covered=$old; break; }
  done
fi
# A self-review verdict for the head counts when the PR is small: GitHub's own
# count of its added plus deleted lines and of its changed files, each within
# the limit. The pin below ties the merge to the head these counts describe.
selfverdict="Self review: pass $sha"
if [ -z "$covered" ] && printf '%s\n' "$firsts" | grep -Fxq "$selfverdict"; then
  size=$(gh api "repos/$owner/$name/pulls/$num" \
    -q '"\(.additions + .deletions) \(.changed_files)"' 2>&1) \
    || cantread "size" "$owner/$name#$num" "$size"
  set -f; set -- $size; set +f
  [ $# -eq 2 ] || cantread "size" "$owner/$name#$num" "$size"
  case "$1$2" in *[!0123456789]*) cantread "size" "$owner/$name#$num" "$size" ;; esac
  if [ ${#1} -le 6 ] && [ ${#2} -le 6 ] && [ "$1" -le $SELF_LINES ] && [ "$2" -le $SELF_FILES ]; then
    covered=$sha
  else
    block "$owner/$name#$num has a self-review verdict, but it changes $1 lines in $2 files. A self review covers at most $SELF_LINES changed lines in $SELF_FILES files.
After a blind review of this commit passes, post a PR comment whose first line is exactly:
  $verdict"
  fi
fi
[ -n "$covered" ] || block "$owner/$name#$num has no blind-review verdict for its head commit $sha.
After a blind review of this commit passes, post a PR comment (as the repo's
owner, a member, or a collaborator) whose first line is exactly:
  $verdict
for example: gh pr comment $num -R $owner/$name --body-file <file>
A PR that changes at most $SELF_LINES lines in $SELF_FILES files may instead carry, after you
have read its whole diff, the first line:
  $selfverdict
A verdict for an older commit doesn't count: a new commit needs a new review,
unless the only change since it is deleting files under .pr-assets/."

# 4. Pin the merge to the head checked above: with --match-head-commit, GitHub
# refuses the merge if the head moves after this check.
# The refusal prints the agent's own command, pinned: the pin goes at its end,
# or its value is replaced where the form check found it.
if [ "$match" != "$sha" ]; then
  if [ -z "$match" ]; then fix="$cmd --match-head-commit $sha"
  elif [ -n "$fixpre$fixpost" ]; then fix="$fixpre$sha$fixpost"
  else fix="${cmd%%"$match"*}$sha${cmd#*"$match"}"
  fi
  [ -z "$match" ] && block "this merge of $owner/$name#$num doesn't pin the head commit the gate checked. Run it with --match-head-commit, so GitHub refuses the merge if the head moves:
  $fix"
  block "this merge of $owner/$name#$num pins --match-head-commit $match, but the PR's head is $sha. Run it pinned to the head the gate checked:
  $fix"
fi

# 5. --delete-branch deletes the head branch, and GitHub then closes every
# open PR based on it. Only a head branch in this repo can be a base here; a
# fork's branch of the same name is another branch. One page is enough: any
# PR there refuses.
if [ -n "$delete" ]; then
  [ -n "$headrepo" ] || cantread "head repository" "$owner/$name#$num" "$info"
fi
if [ -n "$delete" ] && [ "$headrepo" = "$owner/$name" ]; then
  at="open PRs based on $branch"
  stacked=$(gh api "repos/$owner/$name/pulls?state=open&base=$(jq -rn --arg b "$branch" '$b | @uri')&per_page=100" 2>&1) \
    || cantread "$at" "$owner/$name" "$stacked"
  stacked=$(printf '%s' "$stacked" | jq -rs 'if length == 1 and (.[0] | type) == "array"
    then .[0][] | "  #\(.number) \(.html_url)" else error("not one list of PRs") end' 2>&1) \
    || cantread "$at" "$owner/$name" "$stacked"
  [ -n "$nodelete" ] || nodelete="(your command without --delete-branch)"
  [ -z "$stacked" ] || block "$owner/$name#$num's head branch $branch is the base of other open PRs, and --delete-branch would close them:
$stacked
Merge without deleting the branch:
  $nodelete"
fi
exit 0

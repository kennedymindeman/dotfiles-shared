#!/bin/sh
# SessionStart: surface divergence from remote. Informational only — never blocks, never pulls.
# The linker installs this as a symlink into the dotfiles checkout; a dry run
# shows whether that checkout would regenerate ~/.claude/CLAUDE.md.
self=$(readlink "$0" 2>/dev/null) || self=$0
dot=${self%/claude/hooks/*}
if [ -f "$dot/link.sh" ] && sh "$dot/link.sh" --dry-run 2>/dev/null | grep -q '\.claude/CLAUDE\.md$'; then
  echo "Claude rules are stale: ~/.claude/CLAUDE.md differs from $dot; run: sh $dot/link.sh"
fi
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
branch=$(git branch --show-current)
[ -n "$branch" ] || exit 0
remote=$(git config --get "branch.$branch.remote") || exit 0
if [ "$remote" != "." ] && ! git fetch --quiet "$remote" 2>/dev/null; then
  echo "git fetch failed (offline or auth issue) -- remote freshness unknown; local refs may be stale."
  exit 0
fi
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || exit 0
counts=$(git rev-list --count --left-right "$upstream...HEAD" 2>/dev/null) || exit 0
set -- $counts
[ "$#" -eq 2 ] || exit 0
behind=$1; ahead=$2
if [ "$behind" -gt 0 ]; then
  echo "Heads up: $(git branch --show-current) is $behind commit(s) behind $upstream (ahead $ahead)."
  echo "Unless old-branch work is deliberate, pull/rebase before starting ticket work."
fi
exit 0

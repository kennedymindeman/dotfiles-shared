#!/bin/sh
# PreToolUse hook (matcher: Write|Edit) for the reviewer agent. Allows file
# writes only under ${TMPDIR:-/tmp}/review-*, so probes never touch the repo
# under review. Exit 2 = block; fails closed when the input can't be parsed.
set -u
command -v jq >/dev/null 2>&1 || { echo "review-scratch-only: jq not found" >&2; exit 2; }
path=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
tmp=${TMPDIR:-/tmp}
tmp=${tmp%/}
case "$path" in
  *..*) ;;
  "$tmp"/review-?*/?*) exit 0 ;;
esac
echo "Blocked: reviewer writes only go under $tmp/review-<id>/ (got: ${path:-no path}). Create one with mktemp -d \"\${TMPDIR:-/tmp}/review-XXXXXX\"." >&2
exit 2

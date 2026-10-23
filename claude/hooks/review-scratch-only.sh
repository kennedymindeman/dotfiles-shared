#!/bin/sh
# PreToolUse hook (matcher: Write|Edit) for the reviewer agent. Allows file
# writes only inside a scratch dir made by
#   mktemp -d "${TMPDIR:-/tmp}/claude-review-XXXXXX"
# that the current user owns with mode 700. Paths are compared after
# resolving symlinks, so a link from the scratch dir into the repo is blocked.
# Exit 2 = block; fails closed when the input can't be parsed.
set -u
block() {
  echo "Blocked: reviewer writes only go in a dir made by mktemp -d \"\${TMPDIR:-/tmp}/claude-review-XXXXXX\" ($1: ${path:-no path})." >&2
  exit 2
}
path=
command -v jq >/dev/null 2>&1 || block "jq not found"
path=$(jq -r '.tool_input.file_path // empty' 2>/dev/null)
case "$path" in /*) ;; *) block "not an absolute path" ;; esac
case "/$path/" in */../*|*/./*) block "dot path component" ;; esac
[ -L "$path" ] && block "target is a symlink"
[ -d "$path" ] && block "target is a directory"
tmp=$(cd "${TMPDIR:-/tmp}" 2>/dev/null && pwd -P) || block "temp dir missing"

# Resolve the deepest existing ancestor; components below it don't exist yet.
dir=$(dirname "$path")
while [ ! -d "$dir" ]; do
  { [ -e "$dir" ] || [ -L "$dir" ]; } && block "ancestor is not a directory"
  dir=$(dirname "$dir")
done
real=$(cd "$dir" && pwd -P) || block "cannot resolve parent"

case "$real/" in
  "$tmp"/claude-review-?*/*) ;;
  *) block "outside the scratch dir" ;;
esac
rest=${real#"$tmp"/}
scratch=$tmp/${rest%%/*}
[ -n "$(find "$scratch" -maxdepth 0 -type d -user "$(id -u)" -perm 700 2>/dev/null)" ] \
  || block "scratch dir not owned by you with mode 700"
exit 0

#!/bin/sh
# ls/ll/la/lt and cat pick eza/bat at call time and fall back to the system
# tools when those are missing from the current PATH.
set -eu

command -v zsh >/dev/null || { echo "skip: zsh not installed"; exit 0; }
repo=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/test-zshrc-fallback.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/bin" "$tmp/home"
for tool in eza bat; do
  printf '#!/bin/sh\necho %s "$@"\n' "$tool" > "$tmp/bin/$tool"
  chmod +x "$tmp/bin/$tool"
done
echo hello > "$tmp/file"

run() {  # run <PATH while calling> <command...>
  path=$1; shift
  HOME="$tmp/home" PATH="$tmp/bin:/usr/bin:/bin" zsh -f -c \
    'source "$1"; shift; PATH=$1; shift; "$@"' zsh "$repo/zshrc" "$path" "$@"
}

fail=0
check() {  # check <expected> <actual> <label>
  if [ "$1" = "$2" ]; then echo "ok: $3"; else echo "FAIL: $3: expected [$1], got [$2]"; fail=1; fi
}

narrow=/usr/bin:/bin
wide="$tmp/bin:/usr/bin:/bin"

check "eza $tmp" "$(run "$wide" ls "$tmp")" "ls uses eza when present"
check "eza -l --git $tmp" "$(run "$wide" ll "$tmp")" "ll uses eza when present"
check "eza -la --git $tmp" "$(run "$wide" la "$tmp")" "la uses eza when present"
check "eza --tree --level=2 $tmp" "$(run "$wide" lt "$tmp")" "lt uses eza when present"
check "bat $tmp/file" "$(run "$wide" cat "$tmp/file")" "cat uses bat when present"
check "hello" "$(run "$wide" cat -u "$tmp/file")" "cat passes dash-flags to system cat"

check "$(/bin/ls "$tmp")" "$(run "$narrow" ls "$tmp")" "ls falls back to system ls"
check "$(/bin/ls -l "$tmp")" "$(run "$narrow" ll "$tmp")" "ll falls back to system ls -l"
check "$(/bin/ls -la "$tmp")" "$(run "$narrow" la "$tmp")" "la falls back to system ls -la"
check "$(/bin/ls "$tmp")" "$(run "$narrow" lt "$tmp")" "lt falls back to system ls"
check "hello" "$(run "$narrow" cat "$tmp/file")" "cat falls back to system cat"

exit $fail

#!/bin/bash
# Claude Code statusLine: chrome only. Directory/git/branch are already shown
# by starship on the prompt line below, so this deliberately omits them.
# Shows: short model name (+1M tag for large-context variants), context
# window used %, cache-read share with miss/cold warnings, and Claude.ai rate-limit usage when present. Any
# missing field is dropped silently rather than printing "null" or erroring.
command -v jq >/dev/null 2>&1 || exit 0
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name // empty')
model=${model#Claude }
ctx_size=$(echo "$input" | jq -r '.context_window.context_window_size // empty')
if [ -n "$ctx_size" ] && [ "$ctx_size" -ge 1000000 ] 2>/dev/null; then
  model="$model (1M)"
fi

remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
ctx_str=""
[ -n "$remaining" ] && ctx_str=$(awk -v r="$remaining" 'BEGIN{printf "ctx %.0f%% used", 100 - r}')

# Cache: share of the last request's input served from cache reads. A request
# that writes more than it reads rebuilt the prefix at full price (MISS).
# Assumes the 1h cache TTL, so a transcript idle 60m+ means the next prompt
# pays to rebuild the cache (COLD).
cache_str=""
read -r c_read c_write c_in <<<"$(echo "$input" | jq -r '.context_window.current_usage | "\(.cache_read_input_tokens // 0) \(.cache_creation_input_tokens // 0) \(.input_tokens // 0)"')"
c_total=$(( ${c_read:-0} + ${c_write:-0} + ${c_in:-0} ))
if [ "$c_total" -gt 0 ]; then
  cache_str="cache $(( c_read * 100 / c_total ))%"
  [ "$c_write" -gt "$c_read" ] && cache_str="$cache_str \033[0;33mMISS $((c_write / 1000))k\033[0;2m"
fi
tpath=$(echo "$input" | jq -r '.transcript_path // empty')
if [ -f "$tpath" ]; then
  idle=$(( $(date +%s) - $(stat -f %m "$tpath" 2>/dev/null || stat -c %Y "$tpath") ))
  if [ "$idle" -ge 3600 ]; then
    cache_str="$cache_str \033[0;31mCOLD\033[0;2m"
  elif [ "$idle" -ge 3000 ]; then
    cache_str="$cache_str \033[0;33mcold in $(( (3600 - idle) / 60 ))m\033[0;2m"
  fi
fi

# Cache the context fill per session so the UserPromptSubmit hook in
# settings.json can surface it to Claude (hooks don't receive context_window).
sid=$(echo "$input" | jq -r '.session_id // empty')
if [ -n "$sid" ] && [ -n "$remaining" ]; then
  used_tok=$(echo "$input" | jq -r '.context_window.current_usage | ((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0))')
  mkdir -p "$HOME/.cache/claude-ctx"
  printf 'ctx: %dk used of %dk (%.0f%% left)' "$((used_tok / 1000))" "$((${ctx_size:-0} / 1000))" "$remaining" > "$HOME/.cache/claude-ctx/$sid"
fi

five=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
week=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
rl_str=""
[ -n "$five" ] && rl_str="5h:$(printf '%.0f' "$five")%"
if [ -n "$week" ]; then
  [ -n "$rl_str" ] && rl_str="$rl_str 7d:$(printf '%.0f' "$week")%" || rl_str="7d:$(printf '%.0f' "$week")%"
fi

# Pending waits: any tool may drop a one-line file in
# ~/.cache/claude-wait/<session_id>/ saying what the session waits on. A file
# named after a PID is dropped once that process is gone.
wait_str=""
wdir="$HOME/.cache/claude-wait/$sid"
if [ -n "$sid" ] && [ -d "$wdir" ]; then
  for f in "$wdir"/*; do
    [ -f "$f" ] || continue
    name=${f##*/}
    case $name in *[!0-9]*) ;; *) kill -0 "$name" 2>/dev/null || { rm -f "$f"; continue; } ;; esac
    line=$(head -1 "$f")
    [ -n "$line" ] && wait_str="${wait_str:+$wait_str; }$line"
  done
fi
[ -n "$wait_str" ] && wait_str="\033[0;33mwaiting: $wait_str\033[0;2m"

out=""
for part in "$wait_str" "$model" "$ctx_str" "$cache_str" "$rl_str"; do
  [ -z "$part" ] && continue
  if [ -z "$out" ]; then out="$part"; else out="$out · $part"; fi
done

# Dim so it reads as chrome under starship's colored, glyph-heavy prompt.
[ -n "$out" ] && printf '\033[2m%b\033[0m' "$out"

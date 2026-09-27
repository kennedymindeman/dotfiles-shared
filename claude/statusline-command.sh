#!/bin/bash
# Claude Code statusLine: chrome only. Directory/git/branch are already shown
# by starship on the prompt line below, so this deliberately omits them.
# Shows: short model name (+1M tag for large-context variants), context
# window remaining %, and Claude.ai rate-limit usage when present. Any
# missing field is dropped silently rather than printing "null" or erroring.
input=$(cat)

model=$(echo "$input" | jq -r '.model.display_name // empty')
model=${model#Claude }
ctx_size=$(echo "$input" | jq -r '.context_window.context_window_size // empty')
if [ -n "$ctx_size" ] && [ "$ctx_size" -ge 1000000 ] 2>/dev/null; then
  model="$model (1M)"
fi

remaining=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
ctx_str=""
[ -n "$remaining" ] && ctx_str=$(printf '%.0f%% ctx' "$remaining")

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

out=""
for part in "$model" "$ctx_str" "$rl_str"; do
  [ -z "$part" ] && continue
  if [ -z "$out" ]; then out="$part"; else out="$out · $part"; fi
done

# Dim so it reads as chrome under starship's colored, glyph-heavy prompt.
[ -n "$out" ] && printf '\033[2m%s\033[0m' "$out"

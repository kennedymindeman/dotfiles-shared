# History. /etc/zshrc runs first and caps this at SAVEHIST=1000 with no
# timestamps; override here. HISTSIZE must stay >= SAVEHIST or saves truncate
# to the smaller number. HIST_IGNORE_SPACE: a leading space keeps a command
# out of history — the manual escape hatch for secrets.
HISTFILE=~/.zsh_history
HISTSIZE=200000
SAVEHIST=200000
setopt EXTENDED_HISTORY HIST_IGNORE_DUPS HIST_REDUCE_BLANKS HIST_IGNORE_SPACE INC_APPEND_HISTORY_TIME

# Programmable completion. Must run before anything calls compdef (zoxide's
# init below, fzf, the source wrapper further down); also required by
# autosuggestions' "completion" strategy, which silently did nothing without
# it. -C trusts the existing ~/.zcompdump and skips the per-shell security
# audit (~16ms); run `compinit` by hand after installing something new to fpath.
autoload -Uz compinit && compinit -C

# Use a friendly per-machine name when configured, otherwise keep the hostname.
if [[ -z ${MACHINE_NAME:-} ]]; then
  if [[ -r "$HOME/.machine-name" ]]; then
    IFS= read -r MACHINE_NAME < "$HOME/.machine-name"
  fi
  export MACHINE_NAME=${MACHINE_NAME:-$(hostname -s)}
fi

# After compinit, so zoxide's `compdef __zoxide_z_complete z` actually
# registers — it guards on compdef existing and silently skips it otherwise.
command -v zoxide >/dev/null && eval "$(zoxide init zsh)"
command -v starship >/dev/null && eval "$(starship init zsh)"
zstyle ':completion:*' menu select                      # arrow-key menu
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Za-z}'  # case-insensitive
_comp_options+=(globdots)  # offer dotfiles without typing the leading dot —
                           # same show-hidden policy as the fd/fzf lines below

# Bare `source <TAB>` completes straight to the venv activate script (sole
# candidate auto-inserts); any typed prefix falls back to normal file
# completion. Covers . as well; */ pattern catches venvs one directory down.
_source_venvs() {
  local -a venvs
  venvs=( {.,}venv/bin/activate(N) */{.,}venv/bin/activate(N) )
  if (( $#venvs )) && [[ -z $words[CURRENT] ]]; then
    _describe -t venvs 'venv activate' venvs && return
  fi
  _source "$@"
}
compdef _source_venvs source .

# activate the venv here without typing any path
act() {
  local f
  for f in .venv/bin/activate venv/bin/activate; do
    [ -f "$f" ] && { source "$f"; return; }
  done
  echo "no venv found" >&2; return 1
}

# Current fzf emits its own bindings; older distro packages ship shell scripts.
if command -v fzf >/dev/null; then
  if _dotfiles_fzf_init=$(fzf --zsh 2>/dev/null); then
    eval "$_dotfiles_fzf_init"
  elif [[ -f ~/.fzf.zsh ]]; then
    source ~/.fzf.zsh
  else
    for _dotfiles_fzf_dir in /usr/share/fzf /usr/share/fzf/shell "${HOMEBREW_PREFIX:-/opt/homebrew}/opt/fzf/shell"; do
      if [[ -f "$_dotfiles_fzf_dir/key-bindings.zsh" ]]; then
        source "$_dotfiles_fzf_dir/key-bindings.zsh"
        [[ -f "$_dotfiles_fzf_dir/completion.zsh" ]] && source "$_dotfiles_fzf_dir/completion.zsh"
        break
      fi
    done
  fi
  unset _dotfiles_fzf_init _dotfiles_fzf_dir
fi
# fzf pickers use fd: respects .gitignore (so .venv etc. stay out), but do
# show hidden files — same policy as the fd alias and telescope pickers
if command -v fd >/dev/null; then
  export FZF_CTRL_T_COMMAND='fd --type f --hidden --exclude .git'
  export FZF_ALT_C_COMMAND='fd --type d --hidden --exclude .git'
fi

# git, crontab, and anything else that respects the convention opens nvim
if command -v nvim >/dev/null; then
  export EDITOR=nvim
  export VISUAL=nvim
fi
# Colored man pages via bat (col strips the overstrike bold/underline that
# bat can't parse). Falls back to plain less if bat is missing.
command -v bat >/dev/null && command -v col >/dev/null && export MANPAGER="sh -c 'col -bx | bat -l man -p'"

# Declare vi keymap explicitly. zsh already picks it implicitly because $EDITOR
# contains "vi" (n[vi]m), and starship's vimcmd_symbol depends on it — but
# without this line, changing $EDITOR to something without "vi" would silently
# flip the line editor to emacs and change what C-h/C-k/C-l do.
bindkey -v

# breakpoint() opens pudb (TUI debugger) instead of plain pdb.
# Ceiling: in a venv/uv env that lacks pudb, breakpoint() will ImportError —
# add pudb to that env (uv add --dev pudb) or `unset PYTHONBREAKPOINT` there.
export PYTHONBREAKPOINT=pudb.set_trace

# Debugger on failure, opt-in. With DEBUG_ON_FAIL=1 in the environment, the
# normal start commands (`python x.py`, `uv run ...`, `pytest`) stop in a
# post-mortem debugger instead of printing a traceback and exiting — no special
# invocation. Enable it for a shell (or a container, see README):
#   export DEBUG_ON_FAIL=1 && exec zsh
# python/sitecustomize.py does the work for scripts: site.py imports
# `sitecustomize` at startup, so PYTHONPATH reaches every interpreter including
# uv's per-project venvs. pytest doesn't route failures through sys.excepthook,
# hence --pdb, appended so a project's own PYTEST_ADDOPTS survives.
if [[ ${DEBUG_ON_FAIL:-} == 1 ]]; then
  _dotfiles_python="${${(%):-%N}:A:h}/python"
  [[ :${PYTHONPATH:-}: == *":$_dotfiles_python:"* ]] ||
    export PYTHONPATH="$_dotfiles_python${PYTHONPATH:+:$PYTHONPATH}"
  unset _dotfiles_python
  [[ " ${PYTEST_ADDOPTS:-} " == *" --pdb "* ]] ||
    export PYTEST_ADDOPTS="${PYTEST_ADDOPTS:+$PYTEST_ADDOPTS }--pdb"
fi

# bat as a nicer cat, but hand any dash-flags (cat -v/-A/-n/…, which bat
# rejects) back to the real cat so `cat -v` and flag-passing scripts keep working.
if command -v bat >/dev/null; then
  cat() { if [[ ${1-} == -* ]]; then command cat "$@"; else command bat "$@"; fi }
fi
if command -v eza >/dev/null; then
  alias ls='eza'
  alias ll='eza -l --git'
  alias la='eza -la --git'
  alias lt='eza --tree --level=2'
fi
command -v fd >/dev/null && alias fd='fd --hidden --exclude .git'
if command -v nvim >/dev/null; then
  alias vim='nvim'
  alias vi='nvim'
fi

[ -f "$HOME/.config/copilot-shell-defaults.sh" ] && . "$HOME/.config/copilot-shell-defaults.sh"

# Syntax highlighting (load first so autosuggestions color doesn't conflict)
# and autosuggestions. Search Homebrew and Linux package locations.
for _dotfiles_plugin in zsh-syntax-highlighting zsh-autosuggestions; do
  for _dotfiles_share in "${HOMEBREW_PREFIX:-/opt/homebrew}/share" /usr/local/share /usr/share /usr/share/zsh/plugins; do
    if [[ -f "$_dotfiles_share/$_dotfiles_plugin/$_dotfiles_plugin.zsh" ]]; then
      source "$_dotfiles_share/$_dotfiles_plugin/$_dotfiles_plugin.zsh"
      break
    fi
  done
done
unset _dotfiles_plugin _dotfiles_share
ZSH_AUTOSUGGEST_STRATEGY=(history completion)

# Alt+Z: zoxide interactive picker.
if command -v zoxide >/dev/null; then
  zi-widget() { zi; zle reset-prompt }
  zle -N zi-widget
  bindkey '^[z' zi-widget
fi

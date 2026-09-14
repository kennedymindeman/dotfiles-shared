-- WezTerm config. Port of ghostty/config: Catppuccin Mocha.
-- (ghostty's macos-option-as-alt has no Windows equivalent — Alt already
--  sends Alt here, so shell chords like Alt+z / Alt+c work as-is.)
local wezterm = require 'wezterm'
local act = wezterm.action

-- Seamless Ctrl-h/j/k/l between wezterm panes and nvim splits (tmux-style,
-- for machines without tmux). When nvim is the pane's foreground process the
-- key is passed through so vim-tmux-navigator moves the nvim split; otherwise
-- wezterm moves between its own panes.
local function is_nvim(pane)
  local p = pane:get_foreground_process_name()
  return p ~= nil and p:find 'nvim' ~= nil
end

local dirs = { h = 'Left', j = 'Down', k = 'Up', l = 'Right' }
local function nav(key)
  return {
    key = key,
    mods = 'CTRL',
    action = wezterm.action_callback(function(win, pane)
      if is_nvim(pane) then
        win:perform_action(act.SendKey { key = key, mods = 'CTRL' }, pane)
      else
        win:perform_action(act.ActivatePaneDirection(dirs[key]), pane)
      end
    end),
  }
end

return {
  color_scheme = 'Catppuccin Mocha',

  -- Catppuccin's default cursor is Rosewater (#f5e0dc) — a cream block that
  -- looks jarring on an empty input line in TUIs like the Copilot CLI.
  -- Mauve blends with the theme while staying visible.
  colors = {
    cursor_bg = '#cba6f7',
    cursor_border = '#cba6f7',
    cursor_fg = '#1e1e2e',
  },

  hide_tab_bar_if_only_one_tab = true,
  window_decorations = 'RESIZE',

  -- Windows defaults to cmd.exe; use PowerShell 7 there.
  default_prog = wezterm.target_triple:find('windows') and { 'pwsh.exe', '-NoLogo' } or nil,

  keys = {
    nav 'h',
    nav 'j',
    nav 'k',
    nav 'l',
    -- split panes (tmux-ish): Ctrl+Shift+ - / \
    { key = '\\', mods = 'CTRL|SHIFT', action = act.SplitHorizontal { domain = 'CurrentPaneDomain' } },
    { key = '-', mods = 'CTRL|SHIFT', action = act.SplitVertical { domain = 'CurrentPaneDomain' } },
  },
}

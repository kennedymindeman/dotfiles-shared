# Catppuccin Mocha theme for pudb — matches the nvim catppuccin-mocha colorscheme.
# Custom-theme contract: pudb exec's this file with `palette` (a dict to mutate) and
# `add_setting` already in scope. We also import add_setting and bind `palette` from
# globals() so static linters resolve them; both are the same objects pudb injects.
#
# NOTE: pudb only moves a color into urwid's high-color slot if it starts with "h"
# (a 256-color index). Plain #rrggbb stays in the 16-color slot and fails to register,
# so these are the nearest xterm-256 approximations of the Catppuccin Mocha palette.
from pudb.themes.utils import add_setting

palette = globals().get("palette", {})

# --- Catppuccin Mocha, approximated as xterm-256 indices ---
base     = "h235"  # #1e1e2e
surface0 = "h237"  # #313244
surface1 = "h239"  # #45475a
overlay0 = "h243"  # #6c7086
overlay2 = "h103"  # #9399b2
text     = "h189"  # #cdd6f4
blue     = "h111"  # #89b4fa
lavender = "h147"  # #b4befe
sapphire = "h117"  # #74c7ec
sky      = "h116"  # #89dceb
teal     = "h115"  # #94e2d5
green    = "h114"  # #a6e3a1
yellow   = "h223"  # #f9e2af
peach    = "h216"  # #fab387
maroon   = "h174"  # #eba0ac
red      = "h211"  # #f38ba8
mauve    = "h183"  # #cba6f7

palette.update({
    # --- base UI ---
    "background":         (text, surface0),
    "selectable":         (text, base),
    "focused selectable": (text, surface1),
    "hotkey":             (add_setting(mauve, "underline"), surface0),
    "highlighted":        (base, green),

    # --- general UI ---
    "input":          (text, base),
    "focused input":  (base, blue),
    "warning":        (add_setting(base, "bold"), red),
    "dialog title":   (add_setting(text, "bold"), surface0),
    "group head":     (add_setting(blue, "bold"), surface0),
    "button":         (text, surface0),
    "focused button": (base, blue),
    "focused sidebar": (base, blue),
    "value":          (add_setting(yellow, "bold"), base),

    # --- source view ---
    "source":                 (text, base),
    "highlighted source":     (text, surface1),
    "current source":         (add_setting(base, "bold"), lavender),
    "current focused source": (add_setting(base, "bold"), blue),
    "breakpoint source":      (add_setting(base, "bold"), red),
    "current breakpoint source": (base, maroon),
    "line number":            (overlay0, base),
    "current line marker":    (add_setting(green, "bold"), base),
    "breakpoint marker":      (add_setting(red, "bold"), base),

    # --- sidebar ---
    "sidebar two":   (sapphire, base),
    "sidebar three": (teal, base),

    # --- variables view ---
    "return label":         (add_setting(yellow, "bold"), base),
    "return value":         (base, teal),
    "focused return label": (base, blue),

    # --- stack ---
    "current frame name":     (add_setting(green, "bold"), base),
    "current frame class":    (blue, base),
    "current frame location": (sky, base),
    "focused current frame name":     (add_setting(base, "bold"), blue),
    "focused current frame class":    (base, blue),
    "focused current frame location": (base, blue),

    # --- breakpoints view ---
    "breakpoint":                  (text, base),
    "disabled breakpoint":         (overlay0, base),
    "current breakpoint":          (add_setting(red, "bold"), base),
    "disabled current breakpoint": (add_setting(overlay0, "bold"), base),
    "focused current breakpoint":  (add_setting(base, "bold"), blue),

    # --- shell ---
    "command line edit":           (text, base),
    "command line prompt":         (add_setting(mauve, "bold"), base),
    "command line input":          (text, base),
    "command line error":          (add_setting(red, "bold"), base),
    "command line clear button":   (add_setting(text, "bold"), base),
    "command line focused button": (base, blue),

    # --- syntax highlighting (mirrors the nvim catppuccin treesitter mapping) ---
    "keyword":     (mauve, base),
    "operator":    (sky, base),
    "pseudo":      (mauve, base),
    "function":    (add_setting(blue, "bold"), base),
    "builtin":     (peach, base),
    "literal":     (peach, base),
    "string":      (green, base),
    "docstring":   (green, base),
    "backtick":    (green, base),
    "punctuation": (overlay2, base),
    "comment":     (add_setting(overlay0, "italics"), base),
    "exception":   (add_setting(red, "bold"), base),
})

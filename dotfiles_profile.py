"""Resolve the same explicit profile for package installation and configuration."""

import os
from pathlib import Path


def resolve_profile(requested=None, home=None):
    home = Path(home) if home is not None else Path.home()
    marker = home / ".dotfiles-env"
    selected = requested or os.environ.get("DOTFILES_ENV")
    if not selected and marker.exists():
        selected = marker.read_text(encoding="utf-8").strip()
    if selected not in ("work", "home"):
        raise ValueError(
            "select --profile work or --profile home; no default profile is assumed"
        )
    return selected

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import link

TARGET = "AppData/Local/Microsoft/Windows Terminal/Fragments/dotfiles/catppuccin-mocha.json"
COLORS = {"black", "red", "green", "yellow", "blue", "purple", "cyan", "white"}


class WindowsTerminalFragmentTests(unittest.TestCase):
    def test_fragment_is_installed_on_windows_only(self):
        self.assertIn(TARGET, link.configured_files(ROOT, "windows"))
        self.assertNotIn(TARGET, link.configured_files(ROOT, "darwin"))

    def test_fragment_is_one_windows_terminal_accepts(self):
        source = link.configured_files(ROOT, "windows")[TARGET]
        fragment = json.loads((ROOT / source).read_text(encoding="utf-8"))
        # Windows Terminal drops a fragment scheme that lacks a name or any
        # of the 16 table colors.
        required = {"name"} | COLORS | {f"bright{c.capitalize()}" for c in COLORS}
        names = set()
        for scheme in fragment["schemes"]:
            self.assertLessEqual(required, scheme.keys())
            names.add(scheme["name"])
        self.assertTrue(fragment["profiles"])
        for profile in fragment["profiles"]:
            # A guid or name would make this a new profile, not an update.
            self.assertEqual(
                profile.keys(),
                {"updates", "colorScheme", "padding", "scrollbarState", "font"},
            )
            self.assertIn(profile["colorScheme"], names)
            # No blank border or scrollbar strip around the text.
            self.assertEqual(profile["padding"], "0")
            self.assertEqual(profile["scrollbarState"], "hidden")
            # Starship prompt icons need a Nerd Font.
            self.assertEqual(profile["font"], {"face": "JetBrainsMono Nerd Font"})


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path

PROFILE = Path(__file__).resolve().parents[1] / "powershell/Microsoft.PowerShell_profile.ps1"


class PowerShellProfileTests(unittest.TestCase):
    def test_profile_does_not_leave_strict_mode_on(self):
        code = [
            line.split("#", 1)[0]
            for line in PROFILE.read_text(encoding="utf-8").splitlines()
        ]
        calls = [line for line in code if re.search(r"\bSet-StrictMode\b", line, re.I)]
        if calls:
            self.assertRegex(
                calls[-1],
                re.compile(r"-Off\b", re.I),
                "the profile is dot-sourced, so strict mode it turns on stays on "
                "for the whole session",
            )


if __name__ == "__main__":
    unittest.main()

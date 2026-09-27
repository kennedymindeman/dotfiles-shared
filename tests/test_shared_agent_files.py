import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAL = re.compile(
    r"kennedy|telegram|tg-send|\bmini\b|\blaptop\b|tailnet|~/dotfiles\b|/Users/"
    r"|~/wiki|journal|cloudflare",
    re.IGNORECASE,
)


class SharedAgentFileTests(unittest.TestCase):
    def test_agent_files_contain_no_personal_content(self):
        paths = [
            *sorted((ROOT / "claude").rglob("*")),
            *sorted((ROOT / "copilot/agents").rglob("*")),
        ]
        files = [path for path in paths if path.is_file()]
        self.assertTrue(files)
        for path in files:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                match = PERSONAL.search(path.read_text(encoding="utf-8"))
                self.assertIsNone(match, match and match.group())


if __name__ == "__main__":
    unittest.main()

"""Tests for the hardened Copilot settings installer."""

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "apply_copilot_settings",
    Path(__file__).resolve().parents[1] / "bin/apply-copilot-settings.py",
)
settings_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings_module)


class CopilotSettingsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = self.root / ".copilot/settings.json"
        self.subagents = self.root / "subagents.json"
        self.subagents.write_text(
            json.dumps(
                {
                    "code-review": {"model": "gpt-5.6-sol"},
                    "researcher": {"effortLevel": "xhigh"},
                }
            ),
            encoding="utf-8",
        )

    def apply(self, platform, home, sandbox_enabled=True):
        settings_module.apply_settings(
            self.settings,
            self.subagents,
            home,
            platform,
            sandbox_enabled=sandbox_enabled,
        )
        return json.loads(self.settings.read_text(encoding="utf-8"))

    def test_applies_strict_defaults_and_preserves_unrelated_settings(self):
        self.settings.parent.mkdir()
        self.settings.write_text(
            json.dumps(
                {
                    "model": "gpt-5.6-sol",
                    "allowedUrls": ["https://docs.github.com"],
                    "disabledMcpServers": ["custom-write-server"],
                    "sandbox": {
                        "userPolicy": {
                            "network": {"proxy": {"url": "http://proxy.example"}},
                            "filesystem": {"deniedPaths": ["/existing/secret"]},
                        }
                    },
                    "subagents": {
                        "agents": {"existing": {"effortLevel": "low"}}
                    },
                }
            ),
            encoding="utf-8",
        )

        result = self.apply("unix", "/Users/tester")

        self.assertEqual(result["model"], "gpt-5.6-sol")
        self.assertEqual(result["allowedUrls"], ["https://docs.github.com"])
        self.assertEqual(
            result["disabledMcpServers"],
            ["custom-write-server", "github-mcp-server"],
        )
        self.assertEqual(result["defaultPermissionMode"], "manual")
        self.assertTrue(result["experimental"])
        self.assertTrue(result["sandbox"]["enabled"])
        self.assertFalse(result["sandbox"]["allowBypass"])
        self.assertFalse(result["sandbox"]["allowDevToolAccess"])
        self.assertFalse(result["sandbox"]["auth"]["git"])
        self.assertFalse(result["sandbox"]["auth"]["gh"])
        self.assertTrue(
            result["sandbox"]["userPolicy"]["network"]["allowOutbound"]
        )
        self.assertFalse(
            result["sandbox"]["userPolicy"]["network"]["allowLocalNetwork"]
        )
        self.assertEqual(
            result["sandbox"]["userPolicy"]["network"]["proxy"],
            {"url": "http://proxy.example"},
        )
        self.assertIn(
            "/Users/tester/.ssh",
            result["sandbox"]["userPolicy"]["filesystem"]["deniedPaths"],
        )
        self.assertIn(
            "/existing/secret",
            result["sandbox"]["userPolicy"]["filesystem"]["deniedPaths"],
        )
        self.assertEqual(
            result["subagents"]["agents"]["existing"]["effortLevel"], "low"
        )
        self.assertEqual(
            result["subagents"]["agents"]["researcher"]["effortLevel"], "xhigh"
        )
        self.assertEqual(
            result["subagents"]["agents"]["code-review"]["model"], "gpt-5.6-sol"
        )
        self.assertTrue(result["footer"]["showSandbox"])
        self.assertTrue(result["footer"]["showYolo"])

    def test_writes_windows_sensitive_paths(self):
        result = self.apply("windows", r"C:\Users\tester")
        denied = result["sandbox"]["userPolicy"]["filesystem"]["deniedPaths"]

        self.assertIn(r"C:\Users\tester\.ssh", denied)
        self.assertIn(r"C:\Users\tester\.gnupg", denied)
        self.assertIn(r"C:\Users\tester\AppData\Roaming\Bitwarden", denied)
        self.assertIn(
            r"C:\Users\tester\AppData\Roaming\GitHub CLI",
            denied,
        )
        self.assertNotIn("/Users/tester/.ssh", denied)

    def test_disables_sandbox_on_unsupported_windows(self):
        result = self.apply(
            "windows",
            r"C:\Users\tester",
            sandbox_enabled=False,
        )

        self.assertFalse(result["sandbox"]["enabled"])
        self.assertEqual(result["defaultPermissionMode"], "manual")
        self.assertFalse(result["sandbox"]["allowBypass"])

    def test_reapplying_settings_is_idempotent(self):
        first = self.apply("unix", "/Users/tester")
        second = self.apply("unix", "/Users/tester")

        self.assertEqual(second, first)

    def test_rejects_invalid_existing_shape_without_replacing_file(self):
        self.settings.parent.mkdir()
        original = '{"sandbox": []}\n'
        self.settings.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "sandbox must be an object"):
            settings_module.apply_settings(
                self.settings,
                self.subagents,
                "/Users/tester",
                "unix",
            )

        self.assertEqual(self.settings.read_text(encoding="utf-8"), original)

    @unittest.skipIf(os.name == "nt", "POSIX modes are not meaningful on Windows")
    def test_settings_file_is_private(self):
        self.apply("unix", "/Users/tester")
        self.assertEqual(self.settings.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

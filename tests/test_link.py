import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import link
from dotfiles_profile import resolve_profile


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo, self.home, self.overlay = [
            self.base / name for name in ("shared", "user", "private")
        ]
        for path in (self.repo, self.home, self.overlay):
            path.mkdir()
        for name, content in {
            "profiles/shared.json": json.dumps(
                {"files": {".config/shared.txt": "shared.txt"}}
            ),
            "shared.txt": "shared configuration\n",
            "gitconfig": "[user]\n\tuseConfigOnly = true\n",
            "agents/core.md": "Use focused changes.\n",
            "agents/work.md": "Follow employer rules.\n",
            "copilot/copilot-instructions.md": "Write clearly.\n",
            "zshrc": "# shared shell\n",
            "copilot/shell-defaults.sh": "# shared launcher\n",
            "powershell/Microsoft.PowerShell_profile.ps1": "# shared shell\n",
            "tmux.conf": "# shared tmux\n",
            "herdr/config.toml": (ROOT / "herdr/config.toml").read_text(encoding="utf-8"),
        }.items():
            self.write(self.repo / name, content)
        self.write(
            self.repo / "bin/apply-copilot-settings.py",
            (ROOT / "bin/apply-copilot-settings.py").read_text(),
        )
        for name, content in {
            "profiles/home.json": json.dumps(
                {"files": {".config/private.txt": "private.txt"}}
            ),
            "private.txt": "private overlay\n",
            "gitconfig.home": "[user]\n\tname = Personal\n",
            "agents/home.md": "Personal automation.\n",
            "tmux.home.conf": "# private tmux\n",
            "copilot/subagents.json": '{"research": {"model": "personal-model"}}',
        }.items():
            self.write(self.overlay / name, content)

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def plan(self, profile="work", overlay=None, copilot_home=None):
        platform = "windows" if os.name == "nt" else "linux"
        options = {"copilot_home": copilot_home} if copilot_home else {}
        return link.plan(
            self.repo,
            self.home,
            profile,
            platform,
            overlay,
            str(self.home / "Documents/PowerShell/profile.ps1"),
            **options,
        )

    def apply(self, profile="work", overlay=None, dry=False):
        with contextlib.redirect_stdout(io.StringIO()):
            self.plan(profile, overlay).apply(dry)

    def snapshot(self):
        return {
            p.relative_to(self.home).as_posix(): ("link", os.readlink(p))
            if p.is_symlink()
            else ("file", p.read_bytes())
            for p in self.home.rglob("*")
            if p.is_file() or p.is_symlink()
        }

    def test_profile_resolution_never_defaults_or_writes(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                resolve_profile(home=self.home)
            self.assertEqual(self.snapshot(), {})
            self.write(self.home / ".dotfiles-env", "work\n")
            self.assertEqual(resolve_profile(home=self.home), "work")
            with patch.dict(os.environ, {"DOTFILES_ENV": "invalid"}):
                with self.assertRaises(ValueError):
                    resolve_profile(home=self.home)
                self.assertEqual(resolve_profile("home", self.home), "home")

    def test_private_login_script_blocks_home_to_work_before_any_writes(self):
        self.apply("home", self.overlay)
        self.write(
            self.home / ".zprofile", f'source "{self.overlay}/personal-startup.zsh"\n'
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "review remaining reference"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_backup_symlink_cannot_redirect_private_configuration(self):
        outside = self.base / "outside"
        outside.mkdir()
        backups = self.home / ".config/dotfiles/backups"
        backups.parent.mkdir(parents=True)
        backups.symlink_to(outside, target_is_directory=True)
        self.write(self.home / ".gitconfig", "# existing private settings\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "symlinked backup"):
            self.apply()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_backup_junction_cannot_redirect_private_configuration(self):
        outside = self.base / "outside-backups"
        outside.mkdir()
        backups = self.home / ".config/dotfiles/backups"
        backups.parent.mkdir(parents=True)
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(backups), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest("junction creation is unavailable")

        self.write(self.home / ".gitconfig", "# existing private settings\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "symlinked backup"):
            self.apply()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list(outside.iterdir()), [])

    def test_unavailable_reparse_target_is_redirecting(self):
        details = Mock(
            st_mode=stat.S_IFDIR,
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
        )
        with (
            patch.object(link.os, "name", "nt"),
            patch.object(link.os, "lstat", return_value=details),
        ):
            self.assertTrue(link.is_redirecting_link(self.base / "unavailable"))

    def test_dry_run_writes_nothing(self):
        self.apply(dry=True)
        self.assertEqual(self.snapshot(), {})

    def test_preserves_git_shell_settings_and_is_idempotent(self):
        self.write(
            self.home / ".gitconfig",
            "[user]\n\tname = Work\n\temail = work@example.test\n",
        )
        self.write(self.home / ".zshrc", "# existing shell\n")
        self.write(self.home / ".copilot/settings.json", '{"custom": 123}')
        self.apply()
        self.assertIn("name = Work", (self.home / ".gitconfig").read_text())
        if os.name != "nt":
            self.assertIn("# existing shell", (self.home / ".zshrc").read_text())
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        self.assertEqual(settings["custom"], 123)
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertTrue(settings["footer"]["showUsername"])
        before = self.snapshot()
        self.apply()
        self.assertEqual(before, self.snapshot())

    def test_managed_copilot_home_receives_configuration(self):
        copilot_home = self.base / "managed-copilot"
        self.plan(copilot_home=copilot_home).apply(False)

        self.assertFalse((self.home / ".copilot").exists())
        self.assertIn(
            "Write clearly.",
            (copilot_home / "copilot-instructions.md").read_text(),
        )
        settings = json.loads((copilot_home / "settings.json").read_text())
        denied = settings["sandbox"]["userPolicy"]["filesystem"]["deniedPaths"]
        self.assertIn(str(copilot_home.resolve() / "session-state"), denied)

    def test_legacy_state_under_aliased_home_can_relink(self):
        physical_home = self.base / "physical-home"
        physical_home.mkdir()
        self.home = self.base / "aliased-home"
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(self.home), str(physical_home)],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                self.skipTest("junction creation is unavailable")
        else:
            self.home.symlink_to(physical_home, target_is_directory=True)
        self.apply()

        state_path = physical_home / ".config/dotfiles/state.json"
        state = json.loads(state_path.read_text())
        del state["copilot_home"]
        state_path.write_text(json.dumps(state))

        self.plan().apply(False)
        updated = json.loads(state_path.read_text())
        self.assertEqual(
            updated["copilot_home"],
            str((physical_home / ".copilot").resolve()),
        )

    def test_changed_copilot_home_requires_review(self):
        first = self.base / "managed-copilot-first"
        second = self.base / "managed-copilot-second"
        self.plan(copilot_home=first).apply(False)
        before = self.snapshot()

        with self.assertRaisesRegex(ValueError, "COPILOT_HOME changed"):
            self.plan(copilot_home=second)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue((first / "settings.json").exists())
        self.assertFalse(second.exists())

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_symlinked_copilot_home_cannot_redirect_writes(self):
        outside = self.base / "outside-copilot"
        outside.mkdir()
        (self.home / ".copilot").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlinked configuration directory"):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_junctioned_copilot_home_cannot_redirect_writes(self):
        outside = self.base / "outside-copilot"
        outside.mkdir()
        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(self.home / ".copilot"),
                str(outside),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest("junction creation is unavailable")

        with self.assertRaisesRegex(ValueError, "symlinked configuration directory"):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_copilot_home_beneath_junction_is_pinned_to_physical_path(self):
        outside = self.base / "outside-parent"
        outside.mkdir()
        junction = self.base / "junction-parent"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            self.skipTest("junction creation is unavailable")

        installer = self.plan(copilot_home=junction / "managed-copilot")
        self.assertEqual(
            installer.copilot_home,
            (outside / "managed-copilot").resolve(),
        )
        installer.apply(False)
        self.assertTrue((outside / "managed-copilot/settings.json").exists())

    def test_unknown_file_blocks_entire_install(self):
        self.write(self.home / ".config/shared.txt", "locally customized")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.apply()
        self.assertEqual(before, self.snapshot())

    def test_home_to_work_removes_owned_private_files_and_rules(self):
        self.apply("home", self.overlay)
        self.assertIn("Personal automation", (self.home / ".codex/AGENTS.md").read_text())
        self.apply("work")
        self.assertFalse((self.home / ".config/private.txt").exists())
        self.assertNotIn("Personal automation", (self.home / ".codex/AGENTS.md").read_text())
        self.assertNotIn("gitconfig.home", (self.home / ".gitconfig").read_text())
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        self.assertNotIn("research", settings["subagents"]["agents"])
        self.assertEqual((self.home / ".dotfiles-env").read_text(), "work\n")

    def test_claude_only_overlay_rules_reach_only_claude(self):
        self.write(self.overlay / "agents/home.claude.md", "Claude mechanics.\n")
        self.apply("home", self.overlay)
        claude = (self.home / ".claude/CLAUDE.md").read_text()
        self.assertIn("Personal automation", claude)
        self.assertIn("Claude mechanics", claude)
        for relative in (
            ".codex/AGENTS.md",
            ".copilot/copilot-instructions.md",
        ):
            with self.subTest(relative=relative):
                text = (self.home / relative).read_text()
                self.assertIn("Personal automation", text)
                self.assertNotIn("Claude mechanics", text)
        self.apply("work")
        claude = (self.home / ".claude/CLAUDE.md").read_text()
        self.assertNotIn("Claude mechanics", claude)
        self.assertIn("Use focused changes", claude)

    def previous_home_agents(self, user_text=""):
        """Leave ~/AGENTS.md as a link from before #94 left it."""
        self.apply()
        block = "<!-- dotfiles:begin -->\nUse focused changes.\n<!-- dotfiles:end -->"
        state_path = self.home / ".config/dotfiles/state.json"
        state = json.loads(state_path.read_text())
        state["files"]["AGENTS.md"] = {
            "block": link.digest(block.encode()),
            "markers": ["<!-- dotfiles:begin -->", "<!-- dotfiles:end -->"],
        }
        state_path.write_text(json.dumps(state))
        content = user_text + ("\n" if user_text else "") + block + "\n"
        self.write(self.home / "AGENTS.md", content)
        return content

    def backups(self, relative):
        return [
            path.read_text()
            for path in (self.home / ".config/dotfiles/backups").glob(f"*/{relative}")
        ]

    def test_fresh_link_writes_rules_only_to_harness_files(self):
        for profile, overlay, rule in (
            ("work", None, "Follow employer rules"),
            ("home", self.overlay, "Personal automation"),
        ):
            with self.subTest(profile=profile):
                self.apply(profile, overlay)
                self.assertFalse((self.home / "AGENTS.md").exists())
                for relative in (
                    ".codex/AGENTS.md",
                    ".claude/CLAUDE.md",
                    ".copilot/copilot-instructions.md",
                ):
                    text = (self.home / relative).read_text()
                    self.assertIn("Use focused changes", text, relative)
                    self.assertIn(rule, text, relative)

    def test_previous_home_rules_are_removed_and_backed_up(self):
        content = self.previous_home_agents()
        self.apply()
        self.assertFalse((self.home / "AGENTS.md").exists())
        self.assertEqual(self.backups("AGENTS.md"), [content])
        state = json.loads((self.home / ".config/dotfiles/state.json").read_text())
        self.assertNotIn("AGENTS.md", state["files"])
        before = self.snapshot()
        self.apply()
        self.assertEqual(before, self.snapshot())

    def test_previous_home_rules_leave_user_text(self):
        content = self.previous_home_agents("# mine\nKeep me.\n")
        self.apply("home", self.overlay)
        self.assertEqual((self.home / "AGENTS.md").read_text(), "# mine\nKeep me.\n")
        self.assertEqual(self.backups("AGENTS.md"), [content])
        before = self.snapshot()
        self.apply("home", self.overlay)
        self.assertEqual(before, self.snapshot())

    def test_user_home_agents_without_block_is_untouched(self):
        for profile, overlay in (("work", None), ("home", self.overlay)):
            with self.subTest(profile=profile):
                self.write(self.home / "AGENTS.md", "Personal standing authorization.\n")
                self.apply(profile, overlay)
                self.assertEqual(
                    (self.home / "AGENTS.md").read_text(),
                    "Personal standing authorization.\n",
                )
                self.assertEqual(self.backups("AGENTS.md"), [])

    def test_dry_run_reports_home_rules_removal_and_changes_nothing(self):
        self.previous_home_agents()
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.plan().apply(True)
        self.assertIn(f"remove: {self.home / 'AGENTS.md'}", output.getvalue())
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_legacy_codex_link_to_home_rules_becomes_managed_file(self):
        legacy = "Rules from the old installer.\n"
        self.write(
            self.overlay / "profiles/legacy.json",
            json.dumps({"AGENTS.md": [link.digest(legacy.encode())]}),
        )
        for name, home_rules in (("legacy file", legacy), ("dangling", None)):
            with self.subTest(name):
                for path in (self.home / "AGENTS.md", self.home / ".codex/AGENTS.md"):
                    path.unlink(missing_ok=True)
                if home_rules:
                    self.write(self.home / "AGENTS.md", home_rules)
                (self.home / ".codex").mkdir(exist_ok=True)
                (self.home / ".codex/AGENTS.md").symlink_to(self.home / "AGENTS.md")
                self.apply("home", self.overlay)
                codex = self.home / ".codex/AGENTS.md"
                self.assertFalse(codex.is_symlink())
                self.assertIn("Personal automation", codex.read_text())
                self.assertFalse((self.home / "AGENTS.md").exists())
                before = self.snapshot()
                self.apply("home", self.overlay)
                self.assertEqual(before, self.snapshot())
        self.assertEqual(self.backups("AGENTS.md"), [legacy])

    def test_rules_render_as_utf8_whatever_the_locale_encoding(self):
        text = "Dash \u2014 ellipsis\u2026 \u2018quoted\u2019"
        self.write(self.repo / "agents/core.md", f"Core {text}\n")
        self.write(self.repo / "copilot/copilot-instructions.md", f"Copilot {text}\n")
        self.write(self.overlay / "agents/home.md", f"Home {text}\n")
        self.write(self.overlay / "agents/home.claude.md", f"Claude {text}\n")
        # Make an implicit read_text() decode as a cp1252 locale would, on any OS.
        with patch.object(
            io, "text_encoding", lambda encoding, *args: encoding or "cp1252"
        ):
            self.apply("home", self.overlay)
        expected = text.encode("utf-8")
        for relative, sources in {
            ".codex/AGENTS.md": ("Core", "Home"),
            ".claude/CLAUDE.md": ("Core", "Home", "Claude"),
            ".copilot/copilot-instructions.md": ("Copilot", "Core", "Home"),
        }.items():
            with self.subTest(relative=relative):
                rendered = (self.home / relative).read_bytes()
                for source in sources:
                    self.assertIn(source.encode() + b" " + expected, rendered)

    def test_modified_private_copy_blocks_transition(self):
        self.apply("home", self.overlay)
        target = self.home / ".config/private.txt"
        target.unlink()
        self.write(target, "local change")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "modified file"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_modified_managed_block_blocks_transition(self):
        self.apply("home", self.overlay)
        target = self.home / ".codex/AGENTS.md"
        target.write_text(
            target.read_text().replace("Personal automation", "User customization")
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "edited managed block"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_unmanaged_agents_need_review_for_work(self):
        self.write(self.home / ".codex/AGENTS.md", "Unreviewed global automation")
        with self.assertRaisesRegex(ValueError, "existing global agent instructions"):
            self.apply("work")
        self.assertEqual(len(self.snapshot()), 1)

    def test_unowned_rules_outside_home_block_block_work(self):
        self.write(self.home / ".codex/AGENTS.md", "Personal standing authorization.\n")
        self.apply("home", self.overlay)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "existing global agent instructions"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_rules_repeated_outside_block_warn_and_are_kept(self):
        self.write(self.repo / "agents/core.md", "# focus\nUse focused changes.\n")
        stale = "# focus\nOld wording.\n\n# extra\nKeep me.\n"
        self.write(self.home / ".codex/AGENTS.md", stale)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.apply("home", self.overlay)
        self.assertIn("warning", output.getvalue())
        self.assertIn(str(self.home / ".codex/AGENTS.md"), output.getvalue())
        self.assertIn("# focus", output.getvalue())
        self.assertNotIn("# extra", output.getvalue())
        self.assertTrue((self.home / ".codex/AGENTS.md").read_text().startswith(stale))

    def test_camel_case_copy_of_sentence_case_rule_warns(self):
        self.write(self.repo / "agents/core.md", "# PR authoring\nWrite titles.\n")
        self.write(self.home / ".codex/AGENTS.md", "# prAuthoring\nOld wording.\n")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.apply("home", self.overlay)
        self.assertIn("warning", output.getvalue())
        self.assertIn("# PR authoring", output.getvalue())

    def test_distinct_rules_outside_block_do_not_warn(self):
        self.write(self.repo / "agents/core.md", "# focus\nUse focused changes.\n")
        self.write(self.home / ".codex/AGENTS.md", "# extra\nKeep me.\n")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.apply("home", self.overlay)
        self.assertEqual("", output.getvalue())

    def test_fenced_comments_are_not_headings(self):
        fence = "```sh\n# install\nmake\n```\n"
        self.write(self.repo / "agents/core.md", "# focus\n" + fence)
        self.write(self.home / ".codex/AGENTS.md", "~~~\n# focus\n~~~\n" + fence)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.apply("home", self.overlay)
        self.assertEqual("", output.getvalue())

    def test_legacy_git_include_is_adopted_and_removed_on_work(self):
        self.write(
            self.home / ".gitconfig",
            "[include]\n\tpath = "
            + str(self.overlay / "gitconfig").replace(chr(92), "/")
            + "\n",
        )
        self.apply("home", self.overlay)
        self.apply("work")
        self.assertNotIn(
            str(self.overlay).replace(chr(92), "/"),
            (self.home / ".gitconfig").read_text(),
        )

    def test_private_reference_outside_managed_shell_block_blocks_work(self):
        self.apply("home", self.overlay)
        target = self.home / (
            "Documents/PowerShell/profile.ps1" if os.name == "nt" else ".zshrc"
        )
        target.write_text(
            target.read_text()
            + "\n# source "
            + str(self.overlay / "custom-startup")
            + "\n"
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_external_mcp_blocks_transition(self):
        self.apply("home", self.overlay)
        self.write(
            self.home / ".codex/config.toml",
            '[mcp_servers.private]\nurl = "https://example.test"\n',
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "MCP/plugin"):
            self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_work_rejects_home_only_overlay(self):
        with self.assertRaisesRegex(ValueError, r"profiles[/\\]work\.json"):
            self.plan("work", self.overlay)
        self.assertEqual(self.snapshot(), {})

    def work_overlay(self, name="work-overlay"):
        overlay = self.base / name
        for relative, content in {
            "profiles/work.json": json.dumps(
                {"files": {".config/work.txt": "work.txt"}}
            ),
            "work.txt": name + " configuration\n",
            "agents/work.md": name + " instructions\n",
            "gitconfig.work": "[user]\n\tname = Example Developer\n",
            "zshrc.work": "export OVERLAY_TEST=" + name + "\n",
            "bashrc.work": "export OVERLAY_TEST=" + name + "\n",
            "tmux.work.conf": "# work tmux\n",
            "powershell/profile.work.ps1": "$env:OVERLAY_TEST = '" + name + "'\n",
            # The selected profile must never fall back to these files.
            "agents/home.md": "UNSELECTED HOME INSTRUCTIONS\n",
            "gitconfig.home": "[user]\n\tname = Unselected Home\n",
        }.items():
            self.write(overlay / relative, content)
        return overlay

    def test_work_overlay_fresh_repeat_switch_and_removal(self):
        first, second = self.work_overlay(), self.work_overlay("replacement")
        self.apply("work", first, dry=True)
        self.assertEqual(self.snapshot(), {})
        with self.assertRaisesRegex(ValueError, r"profiles[/\\]home\.json"):
            self.apply("home", first)
        self.apply("work", first)
        self.assertIn("Follow employer rules", (self.home / ".codex/AGENTS.md").read_text())
        self.assertIn("work-overlay instructions", (self.home / ".codex/AGENTS.md").read_text())
        self.assertNotIn("UNSELECTED HOME", (self.home / ".codex/AGENTS.md").read_text())
        self.assertIn("gitconfig.work", (self.home / ".gitconfig").read_text())
        self.assertNotIn("gitconfig.home", (self.home / ".gitconfig").read_text())
        self.assertEqual((self.home / ".config/work.txt").read_text(), "work-overlay configuration\n")
        before = self.snapshot()
        self.apply("work", first)
        self.assertEqual(before, self.snapshot())
        self.apply("work", second)
        self.assertNotIn("work-overlay instructions", (self.home / ".codex/AGENTS.md").read_text())
        self.assertIn("replacement instructions", (self.home / ".codex/AGENTS.md").read_text())
        self.assertEqual((self.home / ".config/work.txt").read_text(), "replacement configuration\n")
        self.apply("work")
        self.assertFalse((self.home / ".config/work.txt").exists())
        state = json.loads((self.home / ".config/dotfiles/state.json").read_text())
        self.assertNotIn("overlay", state)
        for path, content in self.snapshot().items():
            if "/backups/" not in path:
                self.assertNotIn(str(second), str(content), path)
        before = self.snapshot()
        self.apply("work")
        self.assertEqual(before, self.snapshot())

    def test_home_to_work_overlay_removes_personal_state(self):
        work = self.work_overlay()
        self.apply("home", self.overlay)
        before = self.snapshot()
        self.apply("home", self.overlay)
        self.assertEqual(before, self.snapshot())
        self.apply("work", work)
        self.assertFalse((self.home / ".config/private.txt").exists())
        for path, content in self.snapshot().items():
            if "/backups/" not in path:
                self.assertNotIn(str(self.overlay), str(content), path)
                self.assertNotIn("Personal automation", str(content), path)
                self.assertNotIn("personal-model", str(content), path)
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        self.assertTrue(settings["sandbox"]["enabled"])
        self.assertEqual((self.home / ".dotfiles-env").read_text(), "work\n")
        self.apply("home", self.overlay)
        self.assertFalse((self.home / ".config/work.txt").exists())
        self.assertNotIn("work-overlay instructions", (self.home / ".codex/AGENTS.md").read_text())
        self.assertIn("Personal automation", (self.home / ".codex/AGENTS.md").read_text())

    def test_overlay_can_omit_all_optional_files(self):
        overlay = self.base / "empty-work"
        self.write(overlay / "profiles/work.json", "{}")
        self.apply("work", overlay)
        self.assertNotIn("gitconfig.work", (self.home / ".gitconfig").read_text())
        self.assertIn("Follow employer rules", (self.home / ".codex/AGENTS.md").read_text())

    def test_overlay_sensitive_paths_reach_copilot_settings(self):
        self.write(
            self.overlay / "copilot/sensitive.json",
            '{"env": ["EXAMPLE_TOKEN"], "paths": [".config/example"]}',
        )
        self.apply("home", self.overlay)
        settings = json.loads((self.home / ".copilot/settings.json").read_text())
        denied = settings["sandbox"]["userPolicy"]["filesystem"]["deniedPaths"]
        self.assertIn(str(self.home / ".config/example"), denied)

    def test_invalid_overlay_sensitive_file_fails_before_writes(self):
        self.write(self.overlay / "copilot/sensitive.json", '{"env": "EXAMPLE_TOKEN"}')
        with self.assertRaisesRegex(ValueError, "sensitive.json env must be"):
            self.apply("home", self.overlay)
        self.assertEqual(self.snapshot(), {})

    def test_overlay_cannot_replace_shared_or_generated_configuration(self):
        overlay = self.work_overlay()
        for relative in (
            ".config/shared.txt", ".gitconfig", ".dotfiles-env",
            ".config/dotfiles/state.json", ".copilot/settings.json",
            ".config", ".", ".config/shared.txt/child",
            ".config/dotfiles/backups/extra.txt",
        ):
            with self.subTest(relative=relative):
                self.write(overlay / "profiles/work.json", json.dumps({"files": {relative: "work.txt"}}))
                with self.assertRaisesRegex(ValueError, "reserved|already configured|invalid installation"):
                    self.apply("work", overlay)
                self.assertEqual(self.snapshot(), {})

    def test_overlay_switch_requires_review_of_external_startup_reference(self):
        overlay = self.work_overlay()
        self.apply("work", overlay)
        self.write(self.home / ".zprofile", f'source "{overlay}/external.zsh"\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("work", self.work_overlay("replacement"))
        self.assertEqual(before, self.snapshot())

    def test_retained_work_overlay_reference_does_not_block_relink(self):
        overlay = self.work_overlay()
        self.apply("work", overlay)
        target = self.home / ".gitconfig"
        target.write_text(target.read_text() + "\n# " + str(overlay) + "\n")
        self.apply("work", overlay)
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("work")

    def test_profile_switch_in_same_overlay_reviews_external_startup(self):
        overlay = self.work_overlay()
        self.write(overlay / "profiles/home.json", "{}")
        self.apply("work", overlay)
        self.write(self.home / ".zprofile", f'source "{overlay}/zshrc.work"\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "remaining reference"):
            self.apply("home", overlay)
        self.assertEqual(before, self.snapshot())

    def test_shell_additions_follow_selected_profile_on_both_platforms(self):
        overlay = self.work_overlay()
        # The CI matrix executes this with native paths and file semantics on each OS.
        self.apply("work", overlay)
        if os.name == "nt":
            target = self.home / "Documents/PowerShell/profile.ps1"
            self.assertIn("profile.work.ps1", target.read_text())
            self.assertIn("DOTFILES_PRIVATE_DIR", target.read_text())
            self.assertFalse((self.home / ".config/work.txt").is_symlink())
        else:
            for name in (".zshrc", ".bashrc"):
                self.assertIn(name[1:] + ".work", (self.home / name).read_text())
            self.assertIn("tmux.work.conf", (self.home / ".tmux.conf").read_text())
            target = self.home / ".zshrc"
        self.apply("work")
        self.assertNotIn(str(overlay), target.read_text())
        self.assertIn("DOTFILES_PRIVATE_DIR", target.read_text())

    def test_native_shell_loads_selected_overlay_and_clears_removed_path(self):
        overlay = self.work_overlay()
        self.apply("work", overlay)
        environment = dict(os.environ, DOTFILES_PRIVATE_DIR="stale-overlay")
        environment.pop("OVERLAY_TEST", None)
        if os.name == "nt":
            shell = shutil.which("pwsh")
            self.assertIsNotNone(shell, "PowerShell 7 is required for the Windows setup tests")
            target = str(self.home / "Documents/PowerShell/profile.ps1").replace("'", "''")
            command = [shell, "-NoProfile", "-Command", f". '{target}'; Write-Output \"$env:DOTFILES_ENV|$env:DOTFILES_PRIVATE_DIR|$env:OVERLAY_TEST\""]
        else:
            command = ["bash", "--noprofile", "--norc", "-c", '. "$1"; printf "%s|%s|%s\\n" "$DOTFILES_ENV" "$DOTFILES_PRIVATE_DIR" "$OVERLAY_TEST"', "test", str(self.home / ".bashrc")]
        result = subprocess.run(command, env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), f"work|{overlay}|work-overlay")
        self.apply("work")
        result = subprocess.run(command, env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "work||")

    def herdr_plan(self, platform, overlay=None, profile="home"):
        """Plan a link for platform and return its herdr target and parsed config."""
        # Copilot settings validate home paths for the target OS, which this
        # host's temporary paths fail on the other OS; stub them for the plan.
        settings = self.repo / "bin/apply-copilot-settings.py"
        real = settings.read_text()
        self.write(settings, (
            "def build_settings(*args, **kwargs):\n    return {'footer': {}}\n"
            "def load_sensitive(path):\n    return {'paths': []}\n"
        ))
        try:
            installer = link.plan(
                self.repo, self.home, profile, platform, overlay,
                str(self.home / "Documents/PowerShell/profile.ps1"),
            )
        finally:
            self.write(settings, real)
        [(relative, data)] = [
            (r, v) for r, action, v in installer.changes if r.endswith("herdr/config.toml")
        ]
        self.assertEqual(installer.records[relative], {"sha256": link.digest(data)})
        return relative, tomllib.loads(data.decode())

    def test_fresh_herdr_config_is_themed_on_windows_and_linux(self):
        for platform, expected in (
            ("windows", "AppData/Roaming/herdr/config.toml"),
            ("linux", ".config/herdr/config.toml"),
        ):
            relative, config = self.herdr_plan(platform)
            self.assertEqual(relative, expected)
            self.assertEqual(config["theme"]["name"], "catppuccin")
            self.assertEqual(config["theme"]["custom"]["mauve"], "#cba6f7")
            self.assertEqual(config["keys"]["last_pane"], "prefix+shift+o")
            self.assertFalse(config["onboarding"])
        self.apply("home")
        native = self.home / (
            "AppData/Roaming" if os.name == "nt" else ".config"
        ) / "herdr/config.toml"
        self.assertFalse(native.is_symlink())
        self.assertEqual(native.read_bytes(), (self.repo / "herdr/config.toml").read_bytes())

    def test_herdr_overlay_adds_shell_and_keys(self):
        self.write(self.overlay / "herdr.home.toml", (
            '[terminal]\ndefault_shell = "example-shell"\n\n'
            '[keys]\nnext_agent = "prefix+a"\n\n'
            '[[keys.command]]\nkey = "prefix+alt+g"\ntype = "popup"\n'
            'command = "example \\"tool\\""\n'
        ))
        for platform in ("windows", "linux"):
            _, config = self.herdr_plan(platform, self.overlay)
            self.assertEqual(config["terminal"]["default_shell"], "example-shell")
            self.assertEqual(config["keys"]["next_agent"], "prefix+a")
            self.assertEqual(config["keys"]["last_pane"], "prefix+shift+o")
            self.assertEqual(config["keys"]["command"][0]["command"], 'example "tool"')
            shared = tomllib.loads((self.repo / "herdr/config.toml").read_text())
            self.assertEqual(config["ui"], shared["ui"])
            self.assertEqual(config["theme"], shared["theme"])
        # The overlay's file is still the installer's to replace and remove.
        self.apply("home", self.overlay)
        before = self.snapshot()
        self.apply("home", self.overlay)
        self.assertEqual(before, self.snapshot())
        self.apply("home")
        native = self.home / (
            "AppData/Roaming" if os.name == "nt" else ".config"
        ) / "herdr/config.toml"
        self.assertEqual(native.read_bytes(), (self.repo / "herdr/config.toml").read_bytes())

    def test_herdr_os_overlay_applies_after_profile_overlay(self):
        self.write(self.overlay / "herdr.home.toml", '[ui.toast]\ndelivery = "system"\n')
        self.write(self.overlay / "herdr.home.darwin.toml", (
            '[ui.toast]\ndelivery = "terminal"\n\n[ui.sound]\nenabled = true\n'
        ))
        _, config = self.herdr_plan("darwin", self.overlay)
        self.assertEqual(config["ui"]["toast"]["delivery"], "terminal")
        self.assertIs(config["ui"]["sound"]["enabled"], True)
        _, config = self.herdr_plan("windows", self.overlay)
        self.assertEqual(config["ui"]["toast"]["delivery"], "system")

    def test_herdr_overlay_syntax_error_names_the_file(self):
        self.write(self.overlay / "herdr.home.toml", "[ui\n")
        with self.assertRaisesRegex(ValueError, "herdr\\.home\\.toml"):
            self.herdr_plan("linux", self.overlay)

    def test_herdr_overlay_delete_character_stays_valid_toml(self):
        self.write(self.overlay / "herdr.home.toml", '[terminal]\ndefault_shell = "a\\u007fb"\n')
        _, config = self.herdr_plan("linux", self.overlay)
        self.assertEqual(config["terminal"]["default_shell"], "a\x7fb")

    def test_herdr_self_edit_skips_only_herdr(self):
        self.apply("home")
        native = self.home / (
            "AppData/Roaming" if os.name == "nt" else ".config"
        ) / "herdr/config.toml"
        # herdr rewrites its config from its Settings screen.
        self.write(native, "onboarding = false\n")
        self.write(self.overlay / "profiles/home.json", json.dumps(
            {"files": {".config/private.txt": "private.txt", ".config/new.txt": "private.txt"}}
        ))
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.apply("home", self.overlay)
        self.assertIn(f"warning: keeping {native}", stderr.getvalue())
        self.assertEqual(native.read_text(encoding="utf-8"), "onboarding = false\n")
        self.assertTrue((self.home / ".config/new.txt").is_file())
        # Still unowned on the next link, and other files keep blocking.
        with contextlib.redirect_stderr(io.StringIO()):
            self.apply("home", self.overlay)
        self.assertEqual(native.read_text(encoding="utf-8"), "onboarding = false\n")
        # Replace rather than write through: on Unix this target is a symlink into the repo.
        (self.home / ".config/shared.txt").unlink()
        self.write(self.home / ".config/shared.txt", "locally customized")
        with self.assertRaisesRegex(ValueError, "unowned"):
            self.apply("home", self.overlay)

    def test_readonly_blocks_before_writes(self):
        target = self.home / ".gitconfig"
        self.write(target, "# read only\n")
        target.chmod(0o444)
        self.addCleanup(target.chmod, 0o644)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "non-writable"):
            self.apply()
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == "nt", "symlinks require Windows developer mode")
    def test_symlink_parent_cannot_redirect_writes(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.home / ".config").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked configuration directory"):
            self.apply()
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "native Windows installation")
    def test_user_wezterm_override_is_checked_even_when_process_is_shared(self):
        for process, user in [(self.repo, self.overlay), (self.overlay, self.repo)]:
            with (
                patch.dict(
                    os.environ, {"WEZTERM_CONFIG_FILE": str(process / "wezterm.lua")}
                ),
                self.assertRaisesRegex(ValueError, "WEZTERM_CONFIG_FILE"),
            ):
                link.plan(
                    self.repo,
                    self.home,
                    "work",
                    "windows",
                    powershell_profile=str(
                        self.home / "Documents/PowerShell/profile.ps1"
                    ),
                    wezterm_user_config=str(user / "wezterm.lua"),
                )
        self.assertEqual(self.snapshot(), {})

    @unittest.skipUnless(os.name == "nt", "native Windows installation")
    def test_windows_copies_and_preserves_powershell_profile(self):
        target = self.home / "Documents/PowerShell/profile.ps1"
        self.write(target, "# existing profile\n")
        self.apply()
        self.assertFalse((self.home / ".config/shared.txt").is_symlink())
        self.assertIn("# existing profile", target.read_text())
        self.assertIn("return dofile", (self.home / ".wezterm.lua").read_text())

    @unittest.skipUnless(os.name == "nt", "native Windows installation")
    def test_existing_wezterm_requires_review(self):
        self.write(self.home / ".wezterm.lua", "return {}\n")
        with self.assertRaisesRegex(ValueError, "existing WezTerm"):
            self.apply()
        self.assertEqual(len(self.snapshot()), 1)

    def checkout(self):
        """Make the fixture repo runnable the way users run it."""
        for name in (
            "link.py", "link.sh", "link.ps1", "dotfiles_profile.py",
            "powershell/find-python.ps1", "claude/hooks/git-freshness.sh",
        ):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, self.repo / name)

    def env(self):
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("GIT_", "DOTFILES_", "COPILOT_", "WEZTERM_", "XDG_"))
        }
        env.update(
            HOME=str(self.home), USERPROFILE=str(self.home), GIT_CONFIG_NOSYSTEM="1",
            GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="dev@example.test",
            GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="dev@example.test",
        )
        return env

    def link(self, *args):
        """Run the command line as `link.py` in the fixture repo would."""
        if os.name == "nt":
            args += ("--powershell-profile", str(self.home / "Documents/PowerShell/profile.ps1"))
        stderr, code = io.StringIO(), 0
        with (
            patch.dict(os.environ, self.env(), clear=True),
            patch.object(link, "__file__", str(self.repo / "link.py")),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                link.main(list(args))
            except SystemExit as error:
                code = error.code
        return SimpleNamespace(returncode=code, stderr=stderr.getvalue())

    def git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", *args], cwd=cwd or self.repo, env=self.env(),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_rerun_without_arguments_keeps_profile_without_overlay(self):
        self.checkout()
        self.assertEqual(self.link("--profile", "work").returncode, 0)
        self.write(self.repo / "agents/core.md", "Use small changes.\n")
        result = self.link()
        self.assertEqual(result.returncode, 0, result.stderr)
        rules = (self.home / ".codex/AGENTS.md").read_text()
        self.assertIn("Use small changes.", rules)
        self.assertIn("Follow employer rules", rules)
        state = json.loads((self.home / ".config/dotfiles/state.json").read_text())
        self.assertEqual(state["profile"], "work")
        self.assertNotIn("overlay", state)

    def test_rerun_without_arguments_keeps_recorded_overlay(self):
        self.checkout()
        first = self.link("--profile", "home", "--overlay", str(self.overlay))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.write(self.repo / "agents/core.md", "Use small changes.\n")
        result = self.link()
        self.assertEqual(result.returncode, 0, result.stderr)
        rules = (self.home / ".codex/AGENTS.md").read_text()
        self.assertIn("Use small changes.", rules)
        self.assertIn("Personal automation", rules)
        self.assertEqual((self.home / ".config/private.txt").read_text(), "private overlay\n")

    def test_rerun_refuses_when_recorded_overlay_is_gone(self):
        self.checkout()
        self.link("--profile", "home", "--overlay", str(self.overlay))
        self.overlay.rename(self.base / "moved")
        before = self.snapshot()
        result = self.link()
        self.assertEqual(result.returncode, 1)
        self.assertIn("recorded overlay no longer exists", result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_rerun_without_state_fails_as_before(self):
        self.checkout()
        result = self.link()
        self.assertEqual(result.returncode, 1)
        self.assertIn("select --profile", result.stderr)
        self.assertEqual(self.snapshot(), {})

    def test_hook_installed_in_checkout_and_overlay_and_removed_with_overlay(self):
        self.checkout()
        for checkout in (self.repo, self.overlay):
            self.git("init", "-q", cwd=checkout)
        result = self.link("--profile", "home", "--overlay", str(self.overlay))
        self.assertEqual(result.returncode, 0, result.stderr)
        hooks = [path / ".git/hooks/post-merge" for path in (self.repo, self.overlay)]
        for hook in hooks:
            self.assertIn("link.", hook.read_text())
        self.assertEqual(self.link("--profile", "work").returncode, 0)
        self.assertTrue(hooks[0].exists())
        self.assertFalse(hooks[1].exists())

    def test_hook_never_replaces_a_hook_it_did_not_write(self):
        self.checkout()
        self.git("init", "-q")
        hook = self.repo / ".git/hooks/post-merge"
        self.write(hook, "#!/bin/sh\necho mine\n")
        result = self.link("--profile", "work")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("did not write", result.stderr)
        self.assertEqual(hook.read_text(), "#!/bin/sh\necho mine\n")
        # A later relink still leaves it alone instead of adopting or removing it.
        self.assertEqual(self.link().returncode, 0)
        self.assertEqual(hook.read_text(), "#!/bin/sh\necho mine\n")

    def test_hook_edited_since_the_link_is_kept(self):
        self.checkout()
        self.git("init", "-q")
        self.assertEqual(self.link("--profile", "work").returncode, 0)
        hook = self.repo / ".git/hooks/post-merge"
        edited = hook.read_text() + "echo my own step\n"
        hook.write_text(edited)
        result = self.link()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed since", result.stderr)
        self.assertEqual(hook.read_text(), edited)

    def test_hook_is_not_written_through_a_symlink(self):
        self.checkout()
        self.git("init", "-q")
        hook = self.repo / ".git/hooks/post-merge"
        hook.parent.mkdir(parents=True, exist_ok=True)
        target = self.repo / "missing-hook-target"
        hook.symlink_to(target)
        result = self.link("--profile", "work")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(target.exists())
        self.assertTrue(hook.is_symlink())

    def test_hook_stays_out_of_a_shared_hooks_folder(self):
        self.checkout()
        self.git("init", "-q")
        shared_hooks = self.base / "shared-hooks"
        shared_hooks.mkdir()
        self.git("config", "core.hooksPath", str(shared_hooks))
        result = self.link("--profile", "work")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("core.hooksPath is set", result.stderr)
        self.assertEqual(list(shared_hooks.iterdir()), [])
        self.assertFalse((self.repo / ".git/hooks/post-merge").exists())

    def linked_clone(self, *args):
        """Link from a clone of the fixture repo; return the upstream repo."""
        self.checkout()
        upstream = self.repo
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.repo = self.base / "clone"
        self.git("clone", "-q", str(upstream), str(self.repo), cwd=self.base)
        result = self.link(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write(upstream / "agents/core.md", "Use small changes.\n")
        self.git("commit", "-qam", "change rules", cwd=upstream)
        return upstream

    @unittest.skipIf(
        os.name == "nt",
        "pwsh takes $PROFILE from the real Documents folder, outside a temp home",
    )
    def test_pull_relinks_changed_rules(self):
        self.linked_clone("--profile", "work")
        self.assertNotIn("Use small changes.", (self.home / ".claude/CLAUDE.md").read_text())
        self.git("pull", "-q")
        self.assertIn("Use small changes.", (self.home / ".claude/CLAUDE.md").read_text())

    def overlay_with_submodule(self):
        """Link from a clone of an overlay that holds the repo as a submodule.

        Returns the upstream repo and overlay, and a git runner that may use
        local-path submodule remotes.
        """
        self.checkout()
        upstream, overlay = self.repo, self.overlay
        local = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "protocol.file.allow",
            "GIT_CONFIG_VALUE_0": "always",
        }

        def git(*args, cwd):
            result = subprocess.run(
                ["git", *args], cwd=cwd, env=self.env() | local,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result

        git("init", "-q", cwd=upstream)
        git("add", ".", cwd=upstream)
        git("commit", "-qm", "fixture", cwd=upstream)
        git("init", "-q", cwd=overlay)
        git("submodule", "add", "-q", str(upstream), "shared", cwd=overlay)
        git("add", ".", cwd=overlay)
        git("commit", "-qm", "overlay", cwd=overlay)
        # The name checks that the hook quotes its paths.
        self.overlay = self.base / "overlay $(clone)"
        git("clone", "-q", "--recurse-submodules", str(overlay), str(self.overlay), cwd=self.base)
        self.repo = self.overlay / "shared"
        result = self.link("--profile", "home", "--overlay", str(self.overlay))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write(upstream / "agents/core.md", "Use small changes.\n")
        git("commit", "-qam", "change rules", cwd=upstream)
        git("pull", "-q", cwd=overlay / "shared")
        git("commit", "-qam", "bump", cwd=overlay)
        return git

    @unittest.skipIf(
        os.name == "nt",
        "pwsh takes $PROFILE from the real Documents folder, outside a temp home",
    )
    def test_overlay_pull_relinks_with_the_updated_submodule(self):
        git = self.overlay_with_submodule()
        git("pull", "-q", cwd=self.overlay)
        self.assertIn("Use small changes.", (self.home / ".claude/CLAUDE.md").read_text())

    @unittest.skipIf(
        os.name == "nt",
        "pwsh takes $PROFILE from the real Documents folder, outside a temp home",
    )
    def test_overlay_pull_relinks_with_submodule_recursion_on(self):
        git = self.overlay_with_submodule()
        git("config", "submodule.recurse", "true", cwd=self.overlay)
        git("pull", "-q", cwd=self.overlay)
        self.assertIn("Use small changes.", (self.home / ".claude/CLAUDE.md").read_text())

    def test_overlay_pull_keeps_uncommitted_work_and_still_relinks(self):
        git = self.overlay_with_submodule()
        # The same file changed upstream, so the submodule update refuses.
        self.write(self.repo / "agents/core.md", "Local edit.\n")
        result = git("pull", "-q", cwd=self.overlay)
        self.assertIn("could not update shared", result.stderr)
        self.assertEqual((self.repo / "agents/core.md").read_text(), "Local edit.\n")
        if os.name != "nt":
            self.assertIn("Local edit.", (self.home / ".claude/CLAUDE.md").read_text())

    def test_hook_gains_the_submodule_step_on_the_next_link(self):
        git = self.overlay_with_submodule()
        hook = self.overlay / ".git/hooks/post-merge"
        git("rm", "-q", "--cached", "shared", cwd=self.overlay)
        self.assertEqual(self.link().returncode, 0)
        self.assertNotIn("submodule update", hook.read_text())
        git("reset", "-q", cwd=self.overlay)
        self.assertEqual(self.link().returncode, 0)
        self.assertIn("submodule update", hook.read_text())

    def test_overlay_pull_leaves_a_submodule_on_a_branch_alone(self):
        git = self.overlay_with_submodule()
        git("checkout", "-qb", "work-in-progress", cwd=self.repo)
        before = git("rev-parse", "HEAD", cwd=self.repo).stdout
        git("pull", "-q", cwd=self.overlay)
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.repo).stdout, before)
        self.assertEqual(
            git("symbolic-ref", "--short", "HEAD", cwd=self.repo).stdout.strip(), "work-in-progress"
        )

    def test_failed_relink_never_fails_the_pull(self):
        self.linked_clone("--profile", "home", "--overlay", str(self.overlay))
        self.overlay.rename(self.base / "moved")
        result = self.git("pull", "-q")
        self.assertIn("recorded overlay no longer exists", result.stderr)
        self.assertIn("relink failed", result.stderr)
        self.assertIn("Use small changes.", (self.repo / "agents/core.md").read_text())

    @unittest.skipIf(os.name == "nt", "the Claude hooks are installed on Unix only")
    def test_session_start_reports_stale_claude_rules(self):
        self.checkout()
        self.write(
            self.repo / "profiles/shared.json",
            json.dumps({"unix": {".claude/hooks/git-freshness.sh": "claude/hooks/git-freshness.sh"}}),
        )
        self.assertEqual(self.link("--profile", "work").returncode, 0)
        env = self.env() | {"GIT_CEILING_DIRECTORIES": str(self.base)}

        def session_start():
            return subprocess.run(
                ["sh", str(self.home / ".claude/hooks/git-freshness.sh")],
                cwd=self.home, env=env, capture_output=True, text=True, check=False,
            ).stdout

        self.assertNotIn("stale", session_start())
        self.write(self.repo / "agents/core.md", "Use small changes.\n")
        self.assertEqual(
            session_start().count("Claude rules are stale"), 1
        )


class PolicyTests(unittest.TestCase):
    def load_policy_module(self):
        spec = importlib.util.spec_from_file_location(
            "policy", ROOT / "bin/check-copilot-policy.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_missing_policy_warns_and_continues(self):
        module = self.load_policy_module()
        output = io.StringIO()
        with (
            patch.object(Path, "lstat", side_effect=FileNotFoundError),
            contextlib.redirect_stderr(output),
        ):
            module.main()
        self.assertIn("warning", output.getvalue())
        self.assertIn("missing", output.getvalue())

    def test_policy_requires_exact_mandatory_settings(self):
        module = self.load_policy_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            policy = {
                "sandbox": {
                    "enabled": True,
                    "failIfUnavailable": True,
                    "allowBypass": False,
                }
            }
            path.write_text(json.dumps(policy))
            module.validate_policy(path, posix=False)
            for key in policy["sandbox"]:
                changed = json.loads(json.dumps(policy))
                changed["sandbox"].pop(key)
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, key):
                    module.validate_policy(path, posix=False)


if __name__ == "__main__":
    unittest.main()

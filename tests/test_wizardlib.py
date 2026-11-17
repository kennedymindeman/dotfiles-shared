import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import wizardlib as w


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "sub", ".env")

    def tearDown(self):
        self.dir.cleanup()

    def test_upsert_is_idempotent_private_and_round_trips(self):
        w.write_env(self.path, "A", "one")
        w.write_env(self.path, "B", "a b#$c")
        w.write_env(self.path, "A", "two")
        with open(self.path) as f:
            self.assertEqual(f.read(), "B='a b#$c'\nA='two'\n")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(w.read_env(self.path, "A"), "two")
        self.assertEqual(w.read_env(self.path, "B"), "a b#$c")
        self.assertIsNone(w.read_env(self.path, "C"))

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privileges")
    def test_writes_through_symlink(self):
        real = os.path.join(self.dir.name, "real.env")
        link = os.path.join(self.dir.name, "link.env")
        os.symlink(real, link)
        w.write_env(link, "A", "x")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(w.read_env(real, "A"), "x")

    def test_rejects_newlines_and_bad_keys(self):
        with self.assertRaises(ValueError):
            w.write_env(self.path, "A", "x\nB=y")
        with self.assertRaises(ValueError):
            w.write_env(self.path, "A-B", "x")
        with self.assertRaises(ValueError):
            w.write_env(self.path, "A", "it's")

    def test_secret_file_is_private(self):
        p = os.path.join(self.dir.name, "d", "k")
        w.write_secret_file(p, "s")
        with open(p) as f:
            self.assertEqual(f.read(), "s")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            mode = stat.S_IMODE(os.stat(os.path.dirname(p)).st_mode)
            self.assertEqual(mode, 0o700)


class PromptTests(unittest.TestCase):
    def test_ask_keeps_current_on_enter(self):
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(w.ask("x", current="old"), "old")

    def test_ask_secret_retries_until_value(self):
        with mock.patch("getpass.getpass", side_effect=["", "s3"]):
            self.assertEqual(w.ask_secret("x"), "s3")

    def test_eof_exits_cleanly(self):
        with (
            mock.patch("builtins.input", side_effect=EOFError),
            self.assertRaises(SystemExit),
        ):
            w.ask("x")
        with (
            mock.patch("getpass.getpass", side_effect=EOFError),
            self.assertRaises(SystemExit),
        ):
            w.ask_secret("x")

    def test_pause_stops_on_eof(self):
        with (
            mock.patch("builtins.input", side_effect=EOFError),
            self.assertRaises(SystemExit),
        ):
            w.pause()

    def test_confirm(self):
        with mock.patch("builtins.input", return_value="y"):
            self.assertTrue(w.confirm("go?"))
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertFalse(w.confirm("go?"))


class EncodingTests(unittest.TestCase):
    def load(self, encoding):
        """Import a fresh copy of the library with console streams in `encoding`."""
        out = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
        err = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
        spec = importlib.util.spec_from_file_location("wizardlib_enc", w.__file__)
        lib = importlib.util.module_from_spec(spec)
        with (
            mock.patch.object(sys, "stdout", out),
            mock.patch.object(sys, "stderr", err),
        ):
            spec.loader.exec_module(lib)
        return lib, out, err

    def test_narrow_consoles_get_ascii_output(self):
        for encoding in ("cp1252", "ascii"):
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as d:
                lib, out, err = self.load(encoding)
                with (
                    mock.patch.object(sys, "stdout", out),
                    mock.patch.object(sys, "stderr", err),
                    mock.patch("builtins.input", return_value=""),
                ):
                    for helper in (lib.say, lib.step, lib.note, lib.warn, lib.ok):
                        helper("msg")
                    lib.show_url("https://example.com")
                    with self.assertRaises(SystemExit):
                        lib.die("msg")
                    wiz = lib.Wizard("Title", 1, intro=["intro"])
                    wiz.banner()
                    wiz.stage("Stage")
                    wiz.write_env(os.path.join(d, ".env"), "KEY", "value")
                    wiz.todo.append("item")
                    wiz.finish(ok=True)
                    wiz.finish(ok=False)
                    out.flush()
                    err.flush()

    def test_utf8_consoles_keep_glyphs(self):
        lib, _, _ = self.load("utf-8")
        self.assertEqual((lib.STAGE, lib.CHECK, lib.ARROW), ("▸", "✓", "→"))


if __name__ == "__main__":
    unittest.main()

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "claude/hooks/researcher-bash-gate.py"
FETCH = ROOT / "claude/hooks/research-fetch.py"
AGENT = ROOT / "claude/agents/researcher.md"
TILDE = "~/.claude/hooks/research-fetch.py"
VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@unittest.skipIf(os.name == "nt", "needs a POSIX shell environment")
class ResearcherBashGateTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base)
        self.home = base / "home"
        (self.home / ".claude/hooks").mkdir(parents=True)
        self.wrapper = self.home / ".claude/hooks/research-fetch.py"
        self.tmp = base / "tmp"
        self.tmp.mkdir()

    def run_gate(self, payload):
        env = {**os.environ, "HOME": str(self.home), "TMPDIR": str(self.tmp)}
        return subprocess.run(
            [sys.executable, "-I", str(GATE)],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def bash(self, command):
        return self.run_gate({"tool_name": "Bash", "tool_input": {"command": command}})

    def assertAllowed(self, command):
        result = self.bash(command)
        self.assertEqual(result.returncode, 0, f"{command!r}: {result.stderr}")

    def assertBlocked(self, command):
        result = self.bash(command)
        self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
        self.assertIn("Blocked", result.stderr)
        return result.stderr

    def test_allows_only_the_wrapper(self):
        for command in [
            f"{TILDE} scratch",
            f"{TILDE} clean {self.tmp}/researcher-abcd1234",
            f"{TILDE} transcript dQw4w9WgXcQ",
            f"{self.wrapper} storyboard dQw4w9WgXcQ {self.tmp}/researcher-abcd1234",
            f"{TILDE} slice dQw4w9WgXcQ 1:02 1:07 {self.tmp}/researcher-abcd1234 20",
            f"{TILDE} reddit /r/python/search 'q=type hints' sort=new",
            TILDE,
        ]:
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_blocks_other_commands_and_bypasses(self):
        w = TILDE
        for command in [
            "/bin/sh -c 'cat /etc/passwd'",
            "sh -c id",
            "bash -c id",
            "zsh -c id",
            "env curl https://example.com",
            "python3 -c 'print(1)'",
            "uvx youtube_transcript_api dQw4w9WgXcQ",
            "curl -s https://example.com",
            "cat ~/.config/gh/hosts.yml",
            "cat /etc/hosts",
            "gh api user",
            "ssh example-host",
            "git push",
            # scratch lifecycle goes through the wrapper, not mktemp and rm
            "mktemp -d",
            "mktemp -d -t research",
            f"rm -rf {self.tmp}/researcher-abcd1234",
            f"rm -rf {self.tmp}/tmp.abcdefghij",
            "rm -rf /",
            # chaining, substitution, redirection, globbing on an allowed command
            f"{w} transcript dQw4w9WgXcQ; curl https://example.com",
            f"{w} transcript dQw4w9WgXcQ && id",
            f"{w} transcript dQw4w9WgXcQ || id",
            f"{w} transcript dQw4w9WgXcQ | sh",
            f"{w} transcript dQw4w9WgXcQ & id",
            f"{w} transcript $(id)",
            f"{w} transcript `id`",
            f"{w} transcript dQw4w9WgXcQ > /etc/x",
            f"{w} transcript dQw4w9WgXcQ < /etc/x",
            f"{w} transcript dQw4w9WgXcQ\nid",
            f"{w} transcript dQw4w9WgXcQ\rid",
            f"{w}\ttranscript dQw4w9WgXcQ",
            f'{w} transcript "$HOME"',
            f"{w} clean {self.tmp}/researcher-*",
            f"{w} clean ~/.ssh",
            f"{w} reddit /r/{{a,b}}",
            f"{w} reddit /r/x?y",
            f"{w} transcript dQw4w9WgXc\\Q",
            f"{w} transcript dQw4w9WgXcQ #",
            f"{w} transcript dQw4w9WgXcQ ! x",
            f"{w} transcript dQw4w9WgXcQ x",
            # path tricks for the wrapper itself
            f"{self.home}/.claude/hooks/../hooks/research-fetch.py transcript x",
            f"{self.home}//.claude/hooks/research-fetch.py transcript x",
            "./research-fetch.py transcript x",
            "research-fetch.py transcript x",
            f"{w}.evil transcript x",
            f"{w}x transcript x",
            "~other/.claude/hooks/research-fetch.py transcript x",
            f'"{w}" transcript x',
            f"{self.tmp}/research-fetch.py transcript x",
            # quoting and env-prefix tricks
            "'cat' /etc/hosts",
            "c'a't /etc/hosts",
            f"PATH=/tmp/evil {self.wrapper} scratch",
            "=curl example.com",
            f"{w} 'unbalanced",
            "",
            "   ",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(command)

    def test_block_message_says_what_is_allowed(self):
        stderr = self.assertBlocked("cat ~/.config/gh/hosts.yml")
        for allowed in (
            "research-fetch.py scratch",
            "clean DIR",
            "transcript VIDEO_ID",
        ):
            self.assertIn(allowed, stderr)
        self.assertNotIn("mktemp", stderr)

    def test_fails_closed_on_bad_input(self):
        for payload in ["not json", "{}", json.dumps({"tool_input": {"command": 1}})]:
            with self.subTest(payload=payload):
                self.assertEqual(self.run_gate(payload).returncode, 2)

    def test_ignores_other_tools(self):
        payload = {"tool_name": "Read", "tool_input": {"command": "x"}}
        self.assertEqual(self.run_gate(payload).returncode, 0)

    def test_agent_frontmatter_wires_the_gate_fail_closed(self):
        text = AGENT.read_text(encoding="utf-8")
        frontmatter = text.split("---")[1]
        self.assertIn('matcher: "Bash"', frontmatter)
        self.assertIn("researcher-bash-gate.py", frontmatter)
        self.assertIn("onFailure: block", frontmatter)
        for path in (GATE, FETCH):
            self.assertTrue(os.access(path, os.X_OK), path)


def png(width, height):
    """The first 24 bytes of a PNG: signature, then the IHDR width and height."""
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


@unittest.skipIf(os.name == "nt", "needs a POSIX shell environment")
class ResearchFetchTests(unittest.TestCase):
    """The wrapper runs fixed programs; fakes on PATH record their argv and,
    like the real tools, write their output files."""

    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base)
        self.bin = base / "bin"
        self.bin.mkdir()
        self.log = base / "calls.log"
        self.mhtml = base / "sb.mhtml"
        self.sheets(2)
        self.png = base / "sheet.png"
        self.png.write_bytes(png(800, 450))
        last = f'for last; do :; done; /bin/cp "{self.png}" "$last"'
        # yt-dlp skips an existing output unless --force-overwrites, like the
        # real one; sips and ffmpeg write a PNG to their last argument.
        behavior = {
            "yt-dlp": 'case " $* " in *" sb0 "*) out=; force=;'
            ' for a; do [ "$a" = --force-overwrites ] && force=1;'
            ' [ "$prev" = -o ] && out=$a; prev=$a; done;'
            ' dest="$(/usr/bin/dirname "$out")/sb.mhtml";'
            f' if [ -n "$force" ] || [ ! -e "$dest" ]; then /bin/cp "{self.mhtml}" "$dest"; fi;;'
            " esac",
            "sips": last,
            "ffmpeg": last,
            "ffmpeg-fallback": last,
            "uvx": f'if [ "$1" = --from ]; then echo "{self.bin}/ffmpeg-fallback"; fi',
        }
        for name in ("uvx", "yt-dlp", "curl", "sips", "ffmpeg", "ffmpeg-fallback"):
            fake = self.bin / name
            fake.write_text(
                f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n{behavior.get(name, "")}\n'
            )
            fake.chmod(0o755)
        self.tmp = base / "tmp"
        self.tmp.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir(mode=0o700)
        (self.outside / "keep").write_text("keep")

    def sheets(self, count):
        message = EmailMessage()
        message.set_content(
            "<figcaption>Slide #1: 00:00:00</figcaption>", subtype="html"
        )
        for i in range(count):
            message.add_related(f"webp{i}".encode(), maintype="image", subtype="webp")
        self.mhtml.write_bytes(message.as_bytes())

    def fetch(self, *args, tmpdir=None):
        # Keep os.environ so coverage can follow the subprocess.
        env = {**os.environ, "PATH": str(self.bin), "TMPDIR": str(tmpdir or self.tmp)}
        if tmpdir == "":
            del env["TMPDIR"]
        return subprocess.run(
            [sys.executable, "-I", str(FETCH), *args],
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def scratch(self, **kwargs):
        result = self.fetch("scratch", **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_scratch_and_clean(self):
        path = self.scratch()
        self.assertEqual(os.path.dirname(path), os.path.realpath(self.tmp))
        self.assertRegex(os.path.basename(path), r"^researcher-[a-z0-9_]{8}$")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)
        Path(path, "sub").mkdir()
        Path(path, "sub/file").write_text("x")
        os.symlink(self.outside, Path(path, "link"))
        result = self.fetch("clean", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(path))
        self.assertTrue((self.outside / "keep").exists())

    def test_scratch_and_clean_with_tmpdir_unset(self):
        path = self.scratch(tmpdir="")
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        default = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import tempfile; print(tempfile.gettempdir())",
            ],
            env=env,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(os.path.dirname(path), os.path.realpath(default))
        self.assertEqual(self.fetch("clean", path, tmpdir="").returncode, 0)
        self.assertFalse(os.path.lexists(path))

    def test_clean_refuses_a_dir_from_a_different_temp_dir(self):
        other = self.tmp / "other"
        other.mkdir()
        path = self.scratch(tmpdir=other)
        self.assertNotEqual(self.fetch("clean", path).returncode, 0)
        self.assertTrue(os.path.isdir(path))

    def test_clean_refuses_everything_that_is_not_its_scratch_dir(self):
        real = self.scratch()
        link = self.tmp / "researcher-symlink"
        link.symlink_to(self.outside)
        foreign = self.tmp / "tmp.abcdefghij"
        foreign.mkdir(mode=0o700)
        loose = self.tmp / "researcher-loose123"
        loose.mkdir()
        loose.chmod(0o755)
        nested = Path(real) / "researcher-nested12"
        nested.mkdir(mode=0o700)
        for path in [
            f"{link}",
            f"{link}/",
            f"{link}//",
            f"{real}/",
            str(foreign),
            str(loose),
            str(nested),
            str(self.tmp),
            str(self.outside),
            f"{self.tmp}/researcher-missing1",
            f"{real}/../{os.path.basename(real)}",
            os.path.basename(real),
        ]:
            with self.subTest(path=path):
                self.assertNotEqual(self.fetch("clean", path).returncode, 0)
        for path in (real, foreign, loose, nested, self.outside / "keep"):
            self.assertTrue(os.path.exists(path), path)

    def test_dir_subcommands_refuse_foreign_dirs(self):
        foreign = self.tmp / "tmp.abcdefghij"
        foreign.mkdir(mode=0o700)
        link = self.tmp / "researcher-symlink"
        link.symlink_to(self.outside)
        for path in (str(foreign), f"{link}/", str(link)):
            with self.subTest(path=path):
                self.assertNotEqual(
                    self.fetch("storyboard", "dQw4w9WgXcQ", path).returncode, 0
                )
                args = ("slice", "dQw4w9WgXcQ", "1:00", "1:05", path)
                self.assertNotEqual(self.fetch(*args).returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(os.listdir(foreign), [])

    def test_transcript_and_reddit_build_fixed_argv(self):
        self.assertEqual(self.fetch("transcript", "-bcdefghijk").returncode, 0)
        result = self.fetch("reddit", "r/python/search", "q=a b&c=1", "sort=new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                "uvx youtube_transcript_api -- -bcdefghijk",
                (
                    "curl -s -A curl/8.7.1 "
                    "https://red.artemislena.eu/r/python/search?q=a+b%26c%3D1&sort=new"
                ),
            ],
        )

    def test_formats_lists_the_video(self):
        self.assertEqual(self.fetch("formats", "dQw4w9WgXcQ").returncode, 0)
        self.assertEqual(self.calls(), [f"yt-dlp -F {VIDEO}"])

    def test_storyboard_writes_png_sheets_with_sizes(self):
        d = self.scratch()
        result = self.fetch("storyboard", "dQw4w9WgXcQ", d)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{d}/sheet00.png 800x450", result.stdout)
        self.assertIn("2 sheets", result.stdout)
        self.assertEqual(Path(d, "sheet01.webp").read_bytes(), b"webp1")
        self.assertEqual(
            self.calls()[:2],
            [
                f"yt-dlp --force-overwrites -f sb0 -o {d}/sb.%(ext)s {VIDEO}",
                f"sips -s format png {d}/sheet00.webp --out {d}/sheet00.png",
            ],
        )

    def test_second_storyboard_into_the_same_dir_replaces_the_first(self):
        d = self.scratch()
        self.assertEqual(self.fetch("storyboard", "dQw4w9WgXcQ", d).returncode, 0)
        self.sheets(1)
        result = self.fetch("storyboard", "dQw4w9WgXcQ", d)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 sheets", result.stdout)
        self.assertEqual(
            sorted(os.listdir(d)), ["sb.mhtml", "sheet00.png", "sheet00.webp"]
        )
        self.assertEqual(Path(d, "sheet00.webp").read_bytes(), b"webp0")

    def test_storyboard_uses_ffmpeg_where_there_is_no_sips(self):
        (self.bin / "sips").unlink()
        d = self.scratch()
        result = self.fetch("storyboard", "dQw4w9WgXcQ", d)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{d}/sheet00.png 800x450", result.stdout)
        self.assertIn(
            f"ffmpeg -y -loglevel error -i {d}/sheet00.webp {d}/sheet00.png",
            self.calls(),
        )

    def test_writes_replace_planted_symlinks_instead_of_following_them(self):
        d = self.scratch()
        target = self.outside / "keep"
        for name in (
            "sheet00.webp",
            "sheet00.png",
            "sb.mhtml",
            "clip.mp4",
            "frame.png",
        ):
            os.symlink(target, Path(d, name))
        self.assertEqual(self.fetch("storyboard", "dQw4w9WgXcQ", d).returncode, 0)
        self.assertEqual(
            self.fetch("slice", "dQw4w9WgXcQ", "1:00", "1:05", d).returncode, 0
        )
        self.assertEqual(target.read_text(), "keep")
        for name in ("sheet00.webp", "sheet00.png", "sb.mhtml", "frame.png"):
            self.assertFalse(Path(d, name).is_symlink(), name)

    def test_slice_downloads_only_the_section_and_falls_back_to_uvx_ffmpeg(self):
        (self.bin / "ffmpeg").unlink()
        d = self.scratch()
        result = self.fetch("slice", "dQw4w9WgXcQ", "1:00", "1:05", d, "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        ff = f"{self.bin}/ffmpeg-fallback"
        self.assertEqual(
            self.calls()[1:],
            [
                (
                    f"yt-dlp --force-overwrites --ffmpeg-location {ff} -f "
                    "bv[height<=1080][ext=mp4]/bv[height<=1080]/b[height<=1080] "
                    f"--download-sections *1:00-1:05 -o {d}/clip.mp4 {VIDEO}"
                ),
                (
                    f"ffmpeg-fallback -y -loglevel error -i {d}/clip.mp4 "
                    f"-vf select=eq(n\\,7) -vframes 1 {d}/frame.png"
                ),
            ],
        )

    def test_rejects_bad_arguments_without_running_anything(self):
        d = self.scratch()
        for args in [
            ("transcript", "--exec=id"),
            ("transcript", "abc"),
            ("transcript", "dQw4w9WgXcQ", "extra"),
            ("formats", "https://example.com/watch?v=dQw4w9WgXcQ"),
            ("slice", "dQw4w9WgXcQ", "1:00;id", "1:05", d),
            ("slice", "dQw4w9WgXcQ", "1:00", "1:05", d, "n"),
            ("reddit", "/r/x/../../etc"),
            ("reddit", "https://example.com/r/x"),
            ("reddit", "/r/x", "novalue"),
            ("scratch", "extra"),
            ("clean",),
            ("shell", "id"),
            (),
        ]:
            with self.subTest(args=args):
                self.assertNotEqual(self.fetch(*args).returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()

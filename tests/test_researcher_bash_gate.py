import json
import os
import shutil
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


def mktemp(directory, prefix="tmp"):
    """A dir made the way the researcher makes one: the real mktemp -d."""
    out = subprocess.run(
        ["mktemp", "-d", f"{directory}/{prefix}.XXXXXXXXXX"],
        capture_output=True,
        check=True,
        text=True,
    )
    return out.stdout.strip()


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
        self.scratch = mktemp(self.tmp)
        self.scratch2 = mktemp(self.tmp, "research")

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

    def test_allows_the_wrapper_mktemp_and_scratch_rm(self):
        for command in [
            f"{TILDE} transcript dQw4w9WgXcQ",
            f"{self.wrapper} storyboard dQw4w9WgXcQ {self.scratch}",
            f"{TILDE} slice dQw4w9WgXcQ 1:02 1:07 {self.scratch} 20",
            f"{TILDE} reddit /r/python/search 'q=type hints' sort=new",
            TILDE,
            "mktemp -d",
            "mktemp -d -t research",
            f"rm -rf {self.scratch}",
            f"rm -fr {self.scratch}/ {self.scratch2}",
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
            f"{w} reddit /r/x/*",
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
            "PATH=/tmp/evil mktemp -d",
            "=curl example.com",
            f"{w} 'unbalanced",
            "",
            "   ",
            # mktemp shapes that pick their own path
            "mktemp -d /etc/x.XXXXXX",
            "mktemp -d -t ../x",
            "mktemp -d -t a b",
            "mktemp",
            # rm of anything that isn't a scratch dir
            "rm -rf /",
            "rm -rf ~",
            "rm -rf $TMPDIR",
            f"rm -rf {self.tmp}",
            f"rm -rf {self.home}",
            f"rm -rf {self.scratch} {self.home}",
            f"rm -rf {self.scratch}/../..",
            f"rm -rf {self.scratch}/./",
            f"rm -r {self.scratch}",
            f"rm {self.scratch}",
            f"rm -rf --no-preserve-root {self.scratch}",
            f"rm -rf {self.tmp}/tmp.doesnotexist",
            "rm -rf tmp.abcdefghij",
            "rm -rf",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(command)

    def test_rm_blocks_lookalike_scratch_dirs(self):
        loose = self.tmp / "tmp.looseMode1"
        loose.mkdir(mode=0o755)
        os.chmod(loose, 0o755)
        link = self.tmp / "tmp.symlinkHom"
        link.symlink_to(self.home)
        outside = Path(mktemp(self.home))
        nested = Path(self.scratch) / "tmp.innerDir12"
        nested.mkdir(mode=0o700)
        for path in (loose, link, outside):
            with self.subTest(path=path):
                self.assertBlocked(f"rm -rf {path}")
        self.assertAllowed(f"rm -rf {nested}")

    def test_block_message_says_what_is_allowed(self):
        stderr = self.assertBlocked("cat ~/.config/gh/hosts.yml")
        for allowed in (
            "research-fetch.py transcript VIDEO_ID",
            "mktemp -d",
            "rm -rf DIR",
        ):
            self.assertIn(allowed, stderr)

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


@unittest.skipIf(os.name == "nt", "needs a POSIX shell environment")
class ResearchFetchTests(unittest.TestCase):
    """The wrapper runs fixed programs; fakes on PATH record their argv."""

    def setUp(self):
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base)
        self.bin = base / "bin"
        self.bin.mkdir()
        self.log = base / "calls.log"
        self.mhtml = base / "sb.mhtml"
        sheets = EmailMessage()
        sheets.set_content(
            "<figcaption>Slide #1: 00:00:00</figcaption>", subtype="html"
        )
        for _ in range(2):
            sheets.add_related(b"RIFFwebp", maintype="image", subtype="webp")
        self.mhtml.write_bytes(sheets.as_bytes())
        # yt-dlp copies the storyboard into its -o dir; sips -g prints a size;
        # uvx --from prints a fallback ffmpeg path.
        behavior = {
            "yt-dlp": 'case " $* " in *" sb0 "*) while [ "$1" != -o ]; do shift; done;'
            f' /bin/cp "{self.mhtml}" "$(/usr/bin/dirname "$2")/sb.mhtml";; esac',
            "sips": 'if [ "$1" = -g ]; then'
            ' printf "%s\\n  pixelWidth: 800\\n  pixelHeight: 450\\n" "$5"; fi',
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
        self.scratch = os.path.realpath(mktemp(self.tmp))

    def fetch(self, *args):
        # Keep os.environ so coverage can follow the subprocess.
        env = {**os.environ, "PATH": str(self.bin), "TMPDIR": str(self.tmp)}
        return subprocess.run(
            [sys.executable, "-I", str(FETCH), *args],
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

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

    def test_storyboard_writes_png_sheets_with_sizes(self):
        result = self.fetch("storyboard", "dQw4w9WgXcQ", self.scratch)
        self.assertEqual(result.returncode, 0, result.stderr)
        sheet = f"{self.scratch}/sheet00"
        self.assertIn(f"{sheet}.png 800x450", result.stdout)
        self.assertIn("2 sheets", result.stdout)
        self.assertEqual(Path(f"{sheet}.webp").read_bytes(), b"RIFFwebp")
        self.assertEqual(
            self.calls()[:2],
            [
                (
                    f"yt-dlp -f sb0 -o {self.scratch}/sb.%(ext)s "
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                ),
                f"sips -s format png {sheet}.webp --out {sheet}.png",
            ],
        )

    def test_slice_downloads_only_the_section_and_falls_back_to_uvx_ffmpeg(self):
        (self.bin / "ffmpeg").unlink()
        result = self.fetch("slice", "dQw4w9WgXcQ", "1:00", "1:05", self.scratch, "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        ff = f"{self.bin}/ffmpeg-fallback"
        self.assertEqual(
            self.calls()[1:],
            [
                (
                    f"yt-dlp --ffmpeg-location {ff} -f "
                    "bv[height<=1080][ext=mp4]/bv[height<=1080]/b[height<=1080] "
                    f"--download-sections *1:00-1:05 -o {self.scratch}/clip.mp4 "
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
                ),
                (
                    f"ffmpeg-fallback -y -loglevel error -i {self.scratch}/clip.mp4 "
                    f"-vf select=eq(n\\,7) -vframes 1 {self.scratch}/frame.png"
                ),
            ],
        )

    def test_formats_lists_the_video(self):
        self.assertEqual(self.fetch("formats", "dQw4w9WgXcQ").returncode, 0)
        self.assertEqual(
            self.calls(), ["yt-dlp -F https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
        )

    def test_rejects_bad_arguments_without_running_anything(self):
        outside = tempfile.mkdtemp(dir=self.bin)
        for args in [
            ("transcript", "--exec=id"),
            ("transcript", "abc"),
            ("transcript", "dQw4w9WgXcQ", "extra"),
            ("formats", "https://example.com/watch?v=dQw4w9WgXcQ"),
            ("storyboard", "dQw4w9WgXcQ", outside),
            ("storyboard", "dQw4w9WgXcQ", str(self.tmp)),
            ("slice", "dQw4w9WgXcQ", "1:00;id", "1:05", self.scratch),
            ("slice", "dQw4w9WgXcQ", "1:00", "1:05", self.scratch, "n"),
            ("reddit", "/r/x/../../etc"),
            ("reddit", "https://example.com/r/x"),
            ("reddit", "/r/x", "novalue"),
            ("storyboard", "dQw4w9WgXcQ", f"{self.tmp}/missing"),
            ("shell", "id"),
            (),
        ]:
            with self.subTest(args=args):
                self.assertNotEqual(self.fetch(*args).returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()

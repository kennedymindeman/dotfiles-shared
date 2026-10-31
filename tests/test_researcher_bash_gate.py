import contextlib
import gzip
import http.server
import importlib.util
import io
import ipaddress
import json
import math
import os
import random
import re
import shlex
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
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
            f"{TILDE} help",
            f"{TILDE} get https://example.com/a/b",
            f"{TILDE} get 'https://example.com/s?q=a&b=c#frag'",
            f"{TILDE} get 'https://example.com/!$`(x)*~;|<>{{}}[]^%20' 50",
            f"{self.wrapper} get 'https://example.com/?a=1' 50 {self.tmp}/researcher-abcd1234",
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
            # get: only the single-quoted URL word escapes the character check
            f"{w} get https://example.com/?a=1",
            f"{w} get https://example.com/a&id",
            f'{w} get "https://example.com/?a=$(id)"',
            f"{w} get 'https://example.com/' ; id",
            f"{w} get 'https://example.com/' && id",
            f"{w} get 'https://example.com/' | sh",
            f"{w} get 'https://example.com/' > /tmp/x",
            f"{w} get 'https://example.com/' 50 '?'",
            f"{w} get 'https://example.com/''$(id)'",
            f"{w} get 'https://example.com/'$(id)",
            f"{w} get $'https://example.com/\\x27'",
            f"{w} get 'https://example.com/\nid'",
            f"{w} get 'https://example.com/'\nid",
            f"{w} transcript 'a?b'",
            f"{w} reddit '/r/x?y'",
            f"{w}  get 'https://example.com/?a'",
            "curl 'https://example.com/?a'",
            f"{w}x get 'https://example.com/?a'",
            "",
            "   ",
        ]:
            with self.subTest(command=command):
                self.assertBlocked(command)

    def assertShellsAgree(self, commands):
        """Run every allowed command in bash and zsh against a wrapper that
        records its argv; the shell must hand it exactly what shlex parsed.
        Returns the commands the gate allowed."""
        dump = self.home / "argv.json"
        canary = self.home / "pwned"
        self.wrapper.write_text(
            "#!/bin/sh\nexec python3 -I -c 'import json,sys;"
            f'open("{dump}","w").write(json.dumps(sys.argv[1:]))\' "$@"\n'
        )
        self.wrapper.chmod(0o755)
        shells = [s for s in ("/bin/bash", "/bin/zsh") if os.path.exists(s)]
        allowed = [c for c in commands if self.bash(c).returncode == 0]
        for command in allowed:
            for shell in shells:
                with self.subTest(command=command, shell=shell):
                    dump.unlink(missing_ok=True)
                    subprocess.run(
                        [shell, "-c", command],
                        env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
                        cwd=self.home,
                        capture_output=True,
                        check=False,
                    )
                    expected = shlex.split(command.replace("~", str(self.home), 1))
                    self.assertEqual(json.loads(dump.read_text()), expected[1:])
        self.assertFalse(canary.exists())
        return allowed

    def test_allowed_get_commands_run_with_the_argv_the_gate_saw(self):
        commands = [
            f"{TILDE} get 'http://a/?x=1&y=$(touch pwned)`touch pwned`'",
            f"{TILDE} get 'http://a/'\"'\"';touch pwned;'",
            f"{TILDE} get 'http://a/\\' ;touch pwned",
            f"{TILDE} get 'http://a/!!' 5",
            f"{TILDE} get 'http://a/' '$(touch pwned)'",
            f"{TILDE} get 'http://a/'\n",
            f"{TILDE} get 'http://{{x,localhost}}/[1-9]*?~#%' 5",
            f"{TILDE} get 'http://a/' =x",
            f"{TILDE} get 'https://b\u00fccher.ch/wiki/B\u00fccher?x=1'",
            f"{TILDE} get 'https://\u4e2d\u6587.example/\u00e9\u0301'",
        ]
        allowed = self.assertShellsAgree(commands)
        self.assertIn(commands[-2], allowed)
        self.assertIn(commands[-1], allowed)

    def test_get_refuses_invisible_and_format_characters(self):
        for char in [
            "\u202e", "\u200b", "\u200f", "\u2066", "\ufeff",
            "\u00a0", "\u2028", "\u0085", "\x7f", "\u3000",
        ]:  # fmt: skip
            with self.subTest(char=repr(char)):
                self.assertBlocked(f"{TILDE} get 'https://a.example/{char}x'")

    def test_get_quoted_url_fuzz(self):
        """Seeded random URLs and trailing text: whatever the gate allows must
        reach the wrapper unchanged in both shells, and never carry an
        invisible or format character."""
        rng = random.Random(322)
        chars = [
            *"'\"`$\\;&|<>(){}[]*?!#~=%^,\n\t @:/.ax1",
            "\u00fc", "\u4e2d", "\u0301", "\u202e", "\u200b", "\u00a0",
        ]  # fmt: skip
        tail = [*"'\"`$;&| ax1/", "\u00fc", "\u202e"]
        commands = [
            f"{TILDE} get 'http://"
            + "".join(rng.choices(chars, k=rng.randint(0, 12)))
            + "'"
            + "".join(rng.choices(tail, k=rng.randint(0, 4)))
            for _ in range(150)
        ]
        allowed = self.assertShellsAgree(commands)
        self.assertTrue(allowed)
        for command in allowed:
            self.assertFalse(set(command) & {"\u202e", "\u200b", "\u00a0"}, command)

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

    def run_guard(self, gate_body=None, mode=0o755):
        frontmatter = AGENT.read_text(encoding="utf-8").split("---")[1]
        command = re.search(r"^\s*command: '(.*)'$", frontmatter, re.MULTILINE).group(1)
        gate = self.home / ".claude/hooks/researcher-bash-gate.py"
        if gate_body is not None:
            gate.write_text(gate_body)
            gate.chmod(mode)
        return subprocess.run(
            ["/bin/sh", "-c", command],
            input="{}",
            env={**os.environ, "HOME": str(self.home)},
            capture_output=True,
            check=False,
            text=True,
        )

    def test_guard_blocks_when_the_gate_is_missing_or_not_executable(self):
        missing = self.run_guard()
        self.assertEqual(missing.returncode, 2)
        self.assertIn("gate missing", missing.stderr)
        self.assertEqual(
            self.run_guard("#!/bin/sh\nexit 0\n", mode=0o644).returncode, 2
        )

    def test_guard_runs_the_gate_and_passes_its_exit_code(self):
        self.assertEqual(
            self.run_guard("#!/bin/sh\ncat >/dev/null\nexit 0\n").returncode, 0
        )
        self.assertEqual(self.run_guard("#!/bin/sh\nexit 2\n").returncode, 2)

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
        self.cwds = base / "cwds.log"
        self.mhtml = base / "sb.mhtml"
        self.sheets(2)
        self.png = base / "sheet.png"
        self.png.write_bytes(png(800, 450))
        last = f'for last; do :; done; /bin/cp "{self.png}" "$last"'
        # Like the real tools: yt-dlp reads ./yt-dlp.conf unless --ignore-config
        # and skips an existing output unless --force-overwrites; sips and
        # ffmpeg write a PNG to their last argument. Every fake logs its cwd.
        honor = (
            'if [ -e yt-dlp.conf ]; then case " $* " in *" --ignore-config "*) ;;'
            f' *) echo "yt-dlp HONORED $(cat yt-dlp.conf)" >> "{self.log}";; esac; fi;'
        )
        behavior = {
            "yt-dlp": honor + ' case " $* " in *" sb0 "*) out=; force=;'
            ' for a; do [ "$a" = --force-overwrites ] && force=1;'
            ' [ "$prev" = -o ] && out=$a; prev=$a; done;'
            ' dest="$(/usr/bin/dirname "$out")/sb.mhtml";'
            f' if [ -n "$force" ] || [ ! -e "$dest" ]; then /bin/cp "{self.mhtml}" "$dest"; fi;;'
            " esac",
            "sips": last,
            "ffmpeg": last,
            "ffmpeg-fallback": last,
            "uvx": f'case " $* " in *" --from "*) echo "{self.bin}/ffmpeg-fallback";; esac',
        }
        for name in ("uvx", "yt-dlp", "curl", "sips", "ffmpeg", "ffmpeg-fallback"):
            fake = self.bin / name
            fake.write_text(
                f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n'
                f'pwd -P >> "{self.cwds}"\n{behavior.get(name, "")}\n'
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

    def fetch(self, *args, tmpdir=None, cwd=None):
        # Keep os.environ so coverage can follow the subprocess.
        env = {**os.environ, "PATH": str(self.bin), "TMPDIR": str(tmpdir or self.tmp)}
        if tmpdir == "":
            del env["TMPDIR"]
        return subprocess.run(
            [sys.executable, "-I", str(FETCH), *args],
            env=env,
            cwd=cwd,
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

    def test_clean_also_removes_stale_get_work_dirs(self):
        old = self.tmp / "research-get-abcd1234"
        old.mkdir(mode=0o700)
        (old / "body").write_text("x")
        fresh = self.tmp / "research-get-efgh5678"
        fresh.mkdir(mode=0o700)
        link = self.tmp / "research-get-link1234"
        link.symlink_to(self.outside)
        an_hour_ago = time.time() - 3600
        for path in (old, link):
            os.utime(path, (an_hour_ago, an_hour_ago), follow_symlinks=False)
        os.utime(old / "body", (an_hour_ago, an_hour_ago))
        result = self.fetch("clean", self.scratch())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(link.is_symlink())
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
                "uvx --no-config youtube_transcript_api -- -bcdefghijk",
                (
                    "curl -s -A curl/8.7.1 "
                    "https://red.artemislena.eu/r/python/search?q=a+b%26c%3D1&sort=new"
                ),
            ],
        )

    def test_formats_lists_the_video(self):
        self.assertEqual(self.fetch("formats", "dQw4w9WgXcQ").returncode, 0)
        self.assertEqual(self.calls(), [f"yt-dlp --ignore-config -F {VIDEO}"])

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
                f"yt-dlp --ignore-config --force-overwrites -f sb0 -o {d}/sb.%(ext)s {VIDEO}",
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
                    f"yt-dlp --ignore-config --force-overwrites --ffmpeg-location {ff} -f "
                    "bv[height<=1080][ext=mp4]/bv[height<=1080]/b[height<=1080] "
                    f"--download-sections *1:00-1:05 -o {d}/clip.mp4 {VIDEO}"
                ),
                (
                    f"ffmpeg-fallback -y -loglevel error -i {d}/clip.mp4 "
                    f"-vf select=eq(n\\,7) -vframes 1 {d}/frame.png"
                ),
            ],
        )

    def test_tools_never_read_config_from_a_hostile_cwd(self):
        hostile = self.outside / "checkout"
        hostile.mkdir()
        (hostile / "yt-dlp.conf").write_text("--exec id")
        d = self.scratch()
        # A conf planted in the scratch dir itself, where storyboard runs.
        Path(d, "yt-dlp.conf").write_text("--exec id")
        for args in [
            ("formats", "dQw4w9WgXcQ"),
            ("storyboard", "dQw4w9WgXcQ", d),
            ("slice", "dQw4w9WgXcQ", "1:00", "1:05", d),
            ("transcript", "dQw4w9WgXcQ"),
            ("reddit", "/r/python"),
        ]:
            with self.subTest(args=args):
                result = self.fetch(*args, cwd=hostile)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse([c for c in self.calls() if "HONORED" in c], self.calls())
        cwds = set(self.cwds.read_text().split())
        self.assertEqual(cwds, {d, os.path.realpath(self.tmp)})
        self.assertIn(
            "uvx --no-config youtube_transcript_api -- dQw4w9WgXcQ", self.calls()
        )

    def test_fake_yt_dlp_honors_a_conf_without_ignore_config(self):
        """Guards the test above: the fake must notice a conf it isn't told to skip."""
        hostile = self.outside / "checkout"
        hostile.mkdir()
        (hostile / "yt-dlp.conf").write_text("--exec id")
        subprocess.run([str(self.bin / "yt-dlp"), "-F", VIDEO], cwd=hostile, check=True)
        self.assertIn("yt-dlp HONORED --exec id", self.calls())

    def test_slice_fails_when_ffmpeg_writes_no_frame(self):
        (self.bin / "ffmpeg").write_text(
            f'#!/bin/sh\necho "ffmpeg $*" >> "{self.log}"\n'
        )
        d = self.scratch()
        result = self.fetch("slice", "dQw4w9WgXcQ", "1:00", "1:05", d, "9999")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no frame 9999", result.stderr)
        self.assertNotIn("frame.png", result.stdout)
        Path(d, "frame.png").write_bytes(b"")
        (self.bin / "ffmpeg").write_text(
            '#!/bin/sh\n: > "$(for a; do :; done; echo $a)"\n'
        )
        self.assertNotEqual(
            self.fetch("slice", "dQw4w9WgXcQ", "1:00", "1:05", d).returncode, 0
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
            ("get",),
            ("get", ""),
            ("get", "https://example.com/\x01"),
            ("get", "https://example.com/", "0"),
            ("get", "https://example.com/", "x"),
            ("get", "https://example.com/", "1", "2", "3"),
            (),
        ]:
            with self.subTest(args=args):
                self.assertNotEqual(self.fetch(*args).returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_help_lists_every_subcommand(self):
        for args in [("help",), ("--help",)]:
            with self.subTest(args=args):
                result = self.fetch(*args)
                self.assertEqual(result.returncode, 0)
                for cmd in (
                    "scratch",
                    "clean",
                    "transcript",
                    "formats",
                    "storyboard",
                    "slice",
                    "reddit",
                    "get",
                ):
                    self.assertIn(f"research-fetch.py {cmd}", result.stdout)
        for args in [(), ("nope",), ("get",)]:
            with self.subTest(args=args):
                result = self.fetch(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("research-fetch.py get 'URL'", result.stderr)


PAGES = {
    "/page": (
        200,
        "text/html; charset=utf-8",
        (
            b"<html><head><title>T</title><script>var secret=1</script>"
            b"<style>p{}</style></head><body><nav>Menu</nav><p>Hello &amp; bye</p>"
            b"</body></html>"
        ),
    ),
    "/big": (200, "text/plain", b"x" * 5000),
    "/json": (200, "application/json", b'{"a": 1}'),
    "/png": (200, "image/png", b"\x89PNG\r\n\x1a\n"),
    "/pdf": (200, "application/pdf", b"%PDF-1.4 fake"),
    "/octet-pdf": (200, "application/octet-stream", b"%PDF-1.4 octet"),
    "/a%20b%C3%A9": (200, "text/plain", b"spaced"),
    "/slow-page": (200, "text/plain", b"slow"),
    "/hops-0": (200, "text/html", b"<p>Hello &amp; bye</p>"),
    "/it%27s": (200, "text/plain", b"quoted"),
    "/yaml": (200, "application/yaml", b"a: 1"),
    "/x-yaml": (200, "application/x-yaml", b"b: 2"),
    "/toml": (200, "application/toml", b"c = 3"),
    "/js": (200, "application/javascript", b"var d = 4"),
    "/x-js": (200, "application/x-javascript", b"var e = 5"),
    "/big-html": (200, "text/html", b"<p>" + b"y" * 10000 + b"</p>"),
    "/punycode": (200, "text/plain; charset=punycode", b"-" + b"a" * 4_000_000),
    "/idna": (200, "text/plain; charset=idna", b"fallback " + b"a" * 70),
    "/undefined": (200, "text/plain; charset=undefined", b"fallback text"),
    "/latin-1": (200, "text/plain; charset=ISO-8859-1", b"caf\xe9"),
    "/missing": (404, "text/html", b"<p>gone</p>"),
}
# A synthetic global prefix that tests inject as one of this machine's networks.
OWN_V6 = "2a00:1450:4001:800::"
# Hosts that reach this loopback server only if curl parses them differently
# from the wrapper, or resolves them itself instead of using the pin.
BYPASS_HOSTS = [
    "0177.0.0.1",
    "0177.1",
    "012.0.0.1",
    "0144.0145.0146.0147",
    "0x7f.1",
    "2130706433",
    "%6cocalhost",
    "loc%61lhost",
    "{x,localhost}",
    "localhos[s-t]",
    "x,localhost",
    "[::127.0.0.1]",
    "[::ffff:0:7f00:1]",
    "[::ffff:127.0.0.1]",
    "[fec0::1]",
    "[0:0::1]",
    "[::1%25lo0]",
]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("User-Agent")))
        self.server.headers.append(self.headers)
        port = self.server.server_address[1]
        redirects = {
            "/to-page?a=1&b=2": f"http://127.0.0.1:{port}/page",
            "/relative": "page",
            "/to-space": "/a bé",
            "/to-private": "http://169.254.169.254/latest/meta-data/",
            "/to-localhost": f"http://localhost:{port}/page",
            "/to-file": "file:///etc/passwd",
            "/to-glob": f"http://{{x.example/,127.0.0.1:{port}/page}}",
            "/loop": "/loop",
            "/slow-hop-1": "/slow-hop-2",
            "/slow-hop-2": "/page",
            "/quote-redirect": "/it's",
            "/to-unicode-host": f"http://b\u00fccher.example:{port}/page",
            "/to-own-v6": f"http://[{OWN_V6}5]:{port}/page",
        }
        for n in range(1, 7):
            redirects[f"/hops-{n}"] = f"/hops-{n - 1}"
        for i, host in enumerate(BYPASS_HOSTS):
            redirects[f"/to-bypass-{i}"] = f"http://{host}:{port}/page"
        if self.path.startswith("/slow"):
            time.sleep(1.5)
        if self.path == "/hang":
            time.sleep(10)
            return
        if self.path == "/chunked-big":
            # No Content-Length, so curl's --max-filesize can't refuse it early.
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"z" * 5000)
            return
        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.end_headers()
            return
        if self.path == "/gzip-bomb":
            body = gzip.compress(b"0" * 50000)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        page = PAGES.get(self.path.split("?")[0], (404, "text/plain", b"no"))
        status, kind, body = page
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Server(http.server.ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer.server_bind does a reverse DNS lookup that can stall.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "localhost", self.server_address[1]


class V6Server(Server):
    address_family = socket.AF_INET6


def serve(server_class, address):
    server = server_class(address, Handler)
    server.requests, server.headers = [], []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@unittest.skipIf(os.name == "nt", "needs a POSIX shell environment")
@unittest.skipUnless(shutil.which("curl"), "needs curl")
class ResearchGetTests(unittest.TestCase):
    """`get` against a local server with the real curl. The server is on
    loopback, which `get` refuses, so most tests load the wrapper in-process and
    let only 127.0.0.1 through; the refusal tests run it unpatched."""

    def setUp(self):
        self.server = serve(Server, ("127.0.0.1", 0))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        spec = importlib.util.spec_from_file_location("research_fetch", FETCH)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.real_public_addresses = self.mod.public_addresses
        self.mod.public_addresses = self.loopback_only
        self.mod.local_networks = lambda: [ipaddress.ip_network(OWN_V6 + "/64")]

    def loopback_only(self, host, port, deadline):
        if host == "127.0.0.1":
            return [ipaddress.ip_address(host)]
        return self.real_public_addresses(host, port, deadline)

    def any_host(self):
        """Every host passes and is pinned to the test server."""
        self.mod.public_addresses = lambda host, port, deadline: [
            ipaddress.ip_address("127.0.0.1")
        ]

    def attacker_dns(self):
        """Every name resolves to a public address, as an attacker's wildcard
        zone would, so only the wrapper's own checks stand in the way."""
        real = socket.getaddrinfo

        def fake(host, *args, **kwargs):
            if host == "127.0.0.1":
                return real(host, *args, **kwargs)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]

        self.mod.socket = types.SimpleNamespace(
            getaddrinfo=fake, IPPROTO_TCP=socket.IPPROTO_TCP
        )

    def get(self, *args):
        """Returns (exit code, stdout, failure message)."""
        out = io.StringIO()
        code, message = 0, ""
        with contextlib.redirect_stdout(out):
            try:
                self.mod.get(*args)
            except SystemExit as e:
                code = e.code
                message = code if isinstance(code, str) else ""
        return code, out.getvalue(), message

    def test_html_becomes_readable_text_with_the_final_url_first(self):
        code, out, _ = self.get(self.base + "/to-page?a=1&b=2")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], f"{self.base}/page 200")
        self.assertIn("Hello & bye", out)
        for dropped in ("secret", "p{}", "Menu"):
            self.assertNotIn(dropped, out)

    def test_sends_curls_own_user_agent(self):
        self.get(self.base + "/page")
        self.assertRegex(self.server.requests[0][1], r"^curl/")

    def test_relative_redirects_and_text_types(self):
        self.assertIn(f"{self.base}/page 200", self.get(self.base + "/relative")[1])
        self.assertIn('{"a": 1}', self.get(self.base + "/json")[1])

    def test_redirect_location_with_space_and_non_ascii_is_encoded(self):
        code, out, _ = self.get(self.base + "/to-space")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[0], f"{self.base}/a%20b%C3%A9 200")
        self.assertIn("spaced", out)

    def test_caps_output_at_max_kb(self):
        code, out, _ = self.get(self.base + "/big", "2")
        self.assertEqual(code, 0)
        body = out.split("\n", 1)[1]
        self.assertEqual(body.count("x"), 2048)
        self.assertIn("[truncated at 2 KB", body)
        self.assertEqual(self.get(self.base + "/big")[1].count("x"), 5000)

    def test_refuses_binary_and_saves_pdf_only_to_a_scratch_dir(self):
        self.assertIn("binary content type image/png", self.get(self.base + "/png")[2])
        self.assertIn("pass a scratch DIR", self.get(self.base + "/pdf")[2])
        self.assertIn("pass a scratch DIR", self.get(self.base + "/octet-pdf")[2])
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base)
        scratch = base / "researcher-abcd1234"
        scratch.mkdir(mode=0o700)
        saved, tempfile.tempdir = tempfile.tempdir, str(base)
        try:
            for path, body in [
                ("/pdf", b"%PDF-1.4 fake"),
                ("/octet-pdf", b"%PDF-1.4 octet"),
            ]:
                with self.subTest(path=path):
                    code, _, _ = self.get(self.base + path, str(scratch))
                    self.assertEqual(code, 0)
                    self.assertEqual((scratch / "page.pdf").read_bytes(), body)
        finally:
            tempfile.tempdir = saved

    def test_error_status_prints_the_page_and_exits_nonzero(self):
        code, out, _ = self.get(self.base + "/missing")
        self.assertEqual(code, 1)
        self.assertIn("/missing 404", out)
        self.assertIn("gone", out)

    def test_curl_connects_only_to_the_checked_address(self):
        """The name doesn't resolve, so a response proves curl used the pin."""
        self.any_host()
        code, out, _ = self.get(f"http://pinned.invalid:{self.port}/page")
        self.assertEqual(code, 0, out)
        self.assertIn("Hello & bye", out)

    def test_bracketed_ipv6_literal_fetches(self):
        try:
            server = serve(V6Server, ("::1", 0))
        except OSError as e:
            self.skipTest(f"no IPv6 loopback: {e}")
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.mod.public_addresses = lambda host, port, deadline: [
            ipaddress.ip_address(host)
        ]
        url = f"http://[::1]:{server.server_address[1]}/page"
        code, out, _ = self.get(url)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[0], f"{url} 200")

    def test_curl_globs_are_literal(self):
        self.get(self.base + "/[1-4]{a,b}")
        self.assertEqual([p for p, _ in self.server.requests], ["/%5B1-4%5D%7Ba,b%7D"])

    def test_one_deadline_covers_every_hop(self):
        self.mod.TIMEOUT = 2
        self.assertEqual(self.get(self.base + "/slow-page")[0], 0)
        # Each slow hop fits the budget alone; together they don't.
        start = time.monotonic()
        code, out, message = self.get(self.base + "/slow-hop-1")
        self.assertNotEqual(code, 0)
        self.assertIn("timed out", message)
        self.assertEqual(out, "")
        self.assertLess(time.monotonic() - start, 2.8)

    def test_closed_port_with_a_large_glob_does_not_hang(self):
        """A glob once made curl fill its stderr pipe while the wrapper waited
        on stdout; the wrapper must return within its deadline."""
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        self.mod.TIMEOUT = 5
        done = threading.Event()

        def run():
            self.get(f"http://127.0.0.1:{closed}/[1-20000]")
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(done.wait(15), "get hung")

    def test_refuses_redirects_to_private_addresses_and_other_schemes(self):
        for path, reason in [
            ("/to-private", "non-public address 169.254.169.254"),
            ("/to-localhost", "non-public address"),
            ("/to-file", "only http and https"),
            ("/loop", "more than 5 redirects"),
        ]:
            with self.subTest(path=path):
                code, out, message = self.get(self.base + path)
                self.assertNotEqual(code, 0)
                self.assertIn(reason, message)
                self.assertEqual(out, "")
        self.attacker_dns()
        paths = ["/to-glob"] + [f"/to-bypass-{i}" for i in range(len(BYPASS_HOSTS))]
        for path in paths:
            with self.subTest(path=path):
                code, out, _ = self.get(self.base + path)
                self.assertNotEqual(code, 0)
                self.assertEqual(out, "")
        self.assertNotIn("/page", [p for p, _ in self.server.requests])

    def test_refuses_hosts_curl_would_read_differently(self):
        self.attacker_dns()
        for host in BYPASS_HOSTS:
            with self.subTest(host=host):
                code, out, _ = self.get(f"http://{host}:{self.port}/page")
                self.assertNotEqual(code, 0)
                self.assertEqual(out, "")
        self.assertEqual(self.server.requests, [])

    def test_refuses_addresses_on_this_machines_networks(self):
        networks = [
            ipaddress.ip_network(OWN_V6 + "/64"),
            ipaddress.ip_network("81.2.69.0/24"),
        ]
        self.mod.local_networks = lambda: networks
        for host in (OWN_V6 + "5", OWN_V6 + "ffff", "81.2.69.10"):
            with self.subTest(host=host), self.assertRaises(SystemExit) as e:
                self.real_public_addresses(host, 80, math.inf)
            self.assertIn("this machine's network", str(e.exception))
        for host in ("2a00:1450:4001:801::5", "81.2.70.1"):
            self.assertEqual(
                self.real_public_addresses(host, 80, math.inf),
                [ipaddress.ip_address(host)],
            )
        _, out, message = self.get(self.base + "/to-own-v6")
        self.assertIn("this machine's network", message)
        self.assertEqual(out, "")

    def test_refuses_all_ipv6_when_local_networks_cannot_be_read(self):
        self.mod.local_networks = lambda: None
        with self.assertRaises(SystemExit):
            self.real_public_addresses("2606:4700::1", 443, math.inf)
        self.assertEqual(
            self.real_public_addresses("8.8.8.8", 443, math.inf),
            [ipaddress.ip_address("8.8.8.8")],
        )

    def test_reads_local_networks_from_ifconfig_and_ip(self):
        ifconfig = (
            "lo0: flags=8049<UP,LOOPBACK> mtu 16384\n"
            "\tinet 127.0.0.1 netmask 0xff000000\n"
            "\tinet6 ::1 prefixlen 128\n"
            "\tinet6 fe80::1%lo0 prefixlen 64 scopeid 0x1\n"
            "en0: flags=8863<UP,BROADCAST> mtu 1500\n"
            "\tinet6 2a00:1450:4001:800::abcd prefixlen 64 autoconf secured\n"
            "\tinet 192.168.7.20 netmask 0xffffff00 broadcast 192.168.7.255\n"
            "utun4: flags=8051<UP,POINTOPOINT> mtu 1280\n"
            "\tinet 100.70.1.2 --> 100.70.1.2 netmask 0xffffffff\n"
        )
        ip_o = (
            "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
            "2: eth0    inet 81.2.69.5/24 brd 81.2.69.255 scope global eth0\n"
            "2: eth0    inet6 2a00:1450:4001:800::5/64 scope global dynamic\n"
        )
        net = ipaddress.ip_network
        self.assertEqual(
            self.mod.parse_interfaces(ifconfig),
            [
                net("127.0.0.0/8"),
                net("::1/128"),
                net("fe80::/64"),
                net(OWN_V6 + "/64"),
                net("192.168.7.0/24"),
                net("100.70.1.2/32"),
            ],
        )
        self.assertEqual(
            self.mod.parse_interfaces(ip_o),
            [net("127.0.0.0/8"), net("81.2.69.0/24"), net(OWN_V6 + "/64")],
        )
        spec = importlib.util.spec_from_file_location("fresh", FETCH)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        self.assertTrue(fresh.local_networks(), "this machine's networks")
        fresh.SYSTEM_PATH = str(Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, fresh.SYSTEM_PATH)
        self.assertIsNone(fresh.local_networks())

    def test_does_not_ask_for_compressed_responses(self):
        self.get(self.base + "/page")
        self.assertIsNone(self.server.headers[0].get("Accept-Encoding"))

    def test_compressed_responses_are_not_inflated(self):
        self.mod.RAW_CAP = 10000
        code, out, message = self.get(self.base + "/gzip-bomb")
        self.assertEqual(code, 0, message)
        self.assertLess(out.count("0"), 10000)

    def test_checks_the_body_size_after_curl_exits(self):
        self.mod.RAW_CAP = 1000
        code, out, message = self.get(self.base + "/chunked-big")
        self.assertNotEqual(code, 0)
        self.assertIn("larger than", message)
        self.assertEqual(out, "")

    def test_parses_at_most_parse_cap_of_html(self):
        self.mod.PARSE_CAP, self.mod.PARSE_CHUNK = 1024, 100
        code, out, _ = self.get(self.base + "/big-html")
        self.assertEqual(code, 0)
        self.assertLess(out.count("y"), 1024)
        self.assertIn("[parsed only the first 1 KB of HTML]", out)

    def test_deadline_covers_parsing(self):
        self.mod.TIMEOUT, self.mod.PARSE_CHUNK = 1, 1000
        feed = self.mod.Text.feed

        def slow_feed(parser, data):
            time.sleep(0.2)
            feed(parser, data)

        self.mod.Text.feed = slow_feed
        start = time.monotonic()
        code, _, message = self.get(self.base + "/big-html")
        self.assertNotEqual(code, 0)
        self.assertIn("timed out", message)
        self.assertLess(time.monotonic() - start, 2)

    def test_dns_lookup_is_bounded_by_the_deadline(self):
        def stall(*args, **kwargs):
            time.sleep(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]

        self.mod.socket = types.SimpleNamespace(
            getaddrinfo=stall, IPPROTO_TCP=socket.IPPROTO_TCP
        )
        self.mod.TIMEOUT = 1
        start = time.monotonic()
        code, _, message = self.get("http://slow-dns.example/")
        self.assertNotEqual(code, 0)
        self.assertIn("timed out resolving", message)
        self.assertLess(time.monotonic() - start, 2.5)

    def test_signal_kills_curl_and_removes_the_work_dir(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        driver = (
            "import importlib.util, ipaddress, sys\n"
            "spec = importlib.util.spec_from_file_location('rf', sys.argv[1])\n"
            "rf = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(rf)\n"
            "rf.public_addresses = lambda h, p, d: [ipaddress.ip_address('127.0.0.1')]\n"
            "rf.main(['get', sys.argv[2]])\n"
        )
        url = f"{self.base}/hang"
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(sig=sig):
                self.server.requests.clear()
                proc = subprocess.Popen(
                    [sys.executable, "-I", "-B", "-c", driver, str(FETCH), url],
                    env={**os.environ, "TMPDIR": str(tmp)},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                deadline = time.monotonic() + 10
                while not self.server.requests and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(self.server.requests, "curl never connected")
                proc.send_signal(sig)
                _, err = proc.communicate(timeout=10)
                self.assertIn(b"stopped by signal", err)
                self.assertEqual(list(tmp.glob("research-get-*")), [])
                ps = subprocess.run(
                    ["ps", "-A", "-o", "command"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotIn(url, ps.stdout)

    def test_unicode_host_path_and_query_are_encoded(self):
        self.any_host()
        code, out, _ = self.get(
            f"http://b\u00fccher.example:{self.port}/wiki/B\u00fccher?x=\u00fc&y='"
        )
        self.assertEqual(code, 1, out)
        self.assertEqual(self.server.requests[0][0], "/wiki/B%C3%BCcher?x=%C3%BC&y=%27")
        self.assertEqual(
            self.server.headers[0]["Host"], f"xn--bcher-kva.example:{self.port}"
        )
        self.assertRaises(SystemExit, self.mod.clean_url, "http://a/\u202e")
        self.assertRaises(SystemExit, self.mod.clean_url, "http://a/\u200b")

    def test_redirects_encode_quotes_and_unicode_hosts(self):
        code, out, _ = self.get(self.base + "/quote-redirect")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[0], f"{self.base}/it%27s 200")
        self.any_host()
        self.server.headers.clear()
        code, out, _ = self.get(self.base + "/to-unicode-host")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            out.splitlines()[0], f"http://xn--bcher-kva.example:{self.port}/page 200"
        )

    def test_decodes_only_allowlisted_charsets(self):
        for path, text in [
            ("/punycode", "-aaaa"),
            ("/idna", "fallback aaaa"),
            ("/undefined", "fallback text"),
            ("/latin-1", "caf\u00e9"),
        ]:
            with self.subTest(path=path):
                start = time.monotonic()
                code, out, message = self.get(self.base + path, "1")
                self.assertEqual(code, 0, message)
                self.assertIn(text, out)
                self.assertLess(time.monotonic() - start, 5)

    def test_more_text_types(self):
        for path, text in [
            ("/yaml", "a: 1"),
            ("/x-yaml", "b: 2"),
            ("/toml", "c = 3"),
            ("/js", "var d = 4"),
            ("/x-js", "var e = 5"),
        ]:
            with self.subTest(path=path):
                code, out, message = self.get(self.base + path)
                self.assertEqual(code, 0, message)
                self.assertIn(text, out)

    def test_tries_the_next_address_when_one_refuses(self):
        self.mod.public_addresses = lambda host, port, deadline: [
            ipaddress.ip_address("::1"),
            ipaddress.ip_address("127.0.0.1"),
        ]
        code, out, _ = self.get(f"http://pinned.invalid:{self.port}/page")
        self.assertEqual(code, 0, out)
        self.assertIn("Hello & bye", out)

    def test_checks_every_address_before_trying_any(self):
        def mixed(host, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            ]

        self.mod.socket = types.SimpleNamespace(
            getaddrinfo=mixed, IPPROTO_TCP=socket.IPPROTO_TCP
        )
        self.mod.public_addresses = self.real_public_addresses
        _, _, message = self.get(f"http://mixed.example:{self.port}/page")
        self.assertIn("non-public address 127.0.0.1", message)
        self.assertEqual(self.server.requests, [])

    def test_accepts_ordinary_url_shapes(self):
        self.any_host()
        long_query = "q=" + "a" * 4000
        for url in [
            f"http://WWW.Example.COM:{self.port}/page",
            f"http://example.com.:{self.port}/page",
            f"http://xn--bcher-kva.example:{self.port}/page",
            f"http://www.example.com:{self.port}/page",
            f"http://my_host.example:{self.port}/page",
            f"http://example.com:{self.port}/page?{long_query}",
            f"{self.base}/hops-5",
        ]:
            with self.subTest(url=url[:80]):
                code, out, message = self.get(url)
                self.assertEqual(code, 0, message)
                self.assertIn("Hello & bye", out)
        code, _, message = self.get(f"{self.base}/hops-6")
        self.assertIn("more than 5 redirects", message)

    def test_refuses_ipv6_forms_that_embed_ipv4_and_site_local(self):
        for host in [
            "::127.0.0.1",
            "::8.8.8.8",
            "::ffff:0:7f00:1",
            "::ffff:0:808:808",
            "::ffff:127.0.0.1",
            "fec0::1",
            "64:ff9b::7f00:1",
        ]:
            with self.subTest(host=host), self.assertRaises(SystemExit):
                self.real_public_addresses(host, 80, math.inf)
        self.assertEqual(
            self.real_public_addresses("2606:4700::1", 443, math.inf),
            [ipaddress.ip_address("2606:4700::1")],
        )

    def test_unpatched_wrapper_refuses_private_addresses_and_schemes(self):
        try:
            v6 = serve(V6Server, ("::", self.port))
            self.addCleanup(v6.server_close)
            self.addCleanup(v6.shutdown)
        except OSError:
            v6 = None
        hosts = [
            "127.0.0.1",
            "localhost",
            "LOCALHOST",
            "localhost.",
            "[::1]",
            *BYPASS_HOSTS,
        ]
        urls = [f"http://{host}:{self.port}/page" for host in hosts]
        urls += [
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.100.100.200/",
            "http://[fd00:ec2::254]/",
            "http://[64:ff9b::7f00:1]/",
            "file:///etc/passwd",
            "ftp://example.com/",
            "gopher://example.com/",
            "http://user@example.com/",
            "http://example.com\\@127.0.0.1/",
        ]
        for url in urls:
            with self.subTest(url=url):
                result = subprocess.run(
                    [sys.executable, "-I", str(FETCH), "get", url],
                    capture_output=True,
                    check=False,
                    text=True,
                    timeout=60,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.server.requests, [])
        if v6:
            self.assertEqual(v6.requests, [])


if __name__ == "__main__":
    unittest.main()

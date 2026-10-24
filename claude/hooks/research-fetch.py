#!/usr/bin/env -S python3 -I
"""The researcher's one fetch command: the fetch-media recipes behind fixed argv.

The researcher's Bash gate (researcher-bash-gate.py) allows this script and
nothing else that reaches the network, so every argument is validated here and
passed to a fixed program without a shell.

  research-fetch.py transcript VIDEO_ID
  research-fetch.py formats VIDEO_ID
  research-fetch.py storyboard VIDEO_ID DIR
  research-fetch.py slice VIDEO_ID START END DIR [FRAME]
  research-fetch.py reddit /r/SUB/PATH [KEY=VALUE ...]

DIR must be a dir made by `mktemp -d` (yours, mode 700, under the temp dir).
"""

import email
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TIME = re.compile(r"\d{1,2}(:\d{2}){0,2}(\.\d+)?")
REDDIT_PATH = re.compile(r"/?r/[A-Za-z0-9_]+(/(?!\.+(/|$))[A-Za-z0-9_.-]+)*/?")
PARAM = re.compile(r"[A-Za-z_]+=[^\n]*")
MIRROR = "https://red.artemislena.eu"
FORMAT = "bv[height<=1080][ext=mp4]/bv[height<=1080]/b[height<=1080]"


def fail(message):
    sys.exit(f"research-fetch: {message}\n{__doc__}")


def check(pattern, value, name):
    if not pattern.fullmatch(value):
        fail(f"bad {name}: {value!r}")
    return value


def video_url(video_id):
    return "https://www.youtube.com/watch?v=" + check(VIDEO_ID, video_id, "video id")


def temp_roots():
    return {os.path.realpath(p) for p in (tempfile.gettempdir(), "/tmp")}


def scratch_dir(path):
    """A dir you own, mode 700, under the temp dir: what `mktemp -d` makes."""
    real = os.path.realpath(path)
    try:
        st = os.lstat(real)
    except OSError:
        fail(f"no such dir: {path}")
    if not (
        stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and stat.S_IMODE(st.st_mode) == 0o700
        and any(real.startswith(root + "/") for root in temp_roots())
    ):
        fail(f"not a mktemp -d scratch dir: {path}")
    return real


def run(argv):
    sys.exit(subprocess.run(argv, check=False).returncode)


def ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    out = subprocess.run(
        [
            "uvx",
            "--from",
            "imageio-ffmpeg",
            "python",
            "-c",
            "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def storyboard(video_id, directory):
    url = video_url(video_id)
    directory = scratch_dir(directory)
    subprocess.run(
        ["yt-dlp", "-f", "sb0", "-o", os.path.join(directory, "sb.%(ext)s"), url],
        check=True,
    )
    # message_from_bytes: message_from_binary_file eats a trailing byte off some parts.
    with open(os.path.join(directory, "sb.mhtml"), "rb") as f:
        parts = email.message_from_bytes(f.read()).walk()
    sheets = [p for p in parts if p.get_content_type().startswith("image/")]
    for i, part in enumerate(sheets):
        webp = os.path.join(directory, f"sheet{i:02d}.webp")
        with open(webp, "wb") as f:
            f.write(part.get_payload(decode=True))
        png = webp[: -len("webp")] + "png"
        subprocess.run(
            ["sips", "-s", "format", "png", webp, "--out", png],
            check=True,
            capture_output=True,
        )
        size = subprocess.run(
            ["sips", "-g", "pixelWidth", "-g", "pixelHeight", png],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        print(png, f"{size[-3]}x{size[-1]}")
    print(
        len(sheets), "sheets; slide times are in", os.path.join(directory, "sb.mhtml")
    )


def main(argv):
    if not argv:
        fail("no subcommand")
    cmd, args = argv[0], argv[1:]
    if cmd == "transcript" and len(args) == 1:
        # `--` so an id starting with "-" is not read as an option.
        run(
            [
                "uvx",
                "youtube_transcript_api",
                "--",
                check(VIDEO_ID, args[0], "video id"),
            ]
        )
    elif cmd == "formats" and len(args) == 1:
        run(["yt-dlp", "-F", video_url(args[0])])
    elif cmd == "storyboard" and len(args) == 2:
        storyboard(*args)
    elif cmd == "slice" and len(args) in (4, 5):
        url = video_url(args[0])
        start, end = (check(TIME, t, "time") for t in args[1:3])
        directory = scratch_dir(args[3])
        frame = (
            int(check(re.compile(r"\d{1,5}"), args[4], "frame"))
            if len(args) == 5
            else 20
        )
        clip = os.path.join(directory, "clip.mp4")
        ff = ffmpeg()
        subprocess.run(
            [
                "yt-dlp",
                "--ffmpeg-location",
                ff,
                "-f",
                FORMAT,
                "--download-sections",
                f"*{start}-{end}",
                "-o",
                clip,
                url,
            ],
            check=True,
        )
        png = os.path.join(directory, "frame.png")
        subprocess.run(
            [
                ff,
                "-y",
                "-loglevel",
                "error",
                "-i",
                clip,
                "-vf",
                f"select=eq(n\\,{frame})",
                "-vframes",
                "1",
                png,
            ],
            check=True,
        )
        print(png)
    elif cmd == "reddit" and args:
        path = "/" + check(REDDIT_PATH, args[0], "reddit path").lstrip("/")
        params = [tuple(check(PARAM, p, "KEY=VALUE").split("=", 1)) for p in args[1:]]
        query = "?" + urllib.parse.urlencode(params) if params else ""
        run(["curl", "-s", "-A", "curl/8.7.1", MIRROR + path + query])
    else:
        fail(f"unknown subcommand or wrong argument count: {cmd!r}")


if __name__ == "__main__":
    main(sys.argv[1:])

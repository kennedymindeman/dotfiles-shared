#!/usr/bin/env -S python3 -I
"""The researcher's one Bash command: the fetch-media recipes behind fixed argv.

The researcher's Bash gate (researcher-bash-gate.py) allows this script and
nothing else, so every argument is validated here and passed to a fixed program
without a shell.

  research-fetch.py scratch                 make a scratch dir and print its path
  research-fetch.py clean DIR               delete a scratch dir
  research-fetch.py transcript VIDEO_ID
  research-fetch.py formats VIDEO_ID
  research-fetch.py storyboard VIDEO_ID DIR
  research-fetch.py slice VIDEO_ID START END DIR [FRAME]
  research-fetch.py reddit /r/SUB/PATH [KEY=VALUE ...]

DIR is a path printed by `scratch`, with no trailing slash.
"""

import email
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.parse

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
TIME = re.compile(r"\d{1,2}(:\d{2}){0,2}(\.\d+)?")
REDDIT_PATH = re.compile(r"/?r/[A-Za-z0-9_]+(/(?!\.+(/|$))[A-Za-z0-9_.-]+)*/?")
PARAM = re.compile(r"[A-Za-z_]+=[^\n]*")
# tempfile.mkdtemp(prefix=PREFIX) appends 8 chars from [a-z0-9_].
PREFIX = "researcher-"
SCRATCH_NAME = re.compile(re.escape(PREFIX) + r"[a-z0-9_]{8}")
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


def scratch_dir(path):
    """A dir `scratch` made: researcher-XXXXXXXX directly in the temp dir, a real
    directory (not a symlink), yours, mode 700. Returns its resolved path."""
    # A trailing slash makes lstat follow a symlink, so refuse it outright.
    if (
        not os.path.isabs(path)
        or path.endswith("/")
        or os.path.normpath(path) != path
        or not SCRATCH_NAME.fullmatch(os.path.basename(path))
    ):
        fail(f"not a scratch dir: {path}")
    root = os.path.realpath(tempfile.gettempdir())
    try:
        st = os.lstat(path)
        parent = os.path.realpath(os.path.dirname(path))
    except OSError:
        fail(f"no such scratch dir: {path}")
    if not (
        parent == root
        and stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and stat.S_IMODE(st.st_mode) == 0o700
    ):
        fail(f"not a scratch dir: {path}")
    return os.path.join(root, os.path.basename(path))


def fresh(path):
    """Remove whatever sits at path, a symlink included, so the next write
    creates a new file instead of following a planted link or reusing old output."""
    if os.path.lexists(path):
        os.unlink(path)
    return path


def write_new(path, data):
    fd = os.open(
        fresh(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def png_size(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        header = f.read(24)
    width, height = struct.unpack(">II", header[16:24])
    return f"{width}x{height}"


def run(argv):
    sys.exit(subprocess.run(argv, check=False).returncode)


def ffmpeg():
    found = shutil.which("ffmpeg")
    if found:
        return found
    out = subprocess.run(
        [
            "uvx",
            "--no-config",
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
    os.chdir(directory)
    mhtml = fresh(os.path.join(directory, "sb.mhtml"))
    for entry in os.scandir(directory):
        if re.fullmatch(r"sheet\d+\.(webp|png)", entry.name):
            os.unlink(entry.path)
    subprocess.run(
        [
            "yt-dlp",
            "--ignore-config",
            "--force-overwrites",
            "-f",
            "sb0",
            "-o",
            os.path.join(directory, "sb.%(ext)s"),
            url,
        ],
        check=True,
    )
    # message_from_bytes: message_from_binary_file eats a trailing byte off some parts.
    fd = os.open(mhtml, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        parts = email.message_from_bytes(f.read()).walk()
    sheets = [p for p in parts if p.get_content_type().startswith("image/")]
    # sips ships with macOS; elsewhere ffmpeg reads WebP.
    ff = None if shutil.which("sips") else ffmpeg()
    for i, part in enumerate(sheets):
        webp = os.path.join(directory, f"sheet{i:02d}.webp")
        write_new(webp, part.get_payload(decode=True))
        png = fresh(webp[: -len("webp")] + "png")
        convert = (
            [ff, "-y", "-loglevel", "error", "-i", webp, png]
            if ff
            else ["sips", "-s", "format", "png", webp, "--out", png]
        )
        subprocess.run(convert, check=True, capture_output=True)
        print(png, png_size(png))
    print(len(sheets), "sheets; slide times are in", mhtml)


def slice_frame(video_id, start, end, directory, frame="20"):
    url = video_url(video_id)
    start, end = (check(TIME, t, "time") for t in (start, end))
    directory = scratch_dir(directory)
    os.chdir(directory)
    frame = int(check(re.compile(r"\d{1,5}"), frame, "frame"))
    clip = fresh(os.path.join(directory, "clip.mp4"))
    png = fresh(os.path.join(directory, "frame.png"))
    ff = ffmpeg()
    subprocess.run(
        [
            "yt-dlp",
            "--ignore-config",
            "--force-overwrites",
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
    # ffmpeg exits 0 and writes nothing when FRAME is past the clip's end.
    try:
        st = os.lstat(png)
    except OSError:
        st = None
    if not (st and stat.S_ISREG(st.st_mode) and st.st_size):
        fail(f"no frame {frame} in the {start}-{end} clip; pick a lower FRAME")
    print(png)


def main(argv):
    if not argv:
        fail("no subcommand")
    cmd, args = argv[0], argv[1:]
    # Tools read config from their working dir (yt-dlp.conf, uv.toml), so never
    # run them from wherever the researcher's shell happens to be.
    os.chdir(os.path.realpath(tempfile.gettempdir()))
    if cmd == "scratch" and not args:
        print(os.path.realpath(tempfile.mkdtemp(prefix=PREFIX)))
    elif cmd == "clean" and len(args) == 1:
        directory = scratch_dir(args[0])
        if not shutil.rmtree.avoids_symlink_attacks:
            fail("this platform's rmtree can follow symlinks; refusing to delete")
        shutil.rmtree(directory)
    elif cmd == "transcript" and len(args) == 1:
        # `--` so an id starting with "-" is not read as an option.
        run(
            [
                "uvx",
                "--no-config",
                "youtube_transcript_api",
                "--",
                check(VIDEO_ID, args[0], "video id"),
            ]
        )
    elif cmd == "formats" and len(args) == 1:
        run(["yt-dlp", "--ignore-config", "-F", video_url(args[0])])
    elif cmd == "storyboard" and len(args) == 2:
        storyboard(*args)
    elif cmd == "slice" and len(args) in (4, 5):
        slice_frame(*args)
    elif cmd == "reddit" and args:
        path = "/" + check(REDDIT_PATH, args[0], "reddit path").lstrip("/")
        params = [tuple(check(PARAM, p, "KEY=VALUE").split("=", 1)) for p in args[1:]]
        query = "?" + urllib.parse.urlencode(params) if params else ""
        run(["curl", "-s", "-A", "curl/8.7.1", MIRROR + path + query])
    else:
        fail(f"unknown subcommand or wrong argument count: {cmd!r}")


if __name__ == "__main__":
    main(sys.argv[1:])

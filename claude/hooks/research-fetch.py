#!/usr/bin/env -S python3 -I
"""The researcher's one Bash command: the fetch-media recipes behind fixed argv.

The researcher's Bash gate (researcher-bash-gate.py) allows this script and
nothing else, so every argument is validated here and passed to a fixed program
without a shell.

  research-fetch.py help                    print this usage
  research-fetch.py scratch                 make a scratch dir and print its path
  research-fetch.py clean DIR               delete a scratch dir
  research-fetch.py transcript VIDEO_ID     print a YouTube video's transcript
  research-fetch.py formats VIDEO_ID        list a video's formats, sb0 included
  research-fetch.py storyboard VIDEO_ID DIR write storyboard sheets as PNGs
  research-fetch.py slice VIDEO_ID START END DIR [FRAME]
                                            write one video frame to DIR/frame.png
  research-fetch.py reddit /r/SUB/PATH [KEY=VALUE ...]
                                            fetch a Reddit page from a mirror
  research-fetch.py get 'URL' [MAX_KB] [DIR]
                                            fetch a public web page with curl as
                                            text, for when WebFetch gets a 403,
                                            a redirect notice, or an empty
                                            JavaScript page

DIR is a path printed by `scratch`, with no trailing slash. `get` prints the
final URL and HTTP status first, then at most MAX_KB (default 200) of text; it
saves a PDF to DIR/page.pdf only when DIR is given. Single-quote the URL.
"""

import email
import html.parser
import ipaddress
import math
import os
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
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
# Printable ASCII with no backslash, quote, or space.
URL_CHARS = re.compile(r"[!#-&(-\[\]-~]+")
# A hostname is letters, digits, and hyphens in dot-separated labels. A name
# whose last label is numeric is an IPv4 address in some notation (0177.0.0.1,
# 0x7f.1, 2130706433) that resolvers and curl read differently, so only the
# canonical dotted-decimal form ipaddress prints is accepted for those.
HOSTNAME = re.compile(
    r"([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]*[a-z0-9])?\.?"
)
NUMERIC_LABEL = re.compile(r"(0x[0-9a-f]*|[0-9]+)\.?")
# Not covered by is_global: IPv4-compatible, IPv4-mapped, SIIT, site-local.
REFUSED_V6 = [
    ipaddress.ip_network(n)
    for n in ("::/96", "::ffff:0:0/96", "::ffff:0:0:0/96", "fec0::/10")
]
NAT64 = ipaddress.ip_network("64:ff9b::/96")
ACCEPT = "text/html,*/*;q=0.8"
MAX_REDIRECTS = 5
TIMEOUT = 30
RAW_CAP = 20 * 1024 * 1024
TEXT_TYPES = re.compile(
    r"text/.*|application/(json|xml|javascript|ld\+json|.*\+(xml|json))"
)
SKIP_TAGS = {"script", "style", "nav", "noscript", "template", "svg"}
BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "pre", "blockquote", "table",
    "ul", "ol", "dt", "dd", "title",
}  # fmt: skip


def fail(message):
    sys.exit(f"research-fetch: {message}\n{__doc__}")


def public_address(host, port):
    """Resolve host and return one address to pin, refusing loopback, private,
    link-local, metadata, and other non-public addresses."""
    try:
        found = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as e:
            fail(f"cannot resolve {host}: {e}")
        found = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    addresses = []
    for ip in found:
        embedded = None
        if ip.version == 6 and ip in NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if (
            not ip.is_global
            or ip.is_multicast
            or (ip.version == 6 and any(ip in net for net in REFUSED_V6))
            or (embedded and not embedded.is_global)
        ):
            fail(f"refusing non-public address {ip} for {host}")
        addresses.append(ip)
    if not addresses:
        fail(f"no address for {host}")
    return addresses[0]


def split_url(url):
    """Return (host, port) for an http(s) URL whose host is a plain hostname
    or a canonical IP literal, so the address checked is the address meant."""
    if not URL_CHARS.fullmatch(url):
        fail(f"bad URL: {url!r}")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        fail(f"bad URL: {url}")
    if parts.scheme not in ("http", "https"):
        fail(f"only http and https URLs are allowed: {url}")
    if "@" in parts.netloc:
        fail(f"URLs with credentials are not allowed: {url}")
    netloc = parts.netloc.lower()
    host = parts.hostname or ""
    bracketed = netloc.startswith("[")
    shown = f"[{host}]" if bracketed else host
    if netloc not in (shown, f"{shown}:{parts.port}"):
        fail(f"bad host in {url}")
    try:
        ip = ipaddress.IPv6Address(host) if bracketed else ipaddress.ip_address(host)
        canonical = str(ip) == host
    except ValueError:
        last = host.rstrip(".").rsplit(".", 1)[-1]
        canonical = (
            not bracketed
            and HOSTNAME.fullmatch(host)
            and not NUMERIC_LABEL.fullmatch(last)
        )
    if not canonical:
        fail(f"bad host in {url}; use a hostname or a canonical IP address")
    return host, port


class Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def fetch_once(url, work, deadline):
    """One request with no redirect following. --connect-to sends every
    connection to the address public_address checked, so neither DNS changes
    nor curl parsing the URL differently can reach another host."""
    host, port = split_url(url)
    ip = public_address(host, port)
    pin = f"[{ip}]" if ip.version == 6 else str(ip)
    body_path, header_path = (os.path.join(work, n) for n in ("body", "headers"))
    argv = [
        "curl",
        "-q",  # first, so no ~/.curlrc adds options
        "-sS",
        "-g",
        "--compressed",
        "--proto",
        "=http,https",
        "--proto-redir",
        "=http,https",
        "--noproxy",
        "*",
        "--connect-to",
        f"::{pin}:{port}",
        "--max-time",
        str(max(1, math.ceil(deadline - time.monotonic()))),
        "--max-filesize",
        str(RAW_CAP),
        "-H",
        f"Accept: {ACCEPT}",
        "-D",
        header_path,
        "--",
        url,
    ]
    with (
        open(body_path, "wb") as out,
        open(os.path.join(work, "stderr"), "w+b") as err,
    ):
        proc = subprocess.Popen(argv, stdout=out, stderr=err)
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    fail(f"timed out (limit {TIMEOUT} s for all redirects): {url}")
                if os.fstat(out.fileno()).st_size > RAW_CAP:
                    fail(f"response is larger than {RAW_CAP // 2**20} MB: {url}")
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        if proc.returncode:
            err.seek(0)
            fail(
                f"curl failed for {url}: {err.read().decode(errors='replace').strip()}"
            )
    with open(header_path, "rb") as f:
        blocks = re.split(rb"\r?\n\r?\n", f.read().strip())
    lines = blocks[-1].decode("latin-1").splitlines()
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return int(lines[0].split()[1]), headers


def download(url):
    """Follow up to MAX_REDIRECTS redirects, checking each hop's URL and
    address, within one TIMEOUT; return the final URL, status, headers, body."""
    deadline = time.monotonic() + TIMEOUT
    work = tempfile.mkdtemp(prefix="research-get-")
    try:
        for _ in range(MAX_REDIRECTS + 1):
            status, headers = fetch_once(url, work, deadline)
            if not (300 <= status < 400 and headers.get("location")):
                with open(os.path.join(work, "body"), "rb") as f:
                    return url, status, headers, f.read()
            try:
                target = urllib.parse.urljoin(url, headers["location"])
            except ValueError:
                fail(f"bad redirect from {url}: {headers['location']!r}")
            # Spaces and non-ASCII become %XX; split_url then checks the result.
            url = urllib.parse.quote(target, safe="!#$%&'()*+,-./:;=?@[]~_")
        fail(f"more than {MAX_REDIRECTS} redirects")
    finally:
        shutil.rmtree(work)


def show_text(body, kind, params, max_kb):
    charset = re.search(r"charset=\"?([\w.:-]+)", params)
    try:
        text = body.decode(charset.group(1) if charset else "utf-8", "replace")
    except LookupError:
        text = body.decode("utf-8", "replace")
    if not kind and "\x00" in text:
        fail("refusing binary content with no content type")
    if kind in ("text/html", "application/xhtml+xml") or (
        not kind and "<html" in text[:1024].lower()
    ):
        parser = Text()
        parser.feed(text)
        parser.close()
        text = parser.text()
    cap = max_kb * 1024
    data = text.encode()
    sys.stdout.write(data[:cap].decode(errors="ignore"))
    if len(data) > cap:
        sys.stdout.write(f"\n[truncated at {max_kb} KB; pass a larger MAX_KB]")
    sys.stdout.write("\n")


def get(url, *rest):
    max_kb, directory = 200, None
    for arg in rest:
        if re.fullmatch(r"\d{1,5}", arg) and int(arg) > 0:
            max_kb = int(arg)
        elif arg.startswith("/"):
            directory = scratch_dir(arg)
        else:
            fail(f"bad argument to get: {arg!r}")
    url, status, headers, body = download(url)
    print(url, status)
    kind, _, params = headers.get("content-type", "").partition(";")
    kind = kind.strip().lower()
    if kind == "application/pdf" or body.startswith(b"%PDF"):
        if not directory:
            fail("this is a PDF; pass a scratch DIR to save it as DIR/page.pdf")
        pdf = os.path.join(directory, "page.pdf")
        write_new(pdf, body)
        print(pdf, len(body), "bytes")
    elif kind and not TEXT_TYPES.fullmatch(kind):
        fail(f"refusing binary content type {kind}")
    else:
        show_text(body, kind, params, max_kb)
    if status >= 400:
        sys.exit(1)


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
    cmd, args = (argv or ["(none)"])[0], argv[1:]
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
    elif cmd == "get" and 1 <= len(args) <= 3:
        get(*args)
    else:
        fail(f"unknown subcommand or wrong argument count: {cmd!r}")


if __name__ == "__main__":
    if sys.argv[1:] in (["help"], ["--help"], ["-h"]):
        print(__doc__)
    else:
        main(sys.argv[1:])

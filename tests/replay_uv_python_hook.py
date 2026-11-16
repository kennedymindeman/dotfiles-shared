"""Replay real Bash calls from Claude Code transcripts through uv-python.py.

  python3 tests/replay_uv_python_hook.py [--days 7] [--show N]

Reads every ~/.claude/projects/**/*.jsonl, keeps Bash calls from the last
--days, and prints how many the hook would block, the top reasons, and the most
common blocked command shapes. --show prints N blocked commands per shape; they
are private, so keep them out of PRs.
"""

import argparse
import collections
import datetime
import importlib.util
import json
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "claude/hooks/uv-python.py"
spec = importlib.util.spec_from_file_location("hook", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def calls(since):
    for transcript in Path.home().glob(".claude/projects/**/*.jsonl"):
        if transcript.stat().st_mtime < since.timestamp():
            continue
        for line in transcript.read_text(errors="replace").splitlines():
            try:
                entry = json.loads(line)
                stamp = datetime.datetime.fromisoformat(
                    entry["timestamp"].replace("Z", "+00:00")
                )
                content = entry["message"]["content"]
            except (ValueError, KeyError, TypeError):
                continue
            if stamp < since or not isinstance(content, list):
                continue
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    command = block.get("input", {}).get("command")
                    if isinstance(command, str):
                        yield command


def shape(command):
    """The blocked word and its first argument, with file names generalized."""
    for tokens in hook.commands(command):
        found = hook.command_word(tokens)
        if found and hook.BARE.fullmatch(found[1]):
            i, word = found
            args = tokens[i + 1 : i + 3]
            if args[:1] != ["-m"]:
                args = args[:1]
            args = ["FILE.py" if a.endswith(".py") else a for a in args]
            return " ".join([word] + args)
    return "?"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--show", type=int, default=0)
    args = parser.parse_args()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=args.days
    )
    total, reasons, shapes = 0, collections.Counter(), collections.defaultdict(list)
    for command in calls(since):
        total += 1
        try:
            reason = hook.check(command)
        except Exception:  # noqa: BLE001, S112 - the hook fails open on these
            continue
        if reason:
            reasons[reason.split(".")[0]] += 1
            shapes[shape(command)].append(command)
    blocked = sum(reasons.values())
    print(f"Bash calls in the last {args.days} days: {total}")
    print(f"blocked: {blocked}  allowed: {total - blocked}")
    print("top reasons:")
    for reason, count in reasons.most_common(10):
        print(f"{count:6}  {reason}")
    print("top blocked command shapes:")
    for name, commands in sorted(shapes.items(), key=lambda kv: -len(kv[1]))[:15]:
        print(f"{len(commands):6}  {name}")
        for command in commands[: args.show]:
            print("          ", command[:200].replace("\n", "\\n"))


if __name__ == "__main__":
    main()

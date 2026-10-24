"""Replay real researcher Bash calls from Claude Code transcripts through the gate.

  python3 tests/replay_researcher_bash_gate.py [--days 7] [--show N]

Reads ~/.claude/projects/**/subagents/agent-*.jsonl whose .meta.json says
agentType "researcher", keeps Bash calls from the last --days, and prints how
many the gate would block and the top reasons. --show prints N blocked commands
per reason; they are private, so keep them out of PRs. Scratch-dir checks run
against today's filesystem, so an `rm -rf` of a dir that is gone counts as blocked.
"""

import argparse
import collections
import datetime
import importlib.util
import json
import re
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "claude/hooks/researcher-bash-gate.py"
spec = importlib.util.spec_from_file_location("gate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def calls(since):
    for meta in Path.home().glob(".claude/projects/**/subagents/agent-*.meta.json"):
        if json.loads(meta.read_text()).get("agentType") != "researcher":
            continue
        transcript = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        if not transcript.exists():
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
                    yield block.get("input", {}).get("command", "")


def kind(reason):
    """Group reasons: drop the paths and characters that make each one unique."""
    reason = re.sub(r"^\S+ is not a dir made", "<path> is not a dir made", reason)
    return re.sub(r"^shell syntax not allowed: .*", "shell syntax not allowed", reason)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--show", type=int, default=0)
    args = parser.parse_args()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=args.days
    )
    total, reasons, chars = 0, collections.defaultdict(list), collections.Counter()
    for command in calls(since):
        total += 1
        reason = gate.check(command)
        if reason:
            reasons[kind(reason)].append(command)
            if reason.startswith("shell syntax"):
                chars.update(set(gate.SAFE.sub("", command)))
    blocked = sum(len(v) for v in reasons.values())
    print(f"researcher Bash calls in the last {args.days} days: {total}")
    print(f"blocked: {blocked}  allowed: {total - blocked}")
    for reason, commands in sorted(reasons.items(), key=lambda kv: -len(kv[1]))[:15]:
        print(f"{len(commands):6}  {reason}")
        for command in commands[: args.show]:
            print("          ", command[:200].replace("\n", "\\n"))
    if chars:
        print("disallowed characters, by calls containing them:")
        print("  ", ", ".join(f"{c!r} {n}" for c, n in chars.most_common(12)))


if __name__ == "__main__":
    main()

"""Replay real Bash calls from Claude Code transcripts through pr-merge-gate.sh.

  python3 tests/replay_pr_merge_gate.py [--days 7] [--protected main]

Reads ~/.claude/projects/**/*.jsonl, keeps Bash calls from the last --days, and
runs each through the hook with PR_MERGE_GATE_PROTECTED_BASES set to
--protected. `gh pr merge` calls query GitHub read-only for the PR's current
state, so verdicts reflect today's PRs, not the day of the call. Prints how
many calls it would block, the top reasons, and hook latency.
"""

import argparse
import collections
import datetime
import json
import os
import statistics
import subprocess
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "claude/hooks/pr-merge-gate.sh"


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
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if stamp < since or not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == "Bash"
                ):
                    yield block.get("input", {}), entry.get("cwd", "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--protected", default="main")
    args = parser.parse_args()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=args.days
    )
    env = {**os.environ, "PR_MERGE_GATE_PROTECTED_BASES": args.protected}
    reasons = collections.Counter()
    timings = {"merge": [], "other": []}
    total = 0
    for tool_input, cwd in calls(since):
        total += 1
        payload = json.dumps({"tool_input": tool_input, "cwd": cwd})
        start = time.perf_counter()
        result = subprocess.run(
            [str(HOOK)],
            input=payload,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )
        kind = "merge" if "gh pr merge" in tool_input.get("command", "") else "other"
        timings[kind].append((time.perf_counter() - start) * 1000)
        if result.returncode == 2:
            first = result.stderr.splitlines()[0] if result.stderr else ""
            if "protected base" in first:
                reasons["protected base: " + first.split(" merges into ")[1]] += 1
            else:
                reasons["unresolved review threads"] += 1
    print(f"{total} Bash calls in the last {args.days} days")
    print(f"would block {sum(reasons.values())}")
    for reason, count in reasons.most_common():
        print(f"  {count:5}  {reason}")
    for kind, values in timings.items():
        if values:
            values.sort()
            p95 = values[int(len(values) * 0.95) - 1]
            print(
                f"latency {kind} ({len(values)} calls): median "
                f"{statistics.median(values):.1f} ms, p95 {p95:.1f} ms, "
                f"max {values[-1]:.1f} ms"
            )


if __name__ == "__main__":
    main()

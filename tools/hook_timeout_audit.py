#!/usr/bin/env python3
"""Find hook timeouts that Claude Code recorded outside Obi's process.

``HookTimer`` starts after Python has launched and the hook has parsed stdin. If
Claude kills a hook while Windows is still launching its shell/process chain,
the internal Obi log can record a fast success or no invocation at all. Claude's
project transcript is the authoritative source for those external timeouts: it
emits a ``hook_cancelled`` attachment with ``timedOut: true``.

This module keeps transcript scanning bounded by look-back window and file count.
It is used by ``hook_stats.py`` and ``healthcheck.py`` and can also run directly.
"""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


def _as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime without changing the represented instant."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def scan_claude_hook_timeouts(
    projects_dir: Path | str | None = None,
    *,
    hours: int = 24,
    since: datetime | None = None,
    now: datetime | None = None,
    max_files: int = 200,
    max_records: int = 1000,
    max_bytes_per_file: int = 8 * 1024 * 1024,
) -> list[dict[str, Any]]:
    """Return recent Claude-reported hook timeouts, newest first.

    Args:
        projects_dir: Claude's ``~/.claude/projects`` directory.
        hours: Look-back window when ``since`` is not supplied.
        since: Explicit lower bound, used by healthcheck to ignore failures that
            predate the currently deployed settings.
        now: Injectable clock for tests.
        max_files: Maximum recent transcript files to inspect.
        max_records: Maximum returned records after newest-first sorting.
        max_bytes_per_file: Read only this many bytes from each transcript tail.
    """
    root = Path(projects_dir) if projects_dir else Path.home() / ".claude" / "projects"
    if not root.is_dir():
        return []

    current = _as_utc(now or datetime.now(timezone.utc))
    cutoff = _as_utc(since) if since else current - timedelta(hours=max(0, hours))
    cutoff_epoch = cutoff.timestamp()

    candidate_limit = min(max(1, max_files), 2000)
    candidates: list[tuple[float, str, Path]] = []
    try:
        paths = root.rglob("*.jsonl")
        for path in paths:
            try:
                modified = path.stat().st_mtime
            except OSError:
                continue
            if modified >= cutoff_epoch:
                item = (modified, str(path), path)
                if len(candidates) < candidate_limit:
                    heapq.heappush(candidates, item)
                elif item[:2] > candidates[0][:2]:
                    heapq.heapreplace(candidates, item)
    except OSError:
        return []

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    records: list[dict[str, Any]] = []

    for _, _, path in candidates:
        try:
            size = path.stat().st_size
            byte_limit = min(max(1024, max_bytes_per_file), 64 * 1024 * 1024)
            start = max(0, size - byte_limit)
            with path.open("rb") as transcript:
                transcript.seek(start)
                if start:
                    transcript.readline()  # discard a partial JSONL record
                for line_number, raw_line in enumerate(transcript, start=1):
                    line = raw_line.decode("utf-8", errors="replace")
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    attachment = entry.get("attachment")
                    if not isinstance(attachment, dict):
                        continue
                    if attachment.get("type") != "hook_cancelled":
                        continue
                    if attachment.get("timedOut") is not True:
                        continue

                    timestamp = _parse_timestamp(entry.get("timestamp"))
                    if timestamp is None or timestamp < cutoff or timestamp > current:
                        continue

                    records.append(
                        {
                            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                            "event": str(
                                attachment.get("hookEvent")
                                or attachment.get("hookName")
                                or "unknown"
                            ),
                            "command": str(attachment.get("command") or "unknown"),
                            "duration_ms": _safe_int(attachment.get("durationMs")),
                            "timeout_ms": _safe_int(attachment.get("timeoutMs")),
                            "cwd": str(entry.get("cwd") or ""),
                            "session_id": str(entry.get("sessionId") or ""),
                            "transcript": str(path),
                            "line": line_number,
                            "line_origin": "file" if start == 0 else "bounded_tail",
                        }
                    )
        except OSError:
            continue

    records.sort(key=lambda record: record["timestamp"], reverse=True)
    return records[: min(max(1, max_records), 10_000)]


def summarize_hook_timeouts(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate timeout records without discarding the responsible command."""
    materialized = list(records)
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "max_duration_ms": 0, "timeout_ms": 0}
    )

    for record in materialized:
        key = (str(record.get("event") or "unknown"), str(record.get("command") or "unknown"))
        group = grouped[key]
        group["count"] += 1
        group["max_duration_ms"] = max(
            group["max_duration_ms"], _safe_int(record.get("duration_ms"))
        )
        group["timeout_ms"] = max(group["timeout_ms"], _safe_int(record.get("timeout_ms")))

    by_command = [
        {
            "event": event,
            "command": command,
            **values,
        }
        for (event, command), values in grouped.items()
    ]
    by_command.sort(
        key=lambda item: (item["count"], item["max_duration_ms"]), reverse=True
    )

    return {
        "total": len(materialized),
        "by_command": by_command,
        "latest": materialized[:10],
    }


def _format_summary(summary: dict[str, Any], hours: int) -> str:
    lines = [f"Claude hook timeouts — last {hours}h", f"Total: {summary['total']}"]
    for group in summary.get("by_command", []):
        lines.append(
            f"  {group['event']:<20} count={group['count']:>3}  "
            f"max={group['max_duration_ms']}ms  budget={group['timeout_ms']}ms"
        )
        lines.append(f"    {group['command']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit Claude transcripts for externally timed-out hooks."
    )
    parser.add_argument("--hours", type=int, default=24, help="Look-back window (default: 24).")
    parser.add_argument("--projects-dir", type=Path, help="Override ~/.claude/projects.")
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-bytes-per-file", type=int, default=8 * 1024 * 1024)
    parser.add_argument("--json", dest="emit_json", action="store_true")
    parser.add_argument(
        "--fail-on-timeout",
        action="store_true",
        help="Exit 1 when at least one timeout is found.",
    )
    args = parser.parse_args(argv)

    records = scan_claude_hook_timeouts(
        args.projects_dir,
        hours=args.hours,
        max_files=args.max_files,
        max_bytes_per_file=args.max_bytes_per_file,
    )
    summary = summarize_hook_timeouts(records)
    if args.emit_json:
        print(json.dumps(summary, indent=2))
    else:
        print(_format_summary(summary, args.hours))

    return 1 if args.fail_on_timeout and summary["total"] else 0


if __name__ == "__main__":
    sys.exit(main())

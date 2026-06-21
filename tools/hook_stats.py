#!/usr/bin/env python3
"""Print Obi hook-execution statistics from ``~/.claude/.obi/hook-execution-log.jsonl``.

Usage::

    python tools/hook_stats.py                # last 24h, table output
    python tools/hook_stats.py --hours 1      # last hour
    python tools/hook_stats.py --json         # machine-readable
    python tools/hook_stats.py --top 10       # show 10 slowest hooks

The data lives in JSONL written by ``HookTimer``. A missing or empty
log file is not an error — the report just shows zeros.
"""

import argparse
import json
import sys
from pathlib import Path

# Resolve hooks/ alongside this tool so the script runs from any cwd.
_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from core.hook_logger import (  # noqa: E402
    get_hook_log_path,
    get_hook_stats_extended,
    get_swallowed_stats,
)


def _format_table(stats: dict, top: int, swallowed: dict = None) -> str:
    """Render a compact human-readable summary."""
    if stats['total_executions'] == 0:
        return (
            f"No hook executions in the last {stats['window_hours']}h "
            f"(log: {get_hook_log_path()})"
        )

    lines = []
    lines.append(f"Hook stats — last {stats['window_hours']}h")
    lines.append(f"Log: {get_hook_log_path()}")
    lines.append("")
    lines.append(
        f"Total executions: {stats['total_executions']}  "
        f"errors: {stats['errors']}"
    )
    lines.append(
        f"avg: {stats['avg_duration_ms']}ms  "
        f"p50: {stats['p50_duration_ms']}ms  "
        f"p95: {stats['p95_duration_ms']}ms"
    )

    by_hook = stats.get('by_hook', {})
    by_hook_avg = stats.get('by_hook_avg_ms', {})
    by_hook_errors = stats.get('by_hook_errors', {})
    if by_hook:
        lines.append("")
        lines.append("Per hook:")
        # Stable ordering: highest count first.
        for name in sorted(by_hook, key=lambda n: by_hook[n], reverse=True):
            err = by_hook_errors.get(name, 0)
            avg = by_hook_avg.get(name, 0.0)
            lines.append(
                f"  {name:<20} count={by_hook[name]:>4}  "
                f"errors={err:>3}  avg={avg}ms"
            )

    # Swallowed-exception visibility (OPT-05 #178): a dead subsystem behind a
    # converted `except Exception: log_swallowed(...)` surfaces here even when
    # it never raises to the user.
    swallowed = swallowed or {}
    sw_window = swallowed.get('window_hours', 168)
    sw_total = swallowed.get('total', 0)
    lines.append("")
    lines.append(f"Swallowed errors last {sw_window}h: {sw_total}")
    by_component = swallowed.get('by_component', {})
    for comp in sorted(by_component, key=lambda c: by_component[c], reverse=True):
        lines.append(f"  {comp:<28} {by_component[comp]:>4}")

    slowest = stats.get('slowest_hooks', [])[:top]
    if slowest:
        lines.append("")
        lines.append(f"Top {len(slowest)} slowest by average duration:")
        for entry in slowest:
            lines.append(
                f"  {entry['hook_name']:<20} avg={entry['avg_ms']}ms "
                f"(count={entry['count']})"
            )

    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Show Obi hook-execution statistics.",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look-back window in hours (default: 24).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many slowest hooks to show (default: 5).",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit JSON instead of a human-readable table.",
    )
    args = parser.parse_args(argv)

    stats = get_hook_stats_extended(hours=args.hours, slowest_n=args.top)
    # Swallowed errors use a fixed 7-day window (per OPT-05) regardless of
    # --hours, since a dead subsystem is a slower-cadence signal than latency.
    swallowed = get_swallowed_stats(hours=168)

    if args.emit_json:
        stats['swallowed_errors_7d'] = swallowed
        print(json.dumps(stats, indent=2))
    else:
        print(_format_table(stats, top=args.top, swallowed=swallowed))

    return 0


if __name__ == "__main__":
    sys.exit(main())

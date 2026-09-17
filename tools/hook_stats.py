#!/usr/bin/env python3
"""Print Obi hook statistics, including Claude-level external timeouts.

Usage::

    python tools/hook_stats.py                # last 24h, table output
    python tools/hook_stats.py --hours 1      # last hour
    python tools/hook_stats.py --json         # machine-readable
    python tools/hook_stats.py --top 10       # show 10 slowest hooks

Internal timings live in JSONL written by ``HookTimer``. Claude-level timeouts
live in project transcripts because the process may be killed before HookTimer
starts or after it already recorded a fast handler body. Both are required for
an honest report. Missing logs/transcripts are not errors.
"""

import argparse
import json
import sys
from pathlib import Path

# Resolve hooks/ alongside this tool so the script runs from any cwd.
_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from core.hook_logger import (  # noqa: E402
    get_hook_log_path,
    get_hook_stats_extended,
    get_swallowed_stats,
)
from hook_timeout_audit import (  # noqa: E402
    scan_claude_hook_timeouts,
    summarize_hook_timeouts,
)


def _format_table(
    stats: dict,
    top: int,
    swallowed: dict = None,
    claude_timeouts: dict = None,
) -> str:
    """Render a compact human-readable summary."""
    lines = []
    lines.append(f"Hook stats — last {stats['window_hours']}h")
    lines.append(f"Log: {get_hook_log_path()}")
    lines.append("")
    if stats['total_executions'] == 0:
        lines.append("No hook executions recorded in the Obi internal log.")
    else:
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

    # A Claude timeout happens outside HookTimer's measured region. Keep this
    # section visible even when the internal log is empty, otherwise the exact
    # failure mode this report exists to diagnose is silently reported as zero.
    claude_timeouts = claude_timeouts or {"total": 0, "by_command": []}
    lines.append("")
    lines.append(
        f"Claude-reported hook timeouts last {stats['window_hours']}h: "
        f"{claude_timeouts.get('total', 0)}"
    )
    for group in claude_timeouts.get('by_command', []):
        lines.append(
            f"  {group['event']:<20} count={group['count']:>3}  "
            f"max={group['max_duration_ms']}ms  budget={group['timeout_ms']}ms"
        )
        lines.append(f"    {group['command']}")

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
    parser.add_argument(
        "--projects-dir",
        type=Path,
        help="Override Claude's ~/.claude/projects transcript directory.",
    )
    args = parser.parse_args(argv)

    stats = get_hook_stats_extended(hours=args.hours, slowest_n=args.top)
    # Swallowed errors use a fixed 7-day window (per OPT-05) regardless of
    # --hours, since a dead subsystem is a slower-cadence signal than latency.
    swallowed = get_swallowed_stats(hours=168)
    claude_timeouts = summarize_hook_timeouts(
        scan_claude_hook_timeouts(args.projects_dir, hours=args.hours)
    )

    if args.emit_json:
        stats['swallowed_errors_7d'] = swallowed
        stats['claude_timeouts'] = claude_timeouts
        print(json.dumps(stats, indent=2))
    else:
        print(
            _format_table(
                stats,
                top=args.top,
                swallowed=swallowed,
                claude_timeouts=claude_timeouts,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())

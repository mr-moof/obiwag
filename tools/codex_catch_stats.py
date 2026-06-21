#!/usr/bin/env python3
"""Print Codex catch analytics from ``.obi/codex-catches.jsonl``.

Usage::

    python tools/codex_catch_stats.py                  # table output
    python tools/codex_catch_stats.py --json            # machine-readable
    python tools/codex_catch_stats.py --days 30         # last 30 days only
    python tools/codex_catch_stats.py --repo-root /path # explicit repo root
    python tools/codex_catch_stats.py --check-threshold # run blindspot check

The data lives in per-repo ``.obi/codex-catches.jsonl`` written by
``tools/log-codex-catch.ps1``.  A missing or empty log is not an error
-- the report just shows zeros.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Resolve hooks/ alongside this tool so the script runs from any cwd.
_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))


def get_catches_path(repo_root: Optional[str] = None) -> Path:
    """Return the path to the codex catches JSONL file."""
    root = Path(repo_root) if repo_root else _REPO_ROOT
    return root / ".obi" / "codex-catches.jsonl"


def read_catches(catches_path: Path) -> List[Dict[str, Any]]:
    """Read all catch entries from the JSONL file.

    Skips blank lines and malformed JSON. Returns a list of dicts.
    """
    entries: List[Dict[str, Any]] = []
    if not catches_path.exists():
        return entries

    try:
        with open(catches_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return entries

    return entries


def deduplicate_catches(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by (ref, category), keeping the last entry."""
    seen: Dict[Tuple[str, str], int] = {}
    for idx, entry in enumerate(entries):
        key = (entry.get("ref", ""), entry.get("category", ""))
        seen[key] = idx

    return [entries[idx] for idx in sorted(seen.values())]


def is_confirmed(entry: Dict[str, Any]) -> bool:
    """Return True if the catch is confirmed (not disputed away).

    A confirmed catch is one where:
    - disputed is False, OR
    - disputed is True AND dispute_resolution is 'codex-right'

    A disputed catch with dispute_resolution 'claude-right' is NOT confirmed.
    """
    disputed = entry.get("disputed", False)
    if not disputed:
        return True
    return entry.get("dispute_resolution") == "codex-right"


def compute_stats(
    entries: List[Dict[str, Any]], days: int = 90
) -> Dict[str, Any]:
    """Compute aggregated statistics from catch entries."""
    deduped = deduplicate_catches(entries)
    total = len(deduped)

    # Counts by category
    by_category: Counter = Counter()
    by_severity: Counter = Counter()
    by_phase: Counter = Counter()
    confirmed_count = 0
    disputed_count = 0
    codex_right = 0
    claude_right = 0
    unresolved = 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    trend_count = 0

    for entry in deduped:
        cat = entry.get("category", "other")
        sev = entry.get("severity", "medium")
        phase = entry.get("phase", "unknown")
        by_category[cat] += 1
        by_severity[sev] += 1
        by_phase[phase] += 1

        if is_confirmed(entry):
            confirmed_count += 1

        if entry.get("disputed", False):
            disputed_count += 1
            resolution = entry.get("dispute_resolution")
            if resolution == "codex-right":
                codex_right += 1
            elif resolution == "claude-right":
                claude_right += 1
            else:
                unresolved += 1

        # 90-day (configurable) trend
        ts_str = entry.get("ts", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts >= cutoff:
                    trend_count += 1
            except (ValueError, TypeError):
                pass

    # Dispute win-rate
    resolved_disputes = codex_right + claude_right
    win_rate = (
        round(codex_right / resolved_disputes, 2)
        if resolved_disputes > 0
        else None
    )

    return {
        "total": total,
        "confirmed": confirmed_count,
        "by_category": dict(by_category.most_common()),
        "by_severity": dict(by_severity.most_common()),
        "by_phase": dict(by_phase.most_common()),
        "disputed": disputed_count,
        "codex_right": codex_right,
        "claude_right": claude_right,
        "unresolved": unresolved,
        "dispute_win_rate": win_rate,
        "trend_days": days,
        "trend_count": trend_count,
    }


def _format_table(stats: Dict[str, Any]) -> str:
    """Render a compact human-readable summary."""
    if stats["total"] == 0:
        return "No Codex catches recorded."

    lines = []
    lines.append("Codex Catch Analytics")
    lines.append("")
    lines.append(
        f"Total: {stats['total']}  "
        f"Confirmed: {stats['confirmed']}  "
        f"Disputed: {stats['disputed']}"
    )

    wr = stats["dispute_win_rate"]
    wr_str = f"{wr:.0%}" if wr is not None else "n/a"
    lines.append(
        f"Dispute win-rate (codex-right): {wr_str}  "
        f"(codex={stats['codex_right']} claude={stats['claude_right']} "
        f"unresolved={stats['unresolved']})"
    )

    lines.append(
        f"\n{stats['trend_days']}-day trend: {stats['trend_count']} catches"
    )

    by_cat = stats.get("by_category", {})
    if by_cat:
        lines.append("")
        lines.append("By category:")
        for cat, count in by_cat.items():
            lines.append(f"  {cat:<22} {count:>4}")

    by_sev = stats.get("by_severity", {})
    if by_sev:
        lines.append("")
        lines.append("By severity:")
        for sev, count in by_sev.items():
            lines.append(f"  {sev:<22} {count:>4}")

    by_phase = stats.get("by_phase", {})
    if by_phase:
        lines.append("")
        lines.append("By phase:")
        for phase, count in by_phase.items():
            lines.append(f"  {phase:<22} {count:>4}")

    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Show Codex catch analytics.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Trend window in days (default: 90).",
    )
    parser.add_argument(
        "--json",
        dest="emit_json",
        action="store_true",
        help="Emit JSON instead of a human-readable table.",
    )
    parser.add_argument(
        "--repo-root",
        type=str,
        default=None,
        help="Path to repo root (default: parent of tools/).",
    )
    parser.add_argument(
        "--check-threshold",
        action="store_true",
        help="Run the codex blindspot threshold check.",
    )
    args = parser.parse_args(argv)

    if args.check_threshold:
        try:
            from core.codex_blindspot import check_blindspot_thresholds
            generated = check_blindspot_thresholds(repo_root=args.repo_root)
            if generated:
                for learning in generated:
                    print(f"Blindspot learning proposed: {learning.title}")
                return 0
            else:
                print("No blindspot thresholds reached.")
                return 0
        except Exception as exc:
            print(f"Error running blindspot check: {exc}", file=sys.stderr)
            return 1

    catches_path = get_catches_path(args.repo_root)
    entries = read_catches(catches_path)
    stats = compute_stats(entries, days=args.days)

    if args.emit_json:
        print(json.dumps(stats, indent=2))
    else:
        print(_format_table(stats))

    return 0


if __name__ == "__main__":
    sys.exit(main())

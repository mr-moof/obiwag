"""Session-end drift nag (Issue #138).

When a deployed file (under ~/.claude/) is edited in-place during a
session, the change is invisible to the source repo until someone
remembers to run /obi-collect. This module snapshots the drift count
at session-start and compares at session-end, nudging when it grows.

The baseline lives in a single file so no per-session ID is required;
overlapping sessions will overwrite each other's baseline, which is
acceptable — the worst case is one missed nag.
"""

import json
import os
from typing import Optional, Tuple

from core.paths import get_obi_root


def get_baseline_path() -> str:
    """Path to the session-start drift baseline file."""
    return str(get_obi_root() / ".obi" / "drift-baseline.json")


def save_drift_baseline(total_drifted: int) -> bool:
    """Record the current drift count as this session's baseline.

    Returns True on success, False otherwise. Never raises.
    """
    path = get_baseline_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"total_drifted": int(total_drifted)}, f)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _load_baseline() -> Optional[int]:
    """Load the saved baseline count, or None if missing/unreadable."""
    path = get_baseline_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("total_drifted") if isinstance(data, dict) else None
    if isinstance(value, int):
        return value
    return None


def compute_drift_delta() -> Tuple[Optional[int], Optional[int]]:
    """Return (delta, current_total) for the end-of-session comparison.

    Returns ``(None, None)`` if the drift detector is unavailable or the
    baseline is missing.  delta > 0 means drift grew during the session.
    """
    try:
        from core.drift_detector import detect_drift
    except ImportError:
        return None, None

    try:
        current = detect_drift().get("total_drifted", 0)
    except Exception:
        return None, None

    baseline = _load_baseline()
    if baseline is None:
        return None, current

    return current - baseline, current


def format_drift_nag(delta: int) -> str:
    """Format the friendly session-end suggestion."""
    plural = "file" if delta == 1 else "files"
    return (
        f"[Obi] {delta} new drifted {plural} this session. "
        "Run /obi-collect to sync back."
    )

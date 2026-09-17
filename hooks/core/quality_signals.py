"""Quality signal computation for Obi Memory System.

Passive quality signals that observe agent tool-usage patterns and surface
precision problems as session metrics. Signals are observation-only.

Three signals:
- edit_without_read: files edited without being Read first in the session
- file_thrashing: files read 3+ times (suggests lost context)
- over_building_corrections: count of over-building corrections from stop hook
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from core.paths import get_obi_root
from core.hook_logger import log_swallowed


def _normalize_path(path: str) -> str:
    """Normalize a file path for comparison (Windows-safe)."""
    return path.replace('\\', '/').lower()


def find_edits_without_read(tools: list) -> List[str]:
    """Find files edited without being Read first in this session.

    Tracks a session-scoped set of files the agent has "seen" — either via
    Read, or via a prior Edit/Write (whose tool_result returns the post-edit
    content, so the agent has the file state in context for the next call).

    Flagging rules:
    - Edit a file not yet in the seen-set: flag once, then add to set
    - Subsequent Edits of the same file: not flagged (file is in set)
    - Write is excluded — it's the file-creation tool, and you can't read a
      file that doesn't exist yet. We do add Write paths to the seen-set so
      a later Edit doesn't re-flag.

    Replaces preceding-tool semantics that produced N false positives for N
    iterative Edits of the same file.
    """
    seen_paths = set()
    violations = []
    for entry in tools:
        tool = entry.get('tool', '')
        path = entry.get('path')
        if not path:
            continue
        normalized = _normalize_path(path)
        if tool == 'Read':
            seen_paths.add(normalized)
        elif tool == 'Edit':
            if normalized not in seen_paths:
                violations.append(path)
            seen_paths.add(normalized)
        elif tool == 'Write':
            seen_paths.add(normalized)
    return violations


def find_file_thrashing(tools: list, threshold: int = 3) -> Dict[str, int]:
    """Find files read 3+ times (suggests lost context or aimless exploration).

    Only counts Read tool calls. Grep/Glob target directories, not specific files.
    """
    read_counts: Dict[str, int] = {}
    for entry in tools:
        tool = entry.get('tool', '')
        path = entry.get('path')
        if not path:
            continue
        if tool == 'Read':
            normalized = _normalize_path(path)
            read_counts[normalized] = read_counts.get(normalized, 0) + 1
    return {p: c for p, c in read_counts.items() if c >= threshold}


def check_file_size(
    file_path: str,
    threshold: int = 400,
    *,
    max_lines: int = 5000,
    max_bytes: int = 2 * 1024 * 1024,
) -> int | None:
    """Check if a file exceeds the line count threshold.

    Returns the line count if it exceeds threshold, else None.
    Returns None for missing, unreadable, binary, or pathologically large
    single-line files. Counting stops after ``max_lines`` and returns
    ``max_lines + 1``; reads stop after ``max_bytes`` unless the threshold has
    already been proven. This keeps a 3-second PostToolUse hook from scanning a
    generated multi-gigabyte artifact merely to print an exact count.
    """
    try:
        count = 0
        consumed = 0
        last_byte = b''
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                if consumed == 0 and b'\0' in chunk[:8192]:
                    return None
                consumed += len(chunk)
                count += chunk.count(b'\n')
                last_byte = chunk[-1:]
                if count > max_lines:
                    return max_lines + 1
                if consumed >= max_bytes:
                    return None
        if consumed and last_byte != b'\n':
            count += 1
        return count if count > threshold else None
    except (FileNotFoundError, PermissionError, OSError):
        return None


def compute_quality_signals(tools_used: list, correction_details: list) -> dict:
    """Compute all quality signals from session tool usage and corrections.

    Args:
        tools_used: List of tool usage dicts from session state
        correction_details: List of correction dicts from stop hook analysis

    Returns:
        Dict with signal results (quality_clean computed at JSONL write time)
    """
    edit_without_read = find_edits_without_read(tools_used)
    file_thrashing = find_file_thrashing(tools_used)
    over_building_corrections = len([
        c for c in correction_details
        if c.get('type') == 'over_building'
    ])

    return {
        'edit_without_read': edit_without_read,
        'file_thrashing': file_thrashing,
        'over_building_corrections': over_building_corrections,
    }


def write_quality_signals_jsonl(
    session_id: str,
    task_type: str,
    signals: dict,
) -> None:
    """Append quality signals entry to session-quality.jsonl.

    Uses rotation from hook_logger to keep file under 500KB.
    """
    from core.hook_logger import append_rotating_jsonl

    obi_dir = get_obi_root() / '.obi'
    obi_dir.mkdir(parents=True, exist_ok=True)
    log_path = obi_dir / 'session-quality.jsonl'

    quality_clean = (
        len(signals.get('edit_without_read', [])) == 0
        and len(signals.get('file_thrashing', {})) == 0
        and signals.get('over_building_corrections', 0) == 0
    )

    entry = {
        'session_id': session_id,
        'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'task_type': task_type,
        'signals': signals,
        'quality_clean': quality_clean,
    }

    try:
        append_rotating_jsonl(log_path, entry, max_size_bytes=500_000)
    except Exception as exc:
        log_swallowed("quality_signal_write", exc)

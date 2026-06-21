"""Hook execution logging for Obi Memory System.

Provides centralized logging for hook execution visibility:
- HookTimer context manager for timing
- JSONL logging to ~/.claude/.obi/hook-execution-log.jsonl
- Utility functions for reading recent logs
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import get_obi_root


def get_hook_log_path() -> Path:
    """Get the path to the hook execution log file."""
    obi_dir = get_obi_root() / '.obi'
    obi_dir.mkdir(parents=True, exist_ok=True)
    return obi_dir / 'hook-execution-log.jsonl'


def get_swallowed_log_path() -> Path:
    """Path to the swallowed-exception log (sibling of the execution log).

    A separate file from hook-execution-log.jsonl: different schema and a
    longer-lived diagnostic cadence. A subsystem can be dead for weeks behind a
    bare ``except Exception: pass``; routing those through here makes the
    failure visible (surfaced by ``tools/hook_stats.py``) without ever blocking
    the caller.
    """
    obi_dir = get_obi_root() / '.obi'
    obi_dir.mkdir(parents=True, exist_ok=True)
    return obi_dir / 'swallowed-errors.jsonl'


def log_swallowed(component: str, exc: BaseException) -> None:
    """Record a deliberately-swallowed exception so dead subsystems are visible.

    Appends one JSONL line ``{timestamp, component, error_class, message}`` to
    ``swallowed-errors.jsonl``. The whole body is wrapped in try/except: logging
    a swallowed error must NEVER raise or block the caller — that is the entire
    point of the bare-except sites this replaces. ``message`` is flattened to a
    single line and truncated so the JSONL stays one record per line.

    Args:
        component: Short label for the subsystem that swallowed (e.g.
            ``"gc_maintenance"``, ``"drift_baseline_save"``).
        exc: The caught exception instance.
    """
    try:
        message = str(exc).replace('\n', ' ').replace('\r', ' ').strip()[:300]
        entry: Dict[str, Any] = {
            'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'component': str(component)[:80],
            'error_class': type(exc).__name__,
            'message': message,
        }
        from core.jsonl_helper import append_jsonl
        log_path = get_swallowed_log_path()
        append_jsonl(log_path, entry)
        _rotate_log_if_needed(log_path)
    except Exception:
        # Logging a swallowed error must itself never raise.
        pass


def get_swallowed_stats(hours: int = 168) -> Dict[str, Any]:
    """Aggregate swallowed-exception counts over the last ``hours`` (default 7d).

    Returns ``{window_hours, total, by_component}`` where ``by_component`` maps
    each component label to its count, most-recent window only. Empty, missing,
    or corrupt logs return a zeroed shape so callers don't special-case it.
    """
    path = get_swallowed_log_path()
    if not path.exists():
        return {'window_hours': hours, 'total': 0, 'by_component': {}}

    entries: List[Dict[str, Any]] = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return {'window_hours': hours, 'total': 0, 'by_component': {}}

    filtered = _filter_logs_by_hours(entries, hours)
    by_component: Dict[str, int] = {}
    for entry in filtered:
        comp = entry.get('component', 'unknown')
        by_component[comp] = by_component.get(comp, 0) + 1

    return {
        'window_hours': hours,
        'total': len(filtered),
        'by_component': dict(sorted(by_component.items(), key=lambda kv: kv[1], reverse=True)),
    }


class HookTimer:
    """Context manager for timing and logging hook execution.

    Usage:
        with HookTimer("SessionStart") as timer:
            timer.set_input_summary("prompt=hello world")
            # ... hook logic ...
            timer.set_output_summary("context injected")
    """

    def __init__(self, hook_name: str):
        """Initialize the timer.

        Args:
            hook_name: Name of the hook (e.g., "SessionStart", "PreToolUse")
        """
        self.hook_name = hook_name
        self.start_time: float = 0
        self.end_time: float = 0
        self.status: str = 'success'
        self.error_message: Optional[str] = None
        self.input_summary: Optional[str] = None
        self.output_summary: Optional[str] = None

    def __enter__(self) -> 'HookTimer':
        """Start timing on context entry."""
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Stop timing and log on context exit."""
        self.end_time = time.time()

        if exc_type is not None:
            self.status = 'error'
            # Preserve a manually-supplied error_message — set_error() is the
            # only way callers can distinguish a real error message from the
            # SystemExit(0) raised by respond_with_error after a handler
            # exception. Without this guard the execution log would record
            # the literal string "0" instead of the original exception text.
            if not self.error_message:
                self.error_message = str(exc_val) if exc_val else exc_type.__name__

        self._write_log_entry()

        # Don't suppress exceptions
        return False

    def set_input_summary(self, summary: str) -> None:
        """Set a summary of the input for logging.

        Args:
            summary: Brief description of input (e.g., "prompt=hello world")
        """
        # Truncate to reasonable length
        self.input_summary = summary[:200] if summary else None

    def set_output_summary(self, summary: str) -> None:
        """Set a summary of the output for logging.

        Args:
            summary: Brief description of output (e.g., "context injected")
        """
        # Truncate to reasonable length
        self.output_summary = summary[:200] if summary else None

    def set_error(self, error_message: str) -> None:
        """Manually set an error status.

        Args:
            error_message: Description of the error
        """
        self.status = 'error'
        self.error_message = error_message[:500] if error_message else None

    @property
    def duration_ms(self) -> int:
        """Get the duration in milliseconds."""
        if self.end_time and self.start_time:
            return int((self.end_time - self.start_time) * 1000)
        return 0

    def _write_log_entry(self) -> None:
        """Write the log entry to the JSONL file."""
        try:
            log_entry: Dict[str, Any] = {
                'timestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'hook_name': self.hook_name,
                'status': self.status,
                'duration_ms': self.duration_ms,
            }

            if self.error_message:
                log_entry['error'] = self.error_message
            if self.input_summary:
                log_entry['input'] = self.input_summary
            if self.output_summary:
                log_entry['output'] = self.output_summary

            log_path = get_hook_log_path()

            # Append to file
            from core.jsonl_helper import append_jsonl
            append_jsonl(log_path, log_entry)

            # Rotate if file gets too large (>1MB)
            _rotate_log_if_needed(log_path)

        except Exception:
            # Never let logging failure block hook execution
            pass


def _rotate_log_if_needed(log_path: Path, max_size_bytes: int = 1_000_000) -> None:
    """Rotate the log file if it exceeds max size.

    Keeps the most recent half of entries when rotating.

    Args:
        log_path: Path to the log file
        max_size_bytes: Maximum file size before rotation (default 1MB)
    """
    try:
        if not log_path.exists():
            return

        file_size = log_path.stat().st_size
        if file_size <= max_size_bytes:
            return

        # Read all lines
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Keep the most recent half
        keep_count = len(lines) // 2
        lines_to_keep = lines[-keep_count:] if keep_count > 0 else lines[-100:]

        # Write back
        with open(log_path, 'w', encoding='utf-8') as f:
            f.writelines(lines_to_keep)

    except Exception:
        pass


def get_recent_hook_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Read the most recent hook execution logs.

    Args:
        limit: Maximum number of entries to return (default 50)

    Returns:
        List of log entry dictionaries, most recent first
    """
    log_path = get_hook_log_path()

    if not log_path.exists():
        return []

    try:
        entries = []
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Return most recent first, limited
        return entries[-limit:][::-1]

    except Exception:
        return []


def get_hook_stats(hours: int = 24) -> Dict[str, Any]:
    """Get aggregate statistics about hook execution.

    Args:
        hours: Number of hours to look back (default 24)

    Returns:
        Dictionary with stats including:
        - total_executions: int
        - by_hook: Dict[str, int] - count per hook
        - errors: int - total error count
        - avg_duration_ms: float - average duration
    """
    logs = get_recent_hook_logs(limit=1000)

    if not logs:
        return {
            'total_executions': 0,
            'by_hook': {},
            'errors': 0,
            'avg_duration_ms': 0.0,
        }

    # Filter by time window
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    filtered = []
    for entry in logs:
        try:
            ts_str = entry.get('timestamp', '')
            if ts_str:
                # Parse ISO format
                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                if ts.timestamp() >= cutoff:
                    filtered.append(entry)
        except Exception:
            continue

    if not filtered:
        return {
            'total_executions': 0,
            'by_hook': {},
            'errors': 0,
            'avg_duration_ms': 0.0,
        }

    by_hook: Dict[str, int] = {}
    errors = 0
    total_duration = 0

    for entry in filtered:
        hook_name = entry.get('hook_name', 'unknown')
        by_hook[hook_name] = by_hook.get(hook_name, 0) + 1

        if entry.get('status') == 'error':
            errors += 1

        total_duration += entry.get('duration_ms', 0)

    return {
        'total_executions': len(filtered),
        'by_hook': by_hook,
        'errors': errors,
        'avg_duration_ms': round(total_duration / len(filtered), 2) if filtered else 0.0,
    }


def _filter_logs_by_hours(logs: List[Dict[str, Any]], hours: int) -> List[Dict[str, Any]]:
    """Return only entries whose timestamp falls within the last ``hours`` hours.

    Bad timestamps are dropped silently. Used by both ``get_hook_stats`` (kept
    inline above for back-compat) and ``get_hook_stats_extended``.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    filtered: List[Dict[str, Any]] = []
    for entry in logs:
        ts_str = entry.get('timestamp', '')
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if ts.timestamp() >= cutoff:
            filtered.append(entry)
    return filtered


def _percentile(sorted_values: List[float], pct: float) -> float:
    """Return the percentile value (0..100) from a pre-sorted ascending list.

    Uses nearest-rank — fine for hook-execution timing where we want a
    single concrete observation, not an interpolated value. Returns 0.0
    on an empty list.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    idx = int(round(rank))
    idx = max(0, min(idx, len(sorted_values) - 1))
    return float(sorted_values[idx])


def get_hook_stats_extended(hours: int = 24, slowest_n: int = 5) -> Dict[str, Any]:
    """Aggregate hook execution metrics with percentiles + per-hook breakdown.

    Wraps the same data as ``get_hook_stats`` but adds:
      - ``p50_duration_ms`` and ``p95_duration_ms`` over all entries
      - ``by_hook_avg_ms``: per-hook mean duration
      - ``by_hook_errors``: per-hook error counts
      - ``slowest_hooks``: top-N hook names by average duration

    Args:
        hours: Window size, in hours.
        slowest_n: How many entries to keep in ``slowest_hooks``.

    Empty / missing / corrupt log files return a zeroed-out shape so
    callers don't have to special-case the empty path.
    """
    logs = get_recent_hook_logs(limit=10000)
    filtered = _filter_logs_by_hours(logs, hours)

    if not filtered:
        return {
            'window_hours': hours,
            'total_executions': 0,
            'by_hook': {},
            'by_hook_avg_ms': {},
            'by_hook_errors': {},
            'errors': 0,
            'avg_duration_ms': 0.0,
            'p50_duration_ms': 0.0,
            'p95_duration_ms': 0.0,
            'slowest_hooks': [],
        }

    by_hook: Dict[str, int] = {}
    by_hook_durations: Dict[str, List[float]] = {}
    by_hook_errors: Dict[str, int] = {}
    durations: List[float] = []
    errors = 0

    for entry in filtered:
        name = entry.get('hook_name', 'unknown')
        by_hook[name] = by_hook.get(name, 0) + 1
        duration = entry.get('duration_ms', 0)
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0.0
        durations.append(duration)
        by_hook_durations.setdefault(name, []).append(duration)
        if entry.get('status') == 'error':
            errors += 1
            by_hook_errors[name] = by_hook_errors.get(name, 0) + 1

    sorted_durations = sorted(durations)
    by_hook_avg_ms = {
        name: round(sum(values) / len(values), 2)
        for name, values in by_hook_durations.items()
        if values
    }

    slowest = sorted(
        ({'hook_name': name, 'avg_ms': avg, 'count': by_hook[name]}
         for name, avg in by_hook_avg_ms.items()),
        key=lambda d: d['avg_ms'],
        reverse=True,
    )[:slowest_n]

    return {
        'window_hours': hours,
        'total_executions': len(filtered),
        'by_hook': by_hook,
        'by_hook_avg_ms': by_hook_avg_ms,
        'by_hook_errors': by_hook_errors,
        'errors': errors,
        'avg_duration_ms': round(sum(durations) / len(durations), 2),
        'p50_duration_ms': round(_percentile(sorted_durations, 50), 2),
        'p95_duration_ms': round(_percentile(sorted_durations, 95), 2),
        'slowest_hooks': slowest,
    }

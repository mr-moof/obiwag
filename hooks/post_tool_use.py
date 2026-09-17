#!/usr/bin/env python3
"""PostToolUse hook for Obi Memory System.

This hook runs after each tool use to:
1. Track tool usage metrics for later analysis in Stop hook
2. Log tool names to persistent session state

NOTE: Correction detection has been moved to the Stop hook because:
- PostToolUse receives tool_name, tool_input, tool_result
- It does NOT receive user_message or follow_up fields
- User corrections can only be detected by analyzing the full transcript
- The Stop hook has access to the full transcript for this analysis
"""

import fnmatch
import os
import re
import sys
import time
from datetime import datetime, timezone

# Add hook root to path for imports
HOOK_ROOT = os.path.dirname(os.path.abspath(__file__))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)


# --- Heartbeat touch (OPT-22) ---
# Module-level cache: read run-id.txt at most once per process (OPT-03
# no-redundant-read).  Each PostToolUse invocation is a fresh Python
# process, but multiple calls within the same process hit the cache.
_RUN_ID = None
_RUN_ID_LOADED = False
_MAX_TRACKED_TOOL_EVENTS = 1000
# Heartbeat is a staleness signal for the watchdog, not an event log: one write
# per PostToolUse call is wasted I/O. Tests set this to 0 to exercise a write.
_HEARTBEAT_MIN_INTERVAL_SEC = 15.0
_EPHEMERAL_SIZE_SUFFIXES = (
    '.diff', '.jsonl', '.log', '.out', '.patch', '.tmp',
)


def _touch_heartbeat(tool_name: str) -> None:
    """Write a heartbeat JSON file for the watchdog to poll.

    Throttled to at most one write per ``_HEARTBEAT_MIN_INTERVAL_SEC`` by
    comparing the existing file's mtime: PostToolUse fires on every tool call,
    and the watchdog only needs staleness at minute granularity, so a write per
    call is pure I/O cost on the hook's 3 s budget.

    Best-effort: any OSError is swallowed so the heartbeat never blocks
    tool use.  No-op when no autonomous run is active (run-id.txt absent).
    """
    global _RUN_ID, _RUN_ID_LOADED
    if not _RUN_ID_LOADED:
        _RUN_ID_LOADED = True
        run_id_path = os.path.join('.obi', 'state', 'run-id.txt')
        try:
            with open(run_id_path, 'r') as f:
                _RUN_ID = f.read().strip()
        except OSError:
            _RUN_ID = None
    if not _RUN_ID:
        return
    hb_path = os.path.join('.obi', 'state', f'heartbeat-{_RUN_ID}.json')
    try:
        age = time.time() - os.path.getmtime(hb_path)
        if 0 <= age < _HEARTBEAT_MIN_INTERVAL_SEC:
            return
    except OSError:
        pass  # no previous heartbeat (or unreadable) — write one
    try:
        import json as _json
        with open(hb_path, 'w') as f:
            _json.dump({"ts": datetime.now(timezone.utc).isoformat(), "tool": tool_name}, f)
    except OSError:
        pass  # heartbeat is best-effort


def _path_matches_any_glob(path, patterns):
    """Return True if path matches any fnmatch pattern.

    Paths are normalized to lowercased forward slashes before matching,
    so patterns like `**/vendor/**` or `**/*.generated.*` work on Windows.
    """
    if not patterns:
        return False
    norm = path.replace("\\", "/").lower()
    for pattern in patterns:
        if not pattern:
            continue
        pat_norm = pattern.replace("\\", "/").lower()
        if fnmatch.fnmatch(norm, pat_norm):
            return True
    return False


def _is_ephemeral_quality_artifact(path: str) -> bool:
    """Exclude generated review/log output from source-module size policy."""
    norm = path.replace('\\', '/').lower()
    return (
        norm.endswith(_EPHEMERAL_SIZE_SUFFIXES)
        or '/.obi/review/runs/' in norm
        or '/.obi/state/worker-' in norm
    )


def _append_tool_event(tools_used, event):
    """Return a bounded rolling tool history for advisory quality signals."""
    history = list(tools_used) if isinstance(tools_used, list) else []
    history.append(event)
    return history[-_MAX_TRACKED_TOOL_EVENTS:]


def compute_in_session_alerts(tools_used, tool_name, tool_path):
    """Compute quality alerts based on in-session tool usage.

    Called AFTER the current tool event has been appended to tools_used.
    Returns a list of alert strings (may be empty).
    """
    from core.quality_signals import (
        check_file_size,
        find_edits_without_read,
        find_file_thrashing,
    )

    alerts = []

    # Edit-without-read: flag if the current Edit targets a file not yet Read
    if tool_name == 'Edit' and tool_path:
        violations = find_edits_without_read(tools_used)
        norm_path = tool_path.replace('\\', '/').lower()
        for v in violations:
            if v.replace('\\', '/').lower() == norm_path:
                alerts.append(
                    f"[Quality] You edited {tool_path} without reading it first. "
                    "Read files before editing to avoid blind changes."
                )
                break

    # File thrashing: flag if this Read is the 3rd+ read of the same file
    if tool_name == 'Read' and tool_path:
        thrashing = find_file_thrashing(tools_used)
        norm_path = tool_path.replace('\\', '/').lower()
        count = thrashing.get(norm_path)
        if count:
            alerts.append(
                f"[Quality] You have read {tool_path} {count} times. "
                "This suggests lost context — consider summarizing key "
                "findings before re-reading."
            )

    # File size: tiered alerts (yellow at soft limit, red at hard limit).
    # Thresholds + ignore globs are configurable via calibration.md.
    if tool_path:
        from core.calibration import load_calibration

        thresholds = load_calibration().get('quality_thresholds', {})
        yellow = int(thresholds.get('file_size_yellow', 400))
        red = int(thresholds.get('file_size_red', 600))
        ignore_globs = thresholds.get('ignore_globs', []) or []

        if (
            not _is_ephemeral_quality_artifact(tool_path)
            and not _path_matches_any_glob(tool_path, ignore_globs)
        ):
            line_count = check_file_size(
                tool_path,
                threshold=yellow,
                max_lines=red,
            )
            if line_count:
                if line_count > red:
                    alerts.append(
                        f"[Quality RED] {tool_path} is over the {red}-line ceiling. "
                        "If you are changing this file, split it as part of the change; "
                        "if you are only reading it, no action."
                    )
                else:
                    alerts.append(
                        f"[Quality] {tool_path} exceeds the {yellow}-line threshold; "
                        "consider splitting into smaller modules if you are changing it."
                    )

    return alerts


def check_compact_nag(session_state) -> list:
    """Return a ``[Context]`` nag when ``tool_count`` outpaces the last compact.

    Compares delta since last compact against ``intervals.compact_nag``
    (default 20). Counter is reset by the PreCompact hook.
    """
    if session_state is None:
        return []

    from core.calibration import get_interval

    interval = get_interval('compact_nag', 20)
    if interval <= 0:
        return []

    tool_count = session_state.get('tool_count', 0)
    last_compacted = session_state.get('last_compacted_at_count', 0)
    delta = tool_count - last_compacted
    if delta < interval:
        return []

    return [
        f"[Context] {delta} tool calls since the last compact. "
        "Compact (/compact) at the next phase boundary if earlier tool output is no longer needed."
    ]


def _handle(input_data, timer):
    tool_name = input_data.get('tool_name', input_data.get('tool', 'unknown'))

    # OPT-22: heartbeat touch — fires on every PostToolUse during an
    # autonomous run, regardless of whether logging is disabled.
    _touch_heartbeat(tool_name)

    # issue #200 §3: record the exact tool-result DROP sentinel as a pending,
    # unresolved record so the Stop hook can fail loud if it is never recovered.
    # Best-effort and unconditional (a safety mechanism, not logging). Only
    # `tool_response`/`tool_error` is inspected — never `tool_input` — so a
    # sentinel appearing in a command does not create a record. The transcript
    # scan in stop.py is the authoritative gate.
    try:
        from core import drop_guard
        _sid = input_data.get('session_id')
        if _sid:
            _resp = input_data.get('tool_response', input_data.get('tool_error'))
            if drop_guard.is_drop_response(_resp):
                drop_guard.record_drop(_sid, tool_name)
    except Exception as exc:
        from core.hook_logger import log_swallowed
        log_swallowed('post_tool_drop_guard', exc)

    # Transient git/gh auth failure: nudge an IMMEDIATE retry rather than a
    # credential diagnosis / hand-off (the user's recurring friction). Always-on and
    # independent of the log_corrections toggle — a behavioral safety net, not
    # logging — so it sits above the is_safety_enabled gate, like drop recording.
    if tool_name in ('Bash', 'PowerShell'):
        try:
            from core.git_transient import transient_retry_alert, coerce_output
            _cmd = (input_data.get('tool_input') or {}).get('command', '') or ''
            # Avoid flattening a potentially huge tool response unless this is
            # actually a git/gh shell operation eligible for the alert.
            if re.search(r'\b(?:git|gh)\b', _cmd):
                _out = coerce_output(input_data.get('tool_response', input_data.get('tool_error')))
                _alert = transient_retry_alert(tool_name, _cmd, _out)
                if _alert:
                    timer.set_output_summary("transient-retry alert")
                    return {"systemMessage": _alert}
        except Exception as exc:
            from core.hook_logger import log_swallowed
            log_swallowed('post_tool_git_transient', exc)

    from core.calibration import is_safety_enabled
    from core.session_state import (
        StateUnavailable,
        get_session_state,
        resolve_session_id,
    )

    timer.set_input_summary(f"tool={tool_name}")

    # Extract file path for Read/Write/Edit (not Grep/Glob — those target directories)
    tool_input = input_data.get('tool_input', {})
    tool_path = None
    if tool_name in ('Read', 'Write', 'Edit'):
        tool_path = tool_input.get('file_path') or tool_input.get('path') or None

    if not is_safety_enabled('log_corrections'):
        timer.set_output_summary("logging disabled")
        return {}

    # Get or create session state, resolved the same way by every hook.
    session_state = get_session_state(resolve_session_id(input_data))

    # ONE lock acquisition for the whole invocation, with a snapshot taken inside
    # it to feed everything downstream. Each locked access carries its own
    # acquisition budget, so separate read/write/nag accesses could spend 4x that
    # budget under contention and blow this hook's 3s timeout. Snapshotting inside
    # the transaction also means the alerts describe exactly what was committed,
    # not a re-read a concurrent writer may have moved. A busy lock drops this
    # advisory telemetry rather than delaying the tool.
    snapshot = {}
    if session_state:
        try:
            with session_state.transaction() as state:
                state['tool_count'] = state.get('tool_count', 0) + 1
                tools_used = _append_tool_event(state.get('tools_used'), {
                    'tool': tool_name,
                    'path': tool_path,
                    'timestamp': datetime.now().isoformat(),
                })
                state['tools_used'] = tools_used
                pending = dict(state)
            # Publish only after the context manager committed. Assigning inside
            # the block would leave `snapshot` holding values that a failed write
            # never persisted, and the alerts below would describe state that does
            # not exist on disk.
            snapshot = pending
        except StateUnavailable:
            pass

    # Compute quality alerts AFTER tracking so the current event is included.
    # Both consumers read the snapshot dict -- no further lock acquisitions.
    alerts = compute_in_session_alerts(
        tools_used=snapshot.get('tools_used', []),
        tool_name=tool_name,
        tool_path=tool_path,
    )
    alerts.extend(check_compact_nag(snapshot if session_state else None))
    if alerts:
        timer.set_output_summary(f"alerts: {len(alerts)}")
        return {"systemMessage": "\n".join(alerts)}

    timer.set_output_summary("tool tracked")
    return {}


def main():
    from core.worker_guard import exit_if_worker
    exit_if_worker()  # OPT-23: no-op inside a headless worker subprocess
    from core.hook_runtime import run_hook
    # Advisory hook: fallback {} does not block tool use.
    run_hook("PostToolUse", _handle, error_name="post_tool_use", fallback={})


if __name__ == '__main__':
    main()

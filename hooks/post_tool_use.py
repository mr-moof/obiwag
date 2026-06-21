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
import sys
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


def _touch_heartbeat(tool_name: str) -> None:
    """Write a heartbeat JSON file for the watchdog to poll.

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
    try:
        import json as _json
        hb_path = os.path.join('.obi', 'state', f'heartbeat-{_RUN_ID}.json')
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

        if not _path_matches_any_glob(tool_path, ignore_globs):
            line_count = check_file_size(tool_path, threshold=yellow)
            if line_count:
                if line_count > red:
                    alerts.append(
                        f"[Quality RED] {tool_path} is {line_count} lines "
                        f"(hard limit: {red}). Non-Negotiable Rule #3 "
                        "requires splitting this file."
                    )
                else:
                    alerts.append(
                        f"[Quality] {tool_path} is {line_count} lines "
                        f"(threshold: {yellow}). Consider splitting into "
                        "smaller modules (Non-Negotiable Rule #3)."
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
        f"[Context] {delta} tool calls since last compact. "
        "Run /compact before continuing."
    ]


def _handle(input_data, timer):
    tool_name = input_data.get('tool_name', input_data.get('tool', 'unknown'))

    # OPT-22: heartbeat touch — fires on every PostToolUse during an
    # autonomous run, regardless of whether logging is disabled.
    _touch_heartbeat(tool_name)

    from core.calibration import is_safety_enabled
    from core.memory_reader import generate_session_id
    from core.session_state import get_session_state, read_current_session

    timer.set_input_summary(f"tool={tool_name}")

    # Extract file path for Read/Write/Edit (not Grep/Glob — those target directories)
    tool_input = input_data.get('tool_input', {})
    tool_path = None
    if tool_name in ('Read', 'Write', 'Edit'):
        tool_path = tool_input.get('file_path') or tool_input.get('path') or None

    if not is_safety_enabled('log_corrections'):
        timer.set_output_summary("logging disabled")
        return {}

    # Get or create session state. Try the session_id from input, or fall
    # back to a stable per-hour seed so PostToolUse calls within the same
    # session map to the same state file.
    session_id = input_data.get('session_id')
    if not session_id:
        # SessionStart sentinel (<24h) keeps a session that crosses an hour
        # boundary mapped to one state file instead of forking (OPT-04 #177).
        session_id = read_current_session()
    if not session_id:
        session_seed = datetime.now().strftime("%Y-%m-%d-%H")
        session_id = generate_session_id(session_seed)

    session_state = get_session_state(session_id)

    if session_state:
        session_state.increment('tool_count')
        session_state.append_to_list('tools_used', {
            'tool': tool_name,
            'path': tool_path,
            'timestamp': datetime.now().isoformat(),
        })

    # Compute quality alerts AFTER tracking so the current event is included.
    alerts = compute_in_session_alerts(
        tools_used=session_state.get('tools_used', []) if session_state else [],
        tool_name=tool_name,
        tool_path=tool_path,
    )
    alerts.extend(check_compact_nag(session_state))
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

#!/usr/bin/env python3
"""PreCompact hook — reset the WI-3 compact nag counter.

When ``/compact`` runs, Claude Code fires this hook before the compaction
takes effect. We snapshot ``tool_count`` into ``last_compacted_at_count`` so
``post_tool_use.check_compact_nag`` stops firing until enough new tool calls
accumulate past the next interval.

Advisory only — emits ``{}`` on success and on any failure so the compaction
is never blocked.
"""

import os
import sys
from datetime import datetime

HOOK_ROOT = os.path.dirname(os.path.abspath(__file__))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)


def _reset_compact_counter(session_state):
    """Copy current tool_count into last_compacted_at_count.

    Returns the value written, or None if there was no state or it could not be
    persisted. One transaction: reading, writing and reporting used to be three
    separate locked accesses, each paying its own acquisition budget, and the
    write's success was discarded so the hook could report a reset that never
    landed (issue #202).
    """
    if session_state is None:
        return None

    from core.session_state import StateUnavailable

    try:
        with session_state.transaction() as state:
            tool_count = state.get('tool_count', 0)
            state['last_compacted_at_count'] = tool_count
            return tool_count
    except StateUnavailable:
        return None


def _handle(input_data, timer):
    from core.session_state import get_session_state, resolve_session_id

    session_state = get_session_state(resolve_session_id(input_data))
    reset_to = _reset_compact_counter(session_state)
    if reset_to is None:
        timer.set_output_summary("no session state — nothing to reset")
    else:
        timer.set_output_summary(f"reset: last_compacted_at_count={reset_to}")
    return {}


def main():
    from core.worker_guard import exit_if_worker
    exit_if_worker()  # OPT-23: no-op inside a headless worker subprocess
    from core.hook_runtime import run_hook
    run_hook("PreCompact", _handle, error_name="pre_compact", fallback={})


if __name__ == '__main__':
    main()

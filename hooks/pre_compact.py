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


def _reset_compact_counter(session_state) -> bool:
    """Copy current tool_count into last_compacted_at_count. Returns True on write."""
    if session_state is None:
        return False
    tool_count = session_state.get('tool_count', 0)
    session_state.set('last_compacted_at_count', tool_count)
    return True


def _handle(input_data, timer):
    from core.memory_reader import generate_session_id
    from core.session_state import get_session_state, read_current_session

    session_id = input_data.get('session_id')
    if not session_id:
        # SessionStart sentinel (<24h) — see OPT-04 (#177).
        session_id = read_current_session()
    if not session_id:
        session_seed = datetime.now().strftime("%Y-%m-%d-%H")
        session_id = generate_session_id(session_seed)

    session_state = get_session_state(session_id)
    if _reset_compact_counter(session_state):
        timer.set_output_summary(
            f"reset: last_compacted_at_count={session_state.get('last_compacted_at_count')}"
        )
    else:
        timer.set_output_summary("no session state — nothing to reset")
    return {}


def main():
    from core.worker_guard import exit_if_worker
    exit_if_worker()  # OPT-23: no-op inside a headless worker subprocess
    from core.hook_runtime import run_hook
    run_hook("PreCompact", _handle, error_name="pre_compact", fallback={})


if __name__ == '__main__':
    main()

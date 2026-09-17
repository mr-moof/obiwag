"""OBI_WORKER hook-isolation guard (OPT-23 foundation).

When Obi dispatches a phase as a supervised headless ``claude -p`` worker, the worker's hooks
fire in its own process. Without isolation the state-mutating hooks write to the SAME run-scoped state as
the parent orchestrator — heartbeats (``.obi/state/heartbeat-<run_id>.json``), pending
learnings, session summaries — and collide.

``claude --bare`` skips hooks entirely, but on this workstation ``--bare`` also disables OAuth
(it forces ANTHROPIC_API_KEY / apiKeyHelper auth), so workers must run WITHOUT ``--bare`` and
self-suppress instead. The orchestrator sets ``OBI_WORKER=1`` in the worker's environment; the
state-touching hooks call :func:`exit_if_worker` at the top of ``main()`` and no-op.

NOT applied to ``pre_tool_use``: its policy gating must still apply to a worker's tool calls.
"""

import os
import sys

# Values that mean "not a worker" even when the var is present.
_FALSEY = ('', '0', 'false', 'no', 'off')


def is_worker() -> bool:
    """True only when ``OBI_WORKER`` is explicitly set to a truthy value.

    Deliberately conservative: an unset, empty, or falsey value means NORMAL session, so a
    normal interactive/orchestrator session is never accidentally suppressed.
    """
    return os.environ.get('OBI_WORKER', '').strip().lower() not in _FALSEY


def exit_if_worker(emit: bool = True) -> None:
    """No-op and ``sys.exit(0)`` when running inside an Obi headless worker subprocess.

    Call this as the first line of a state-touching hook's ``main()``. When ``emit`` is True
    (the default), an empty JSON object is printed first so Claude Code still receives the one
    JSON object it expects from the hook.
    """
    if is_worker():
        if emit:
            print('{}', file=sys.stdout)
        sys.exit(0)


__all__ = ['is_worker', 'exit_if_worker']

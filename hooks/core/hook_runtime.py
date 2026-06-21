"""Common runtime mechanics shared by all Obi hook entry points.

Each hook (pre_tool_use, post_tool_use, pre_compact, session_start, stop)
historically duplicated the same scaffolding:

  - parse JSON from stdin (with a non-fatal fallback on malformed input)
  - start a ``HookTimer`` for execution logging
  - emit JSON output
  - route unexpected exceptions through ``respond_with_error``
  - call ``sys.exit(0)`` so a hook crash never blocks Claude Code

This module centralizes the mechanics so each hook can focus on its
domain logic. It deliberately stays small — no domain knowledge about
permission decisions, learning detection, etc. lives here.
"""

import json
import sys
from typing import Any, Callable, Dict, Optional, TextIO

from core.hook_error_handler import respond_with_error
from core.hook_logger import HookTimer


HookHandler = Callable[[Dict[str, Any], HookTimer], Optional[Dict[str, Any]]]


def read_hook_input(stream: Optional[TextIO] = None) -> Dict[str, Any]:
    """Parse a JSON payload from a stream (defaults to ``sys.stdin``).

    Returns ``{}`` if the stream is empty, missing, or contains invalid
    JSON. This mirrors what every hook used to do inline — Claude Code may
    invoke a hook with no stdin payload (e.g. test runs), and that must
    not be fatal.
    """
    if stream is None:
        stream = sys.stdin
    try:
        return json.load(stream) or {}
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        return {}


def emit_hook_output(output: Optional[Dict[str, Any]]) -> None:
    """Print a hook output dict to stdout as a single JSON object.

    ``None`` is normalized to ``{}`` so callers can simply ``return None``
    when they have nothing to say. The output is emitted as compact JSON
    on a single line — Claude Code expects exactly one JSON object per
    invocation.
    """
    if output is None:
        output = {}
    print(json.dumps(output), file=sys.stdout)


def run_hook(
    hook_name: str,
    handler: HookHandler,
    *,
    error_name: str,
    fallback: Dict[str, Any],
) -> None:
    """Drive a single hook invocation: read input → handler → emit output.

    Args:
        hook_name: Display name passed to ``HookTimer`` (e.g. ``"Stop"``).
        handler: Callable receiving ``(input_data, timer)`` and returning
            the output dict to emit (or ``None`` for an empty ``{}``).
            The handler is responsible for calling ``timer.set_input_summary``
            / ``timer.set_output_summary`` for execution-log richness.
        error_name: Snake-case name passed to ``respond_with_error`` so log
            entries and systemMessage prefixes use the canonical hook name
            (e.g. ``"stop"``, ``"pre_tool_use"``).
        fallback: Output shape used when an unhandled exception escapes
            ``handler``. Pass a hook-appropriate dict (e.g. ``{}`` for
            advisory hooks, the full ``hookSpecificOutput`` skeleton for
            session_start so additionalContext can be appended cleanly).

    Always exits 0 — hooks must never block Claude Code by crashing.
    """
    with HookTimer(hook_name) as timer:
        try:
            input_data = read_hook_input()
            output = handler(input_data, timer)
            emit_hook_output(output)
        except Exception as exc:  # noqa: BLE001 - explicitly catch-all here
            timer.set_error(str(exc))
            # respond_with_error logs, prints the fallback, and calls
            # sys.exit(0) — control does not return from this call.
            respond_with_error(error_name, exc, fallback=fallback)

    sys.exit(0)


__all__ = [
    'HookHandler',
    'read_hook_input',
    'emit_hook_output',
    'run_hook',
]

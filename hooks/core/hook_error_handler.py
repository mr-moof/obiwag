"""Unified hook error handling for Obi Memory System.

Each hook (pre_tool_use, post_tool_use, session_start, stop) previously had
its own top-level `except Exception` block with divergent behavior — some
logged via systemMessage, others silently printed {}. This made failures
invisible to the user and untraceable on disk.

This module centralizes:
1. Structured error logging to ~/.claude/.obi/hook-errors.jsonl
2. A consistent systemMessage injected into the fallback response so the
   user sees that a hook failed, not just what the hook was going to say.

Each hook supplies its own fallback output dict (preserving its fail-open
vs fail-closed semantics) — this module does not decide whether to block
or allow. See issue #122.
"""

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.paths import get_obi_root


def _get_error_log_path() -> str:
    """Path to the shared hook-error log. Mirrors calibration.py convention."""
    return str(get_obi_root() / ".obi" / "hook-errors.jsonl")


def log_hook_error(
    hook_name: str,
    exc: BaseException,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a structured record of a hook exception to hook-errors.jsonl.

    Never raises — a logger that can itself fail defeats the purpose. If
    writing the log fails, the exception is swallowed silently.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hook": hook_name,
        "error_class": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }
    if context:
        record["context"] = context

    try:
        path = _get_error_log_path()
        from core.jsonl_helper import append_jsonl
        append_jsonl(path, record)
    except Exception:
        # Logger must never raise; hooks depend on it as a last-resort safety net.
        pass


def _ensure_system_message(output: Dict[str, Any], message: str) -> Dict[str, Any]:
    """Inject or append a systemMessage so hook failures are user-visible.

    If the output is empty ({}), add the systemMessage as the sole key.
    If it already has a systemMessage, append to it.
    Otherwise, add the systemMessage key alongside existing keys.
    """
    if not isinstance(output, dict):
        output = {}
    existing = output.get("systemMessage")
    if existing:
        output["systemMessage"] = f"{existing}\n{message}"
    else:
        output["systemMessage"] = message
    return output


def respond_with_error(
    hook_name: str,
    exc: BaseException,
    fallback: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    silent: bool = False,
) -> None:
    """Log, print fallback with a visible systemMessage, and exit 0.

    Call this from a hook's top-level `except Exception as e` block instead
    of a bespoke fallback. The hook keeps control of its fallback shape
    (e.g. pre_tool_use returning {} to defer to Claude's default permission
    checks) while gaining consistent logging and user-visible errors.

    Args:
        hook_name: Name of the hook, e.g. "pre_tool_use".
        exc: The caught exception instance.
        fallback: The response dict the hook would print (shape is hook-specific).
        context: Optional extra context to attach to the log record
            (e.g. tool_name, tool_input).
        silent: If True, suppresses the systemMessage injection. Use only
            for hooks that must not perturb the output at all (rare).

    Exits the process with code 0 — hooks should never block Claude Code
    by crashing.
    """
    log_hook_error(hook_name, exc, context=context)

    if not silent:
        message = f"[Obi Memory] {hook_name} failed: {type(exc).__name__}: {exc}"
        # session_start uses a nested hookSpecificOutput shape — if that's
        # what the caller passes in, append to additionalContext instead.
        hso = fallback.get("hookSpecificOutput") if isinstance(fallback, dict) else None
        if isinstance(hso, dict) and "additionalContext" in hso:
            existing = hso.get("additionalContext", "")
            hso["additionalContext"] = f"{existing}\n{message}" if existing else message
        else:
            fallback = _ensure_system_message(fallback, message)

    try:
        print(json.dumps(fallback), file=sys.stdout)
    except Exception:
        # Even JSON serialization can fail (e.g. unserializable context).
        # Fall back to an empty response rather than raising.
        print("{}", file=sys.stdout)

    sys.exit(0)

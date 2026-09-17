"""Tool-result-drop enforcement (issue #200 §3).

The operation-timeouts contract says the exact ``[Tool result missing due to internal error]``
sentinel must trigger IMMEDIATE recovery — never an idle hang and never a "that wasn't a hang"
reassurance as the terminal response. Empty and routine errors remain visible to the caller but do
not create drop state. Historically the sentinel contract lived only in prose; this enforces it:

* ``is_drop_response`` — structural check on a PostToolUse ``tool_response`` (or a
  PostToolUseFailure ``tool_error``). post_tool_use.py records a pending, unresolved
  drop when this fires.
* ``transcript_has_unresolved_drop`` — the AUTHORITATIVE gate. Reads the transcript
  JSONL and returns True iff the last tool-result batch containing a drop has no LATER
  batch containing a successful tool result (a real recovery action). Siblings in the
  same parallel batch do NOT count as recovery.
* ``evaluate_stop_block`` — stop.py calls this. On an unresolved drop it returns a
  ``{"decision":"block", ...}`` to force a recovery tool call, bounded by a persistent
  retry counter AND ``stop_hook_active`` so it can never loop forever; at the cap it
  fails loud with the 3-line operator-stall report and allows the stop.

Precision matters: a drop is detected only when the result IS (essentially) the sentinel
— NOT when a result merely CONTAINS the sentinel as data (operation-timeouts.md and
CLAUDE.md contain the literal sentinel string; Reading them must not read as a drop).
Only ``tool_response`` / ``tool_error`` and JSONL ``tool_result`` blocks are inspected —
never ``tool_input`` and never assistant prose — so a mention of the sentinel in a
command or a sentence does not create or clear a record.
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

SENTINEL = "[Tool result missing due to internal error]"

# Max Stop blocks before we stop blocking and fail loud instead (bounded re-entry).
_RETRY_CAP = 2


# ---------------------------------------------------------------------------
# State-file helpers (keyed by session, under the run's .obi/state/)
# ---------------------------------------------------------------------------

def _safe(session_id: Optional[str]) -> str:
    if not session_id:
        return "session"
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id))[:120]


def _state_dir(cwd: Optional[str]) -> str:
    return os.path.join(cwd or os.getcwd(), ".obi", "state")


def pending_drop_path(session_id: Optional[str], cwd: Optional[str] = None) -> str:
    return os.path.join(_state_dir(cwd), f"pending-drop-{_safe(session_id)}.json")


def retry_path(session_id: Optional[str], cwd: Optional[str] = None) -> str:
    return os.path.join(_state_dir(cwd), f"stop-drop-retries-{_safe(session_id)}.txt")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Drop detection
# ---------------------------------------------------------------------------

def _is_sentinel(text: str) -> bool:
    """True only when the ENTIRE result IS the harness drop sentinel.

    Exact match (after strip), NOT substring. A genuine drop replaces the whole tool
    result with the sentinel; a Grep/Read whose output merely CONTAINS the sentinel
    string (it lives verbatim in CLAUDE.md, docs/operation-timeouts.md, obi-auto.md,
    and others) must NOT read as a drop — otherwise reviewing/working on this very
    feature would false-trip the Stop block (Opus review #3). Precision over recall:
    the sentinel is the one unambiguous marker; a routine tool error (nonzero exit,
    "file not found") is NOT a drop (Opus review #2) — the agent sees it and handles
    it normally, so it must not create a pending-drop record.
    """
    if not text or len(text) > len(SENTINEL) + 32:
        return False
    return text.strip() == SENTINEL


def is_drop_response(tool_response: Any) -> bool:
    """Structural drop check for a PostToolUse ``tool_response`` / ``tool_error``.

    Detects ONLY the harness missing-result sentinel: as the whole string, or as a
    text/content/result field that IS the sentinel, or inside a list of content
    blocks. An absent field (None), a normal successful result, or a routine tool
    error is NOT a drop. The authoritative gate is the transcript scan in Stop.
    """
    if tool_response is None:
        return False
    if isinstance(tool_response, str):
        return _is_sentinel(tool_response)
    if isinstance(tool_response, dict):
        for key in ("text", "content", "result"):
            val = tool_response.get(key)
            if isinstance(val, str) and _is_sentinel(val):
                return True
            if isinstance(val, list) and is_drop_response(val):
                return True
        return False
    if isinstance(tool_response, list):
        return any(is_drop_response(b) for b in tool_response)
    return False


# ---------------------------------------------------------------------------
# Pending-drop record
# ---------------------------------------------------------------------------

def record_drop(session_id: Optional[str], tool_name: str, cwd: Optional[str] = None) -> None:
    """Write/refresh an unresolved pending-drop record. Best-effort (never raises)."""
    path = pending_drop_path(session_id, cwd)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"session_id": session_id, "tool": tool_name,
                 "detected_at": _now(), "resolved": False},
                f,
            )
    except OSError:
        pass


def read_drop(session_id: Optional[str], cwd: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        with open(pending_drop_path(session_id, cwd), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def clear_drop(session_id: Optional[str], cwd: Optional[str] = None) -> None:
    try:
        os.remove(pending_drop_path(session_id, cwd))
    except OSError:
        pass


def _read_retry(session_id: Optional[str], cwd: Optional[str] = None) -> int:
    try:
        with open(retry_path(session_id, cwd), "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip())
    except (OSError, ValueError):
        return 0


def _write_retry(session_id: Optional[str], count: int, cwd: Optional[str] = None) -> None:
    path = retry_path(session_id, cwd)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(count))
    except OSError:
        pass


def _clear_retry(session_id: Optional[str], cwd: Optional[str] = None) -> None:
    try:
        os.remove(retry_path(session_id, cwd))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Transcript scan (authoritative)
# ---------------------------------------------------------------------------

def _tool_result_text(tr: Dict[str, Any]) -> str:
    content = tr.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text", "") or b.get("content", "") or "")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return str(content)


def _tr_is_drop(tr: Dict[str, Any]) -> bool:
    # Sentinel-exact only (Opus review #3): a tool_result whose text merely CONTAINS
    # the sentinel (e.g. a grep match line while working on this feature) or an empty
    # errored result is NOT a drop; only the whole result BEING the sentinel is.
    return _is_sentinel(_tool_result_text(tr))


def _tr_is_success(tr: Dict[str, Any]) -> bool:
    if _tr_is_drop(tr):
        return False
    return not tr.get("is_error")


def _tool_result_batches(transcript_text: str) -> List[Tuple[int, List[Dict[str, Any]]]]:
    """Ordered (batch_index, [tool_result blocks]) for each user message that has any.

    One user message == one parallel batch of tool results. Only ``tool_result`` blocks
    are considered, so ``tool_use`` inputs and assistant/user prose are ignored.
    """
    batches: List[Tuple[int, List[Dict[str, Any]]]] = []
    idx = 0
    for raw in transcript_text.splitlines():
        raw = raw.strip()
        if not raw or raw[0] != "{":
            continue
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        role = msg.get("role") or obj.get("type")
        if role not in ("user", "human"):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        trs = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
        if trs:
            batches.append((idx, trs))
            idx += 1
    return batches


def transcript_has_unresolved_drop(transcript_text: str) -> bool:
    """True iff the last drop-bearing tool-result batch has no LATER successful batch.

    A recovery must be a subsequent tool call (a later batch) — a successful sibling in
    the SAME parallel batch as the drop does not clear it.
    """
    if not transcript_text:
        return False
    batches = _tool_result_batches(transcript_text)
    if not batches:
        return False

    last_drop_idx = None
    for idx, trs in batches:
        if any(_tr_is_drop(t) for t in trs):
            last_drop_idx = idx
    if last_drop_idx is None:
        return False

    for idx, trs in batches:
        if idx > last_drop_idx and any(_tr_is_success(t) for t in trs):
            return False
    return True


# ---------------------------------------------------------------------------
# Stop-hook decision
# ---------------------------------------------------------------------------

def _fail_loud_message(record: Optional[Dict[str, Any]]) -> str:
    tool = (record or {}).get("tool", "the last tool call")
    return (
        "Unresolved tool-result drop persisted after repeated recovery prompts. "
        "Failing loud (operator-stall report):\n"
        f"1. Dispatched: {tool} returned the missing-result sentinel.\n"
        "2. Verified state: no recovery tool call was detected in the transcript "
        "after the drop.\n"
        "3. Next action: verify the affected state now (Read / Grep / git status / "
        "Test-Path), then retry ONLY the one failed operation if it did not land, "
        "or surface NEEDS USER INPUT."
    )


def evaluate_stop_block(
    input_data: Dict[str, Any],
    transcript_text: str,
    session_id: Optional[str],
    cwd: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a Stop-hook output to block/fail-loud on an unresolved drop, else None.

    * unresolved drop + under cap  -> ``{"decision":"block","reason":...}`` (force recovery)
    * unresolved drop + at cap (or stop_hook_active re-entry) -> ``{"systemMessage":<3-line>}``
      and allow the stop (bounded fail-loud)
    * resolved / no drop -> clear state, return None (allow the stop)
    """
    unresolved = transcript_has_unresolved_drop(transcript_text)

    # Fallback when the transcript is unavailable: trust the pending record.
    record = read_drop(session_id, cwd)
    if not transcript_text and record is not None and not record.get("resolved", False):
        unresolved = True

    if not unresolved:
        clear_drop(session_id, cwd)
        _clear_retry(session_id, cwd)
        return None

    stop_hook_active = bool(input_data.get("stop_hook_active"))
    retries = _read_retry(session_id, cwd)

    if retries >= _RETRY_CAP or (stop_hook_active and retries >= 1):
        # Bounded fail-loud: allow the stop but surface the operator-stall report.
        _clear_retry(session_id, cwd)
        clear_drop(session_id, cwd)
        return {"systemMessage": _fail_loud_message(record)}

    _write_retry(session_id, retries + 1, cwd)
    return {
        "decision": "block",
        "reason": "Unresolved tool-result drop: verify state and recover now.",
    }


__all__ = [
    "SENTINEL",
    "is_drop_response",
    "record_drop",
    "read_drop",
    "clear_drop",
    "transcript_has_unresolved_drop",
    "evaluate_stop_block",
    "pending_drop_path",
    "retry_path",
]

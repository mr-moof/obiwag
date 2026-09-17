"""Tests for tool-result-drop enforcement (issue #200 §3, core/drop_guard.py)."""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core import drop_guard  # noqa: E402

SENTINEL = drop_guard.SENTINEL


# --- JSONL transcript fixture helpers --------------------------------------

def _user_tool_results(*results):
    """One user message (= one parallel batch) of tool_result blocks.

    Each result is (content, is_error).
    """
    content = [
        {"type": "tool_result", "tool_use_id": f"t{i}", "content": c, "is_error": e}
        for i, (c, e) in enumerate(results)
    ]
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}})


def _assistant_text(text):
    return json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


def _transcript(*lines):
    return "\n".join(lines)


# --- is_drop_response ------------------------------------------------------

def test_is_drop_response_sentinel_string():
    assert drop_guard.is_drop_response(SENTINEL) is True
    assert drop_guard.is_drop_response("  " + SENTINEL + "  ") is True


def test_is_drop_response_none_and_normal_are_not_drops():
    assert drop_guard.is_drop_response(None) is False
    assert drop_guard.is_drop_response("") is False
    assert drop_guard.is_drop_response("normal output") is False
    assert drop_guard.is_drop_response({"type": "text", "text": "ok"}) is False


def test_is_drop_response_does_not_flag_sentinel_as_substring_of_data():
    # Reading a doc that CONTAINS the sentinel string must NOT read as a drop.
    doc = (
        "## Tool-result drops\n\n"
        f"A `{SENTINEL}` result still leaves you in control, so react immediately. "
        "Never idle, never re-issue blindly. Verify state with one cheap read."
    )
    assert drop_guard.is_drop_response(doc) is False


def test_is_drop_response_routine_tool_errors_are_NOT_drops():
    # Precision (Opus review #2): a routine tool error is NOT a drop — the agent sees
    # it and handles it. ONLY the exact harness sentinel is a drop.
    assert drop_guard.is_drop_response({"type": "error", "error": "boom"}) is False
    assert drop_guard.is_drop_response({"is_error": True, "text": "file not found"}) is False
    assert drop_guard.is_drop_response({"error": "nonzero exit"}) is False
    # ...but the sentinel inside a text/content field IS a drop.
    assert drop_guard.is_drop_response({"type": "text", "text": SENTINEL}) is True


def test_is_drop_response_short_substring_is_not_a_drop():
    # Opus review #3: a short result that merely CONTAINS the sentinel (e.g. a grep
    # match line while working on this feature) must not read as a drop.
    assert drop_guard.is_drop_response(
        "operation-timeouts.md:28: " + SENTINEL + " ...") is False
    assert drop_guard.is_drop_response(SENTINEL + " is the marker") is False


def test_is_drop_response_list_of_blocks():
    assert drop_guard.is_drop_response([{"type": "text", "text": SENTINEL}]) is True
    assert drop_guard.is_drop_response([{"type": "text", "text": "fine"}]) is False


# --- transcript_has_unresolved_drop ----------------------------------------

def test_no_drop_is_resolved():
    t = _transcript(
        _user_tool_results(("hello", False)),
        _assistant_text("done"),
    )
    assert drop_guard.transcript_has_unresolved_drop(t) is False


def test_empty_transcript_is_resolved():
    assert drop_guard.transcript_has_unresolved_drop("") is False


def test_drop_then_reassurance_only_is_unresolved():
    # Acceptance (§6): a "this is not a hang" reassurance with NO recovery tool call
    # leaves the drop unresolved -> Stop must block.
    t = _transcript(
        _user_tool_results((SENTINEL, False)),
        _assistant_text("That was a dropped result, not a hang. Not stuck."),
    )
    assert drop_guard.transcript_has_unresolved_drop(t) is True


def test_drop_then_later_success_is_resolved():
    t = _transcript(
        _user_tool_results((SENTINEL, False)),
        _assistant_text("verifying state"),
        _user_tool_results(("git status: clean", False)),
    )
    assert drop_guard.transcript_has_unresolved_drop(t) is False


def test_parallel_batch_success_sibling_does_not_clear_drop():
    # Acceptance (§6): a success in the SAME batch as the drop must not clear it.
    t = _transcript(
        _assistant_text("dispatching two calls"),
        _user_tool_results(("ok result", False), (SENTINEL, False)),
    )
    assert drop_guard.transcript_has_unresolved_drop(t) is True


def test_read_of_doc_containing_sentinel_is_not_a_drop():
    # A tool_result whose content merely CONTAINS the sentinel among real output.
    long_doc = "line1\n" + SENTINEL + "\n" + ("x" * 500)
    t = _transcript(_user_tool_results((long_doc, False)))
    assert drop_guard.transcript_has_unresolved_drop(t) is False


def test_empty_errored_result_is_not_a_drop():
    # Precision (Opus review #3): an empty errored result is ambiguous and NOT flagged;
    # only the exact sentinel is. Avoids false blocks on routine tool errors.
    t = _transcript(_user_tool_results(("", True)))
    assert drop_guard.transcript_has_unresolved_drop(t) is False


def test_empty_successful_result_is_not_a_drop():
    t = _transcript(_user_tool_results(("", False)))
    assert drop_guard.transcript_has_unresolved_drop(t) is False


def test_short_match_line_containing_sentinel_is_not_a_drop():
    # A grep/read match line that CONTAINS the sentinel among other text is not a drop.
    t = _transcript(_user_tool_results(("CLAUDE.md:42: " + SENTINEL + " leaves you in control", False)))
    assert drop_guard.transcript_has_unresolved_drop(t) is False


# --- record/read/clear round-trip ------------------------------------------

def test_record_read_clear_roundtrip(tmp_path):
    sid = "sess-123"
    assert drop_guard.read_drop(sid, cwd=str(tmp_path)) is None
    drop_guard.record_drop(sid, "Bash", cwd=str(tmp_path))
    rec = drop_guard.read_drop(sid, cwd=str(tmp_path))
    assert rec and rec["tool"] == "Bash" and rec["resolved"] is False
    drop_guard.clear_drop(sid, cwd=str(tmp_path))
    assert drop_guard.read_drop(sid, cwd=str(tmp_path)) is None


# --- evaluate_stop_block ---------------------------------------------------

def _drop_transcript():
    return _transcript(
        _user_tool_results((SENTINEL, False)),
        _assistant_text("not a hang"),
    )


def test_stop_block_first_time_returns_block(tmp_path):
    out = drop_guard.evaluate_stop_block(
        input_data={},
        transcript_text=_drop_transcript(),
        session_id="s1",
        cwd=str(tmp_path),
    )
    assert out == {
        "decision": "block",
        "reason": "Unresolved tool-result drop: verify state and recover now.",
    }


def test_stop_block_is_bounded_then_fails_loud(tmp_path):
    kw = dict(input_data={}, transcript_text=_drop_transcript(),
              session_id="s2", cwd=str(tmp_path))
    first = drop_guard.evaluate_stop_block(**kw)
    assert first.get("decision") == "block"
    second = drop_guard.evaluate_stop_block(**kw)
    assert second.get("decision") == "block"
    # Third time: cap reached -> fail loud (allow stop), no decision:block.
    third = drop_guard.evaluate_stop_block(**kw)
    assert "decision" not in third
    assert "systemMessage" in third
    msg = third["systemMessage"]
    assert "Dispatched" in msg and "Verified state" in msg and "Next action" in msg


def test_stop_hook_active_fails_loud_sooner(tmp_path):
    kw = dict(transcript_text=_drop_transcript(), session_id="s3", cwd=str(tmp_path))
    first = drop_guard.evaluate_stop_block(input_data={}, **kw)
    assert first.get("decision") == "block"
    # stop_hook_active re-entry after one block -> fail loud immediately.
    second = drop_guard.evaluate_stop_block(input_data={"stop_hook_active": True}, **kw)
    assert "systemMessage" in second and "decision" not in second


def test_stop_allows_when_recovery_present(tmp_path):
    resolved = _transcript(
        _user_tool_results((SENTINEL, False)),
        _assistant_text("verifying"),
        _user_tool_results(("clean", False)),
    )
    out = drop_guard.evaluate_stop_block(
        input_data={}, transcript_text=resolved, session_id="s4", cwd=str(tmp_path),
    )
    assert out is None


def test_stop_allows_and_clears_state_when_no_drop(tmp_path):
    sid = "s5"
    drop_guard.record_drop(sid, "Bash", cwd=str(tmp_path))
    drop_guard._write_retry(sid, 1, cwd=str(tmp_path))
    out = drop_guard.evaluate_stop_block(
        input_data={}, transcript_text=_transcript(_user_tool_results(("ok", False))),
        session_id=sid, cwd=str(tmp_path),
    )
    assert out is None
    assert drop_guard.read_drop(sid, cwd=str(tmp_path)) is None
    assert drop_guard._read_retry(sid, cwd=str(tmp_path)) == 0


def test_transcript_unavailable_falls_back_to_pending_record(tmp_path):
    sid = "s6"
    drop_guard.record_drop(sid, "PowerShell", cwd=str(tmp_path))
    out = drop_guard.evaluate_stop_block(
        input_data={}, transcript_text="", session_id=sid, cwd=str(tmp_path),
    )
    assert out and out.get("decision") == "block"


# --- end-to-end: the real Stop hook subprocess blocks a reassurance-only drop ---
# Only the BLOCK path is exercised end-to-end: it exits before any session-summary
# pipeline writes, so the test stays contained to the tmp cwd (no real state touched).

def _last_json_line(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def _hook_env(tmp_path):
    env = os.environ.copy()
    obi_root = tmp_path / "obi-home"
    calibration = obi_root / ".obi" / "calibration.md"
    calibration.parent.mkdir(parents=True, exist_ok=True)
    calibration.write_text(
        "---\nsafety:\n  log_corrections: false\n  generate_summaries: false\n---\n",
        encoding="utf-8",
    )
    env["OBI_ROOT"] = str(obi_root)
    return env, obi_root


def _run_hook(script, payload, tmp_path, env):
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps(payload), capture_output=True, text=True,
        cwd=str(tmp_path), env=env, timeout=60,
    )


def test_e2e_stop_hook_blocks_reassurance_only_drop(tmp_path):
    inp = {
        "session_id": "e2e-block",
        "transcript": _drop_transcript(),
        "stop_hook_active": False,
    }
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "stop.py")],
        input=json.dumps(inp), capture_output=True, text=True,
        cwd=str(tmp_path), timeout=60,
    )
    data = _last_json_line(proc.stdout)
    assert data is not None, f"no JSON on stdout: {proc.stdout!r} / {proc.stderr!r}"
    assert data.get("decision") == "block"
    assert "verify state and recover" in data.get("reason", "")


def test_e2e_post_failure_stop_block_recovery_and_stop_allow(tmp_path):
    env, _ = _hook_env(tmp_path)
    sid = "e2e-chain"

    failed = _run_hook("post_tool_use.py", {
        "hook_event_name": "PostToolUseFailure",
        "session_id": sid,
        "tool_name": "Agent",
        "tool_error": SENTINEL,
    }, tmp_path, env)
    assert failed.returncode == 0, failed.stderr
    assert drop_guard.read_drop(sid, cwd=str(tmp_path)) is not None

    blocked = _run_hook("stop.py", {
        "session_id": sid,
        "transcript": _drop_transcript(),
        "stop_hook_active": False,
    }, tmp_path, env)
    assert _last_json_line(blocked.stdout).get("decision") == "block"

    recovered = _run_hook("post_tool_use.py", {
        "hook_event_name": "PostToolUse",
        "session_id": sid,
        "tool_name": "Read",
        "tool_response": "verified state",
    }, tmp_path, env)
    assert recovered.returncode == 0, recovered.stderr

    resolved = _transcript(
        _user_tool_results((SENTINEL, True)),
        _assistant_text("verifying state"),
        _user_tool_results(("verified state", False)),
    )
    allowed = _run_hook("stop.py", {
        "session_id": sid,
        "transcript": resolved,
        "stop_hook_active": False,
    }, tmp_path, env)
    assert _last_json_line(allowed.stdout) == {}
    assert drop_guard.read_drop(sid, cwd=str(tmp_path)) is None
    assert drop_guard._read_retry(sid, cwd=str(tmp_path)) == 0


def test_e2e_stop_guard_fault_logs_and_fails_loud_without_blocking(tmp_path):
    env, obi_root = _hook_env(tmp_path)
    proc = _run_hook("stop.py", {
        "session_id": "e2e-guard-fault",
        "transcript": ["invalid transcript shape"],
    }, tmp_path, env)

    data = _last_json_line(proc.stdout)
    assert proc.returncode == 0, proc.stderr
    assert "decision" not in data
    assert "recovery guard failed" in data.get("systemMessage", "")
    swallowed = obi_root / ".obi" / "swallowed-errors.jsonl"
    rows = [json.loads(line) for line in swallowed.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["component"] == "stop_drop_guard"

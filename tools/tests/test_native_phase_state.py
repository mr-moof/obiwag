"""Deterministic Codex-native phase timeline regressions for issue #203."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.native_phase_state import (
    TimelineError,
    classify_initial_progress,
    load_codex_policy,
    new_timeline,
    record_host_failure,
    record_interrupt,
    record_progress,
    record_resume,
    record_synthesis,
    record_terminal,
    validate_timeline,
)


BASE = datetime(2026, 8, 12, 20, 48, 52, tzinfo=timezone.utc)


@pytest.fixture()
def policy() -> dict[str, object]:
    return {
        "synthesis_due_sec": 270,
        "grace_sec": 60,
        "recent_progress_sec": 60,
        "wait_window_max_sec": 60,
        "resume_window_count": 2,
        "resume_window_sec": 60,
        "terminal_states": ["completed", "native_completion_stalled"],
        "initial_classifications": [
            "completed",
            "productive_budget_exhausted",
            "interrupted_after_grace",
        ],
        "synthesis_instruction": (
            "No more tools. Return the phase artifact now from current evidence; "
            "list missing sources instead of researching further."
        ),
    }


def test_steer_at_195_seconds_is_rejected(policy: dict[str, object]) -> None:
    state = new_timeline("run", 1, "thread", policy, BASE)
    with pytest.raises(TimelineError, match="before synthesis_due_at_utc"):
        record_synthesis(state, BASE + timedelta(seconds=195))
    assert state["synthesis_sent_at_utc"] is None


def test_progress_nine_seconds_ago_prevents_initial_stalled_classification(
    policy: dict[str, object],
) -> None:
    state = new_timeline("run", 1, "thread", policy, BASE)
    record_synthesis(state, BASE + timedelta(seconds=270))
    interrupt_at = BASE + timedelta(seconds=330)
    record_progress(state, "tool", interrupt_at - timedelta(seconds=9))
    record_interrupt(state, policy, interrupt_at)

    assert classify_initial_progress(state, policy, interrupt_at) == "productive_budget_exhausted"
    assert state["initial_classification"] == "productive_budget_exhausted"
    errors = validate_timeline(state, policy)
    assert "tool calls after the synthesis steer are forbidden" in errors
    assert not any("initial classification" in error for error in errors)


def test_worker_obeys_steer_and_returns(policy: dict[str, object]) -> None:
    state = new_timeline("run", 1, "thread", policy, BASE)
    record_synthesis(state, BASE + timedelta(seconds=270))
    record_progress(state, "message", BASE + timedelta(seconds=280))
    record_terminal(state, "completed", policy, BASE + timedelta(seconds=290))

    assert validate_timeline(state, policy) == []
    assert state["tool_calls_after_synthesis"] == 0
    assert state["terminal_state"] == "completed"


def test_resumed_no_progress_full_bound_is_native_completion_stalled(
    policy: dict[str, object],
) -> None:
    state = new_timeline("run", 1, "thread", policy, BASE)
    record_progress(state, "message", BASE + timedelta(seconds=250))
    record_synthesis(state, BASE + timedelta(seconds=270))
    record_interrupt(state, policy, BASE + timedelta(seconds=330))
    record_resume(state, "thread", BASE + timedelta(seconds=334))
    record_terminal(state, "native_completion_stalled", policy, BASE + timedelta(seconds=454))

    assert validate_timeline(state, policy) == []
    assert state["same_thread_resumed_once"] is True
    assert state["replacement_dispatched"] is False


def test_resumed_message_does_not_reclassify_initial_interrupt(
    policy: dict[str, object],
) -> None:
    state = new_timeline("run", 2, "thread", policy, BASE)
    record_synthesis(state, BASE + timedelta(seconds=270))
    record_interrupt(state, policy, BASE + timedelta(seconds=330))
    record_resume(state, "thread", BASE + timedelta(seconds=334))
    record_progress(state, "message", BASE + timedelta(seconds=340))
    record_terminal(state, "completed", policy, BASE + timedelta(seconds=341))

    assert state["initial_classification"] == "interrupted_after_grace"
    assert state["resumed_progress_count"] == 1
    assert validate_timeline(state, policy) == []


def test_windows_1312_is_host_failure_not_worker_terminal(
    policy: dict[str, object],
) -> None:
    state = new_timeline("run", 1, "thread", policy, BASE)
    record_host_failure(state, 1312, BASE + timedelta(seconds=1))

    assert validate_timeline(state, policy) == []
    assert state["host_status"] == "host_pre_execution_failure"
    assert state["host_error_code"] == 1312
    assert state["terminal_state"] is None


def test_repository_policy_carries_discovery_limits() -> None:
    policy = load_codex_policy(1)
    assert policy["research"] == {
        "targeted_local_call_limit": 12,
        "official_source_batch_call_limit": 4,
        "source_open_stop_sec": 180,
    }
    assert policy["recent_progress_sec"] == 60
    assert load_codex_policy(3)["synthesis_due_sec"] == 270


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "terminal_states",
            ["completed", "native_completion_stalled", "timed_out"],
            "terminal states must preserve native semantics",
        ),
        (
            "initial_classifications",
            ["completed", "interrupted_after_grace"],
            "initial classifications must preserve native semantics",
        ),
        ("wait_window_max_sec", 61, "cannot exceed 60"),
    ],
)
def test_native_policy_rejects_semantic_drift(
    policy: dict[str, object], field: str, value: object, message: str
) -> None:
    changed = dict(policy)
    changed[field] = value
    with pytest.raises(TimelineError, match=message):
        new_timeline("run", 1, "thread", changed, BASE)

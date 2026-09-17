"""Autonomous continuation decision and append-only ledger regressions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.autonomous_recovery import RecoveryError, read_ledger, record_recovery
from tools.native_phase_state import (
    load_codex_policy,
    new_timeline,
    record_interrupt,
    record_progress,
    record_resume,
    record_synthesis,
    record_terminal,
)


BASE = datetime(2026, 8, 26, 16, 0, 0, tzinfo=timezone.utc)


def write_native_stall(path: Path) -> None:
    policy = load_codex_policy(1)
    state = new_timeline("run-native", 1, "discovery-thread", policy, BASE)
    record_progress(state, "message", BASE + timedelta(seconds=250))
    record_synthesis(state, BASE + timedelta(seconds=270))
    record_interrupt(state, policy, BASE + timedelta(seconds=330))
    record_resume(state, "discovery-thread", BASE + timedelta(seconds=334))
    resumed_bound = policy["resume_window_count"] * policy["resume_window_sec"]
    record_terminal(
        state,
        "native_completion_stalled",
        policy,
        BASE + timedelta(seconds=334 + resumed_bound),
    )
    path.write_text(json.dumps(state), encoding="utf-8")


def test_native_stall_appends_takeover_then_continues_without_prompt(tmp_path: Path) -> None:
    native_state = tmp_path / "native-phase-run-native-1.json"
    ledger = tmp_path / "status-updates-run-native.jsonl"
    write_native_stall(native_state)

    _, decision = record_recovery(
        event="native_completion_stalled",
        run_id="run-native",
        phase=1,
        provider="codex-native",
        native_state_path=native_state,
        state_path=ledger,
        at=BASE + timedelta(seconds=500),
    )

    assert decision["next_actions"] == [
        "status-append",
        "primary-takeover",
        "continue-dispatch",
    ]
    assert decision["disposition"] == "continue"
    assert decision["provenance"] == "primary-takeover"
    assert decision["asks_continuation_question"] is False
    assert "execution continues" in decision["progress_update"]
    assert read_ledger(ledger, "run-native") == [decision]


@pytest.mark.parametrize(
    ("event", "expected_action", "expected_next"),
    [
        ("peer_unavailable", "local-review", "local-review"),
        ("prior_run_safe_resumable", "resume-existing-run", "resume-existing-run"),
        (
            "prior_run_terminal_or_corrupt",
            "archive-and-start-fresh",
            "manifest-archive",
        ),
        (
            "reversible_default_selected",
            "select-conservative-default",
            "select-default",
        ),
        ("phase_blocker_fixable", "repair-and-rerun-phase", "primary-takeover"),
        ("check_failure_fixable", "repair-and-rerun-check", "repair-check"),
    ],
)
def test_recoverable_events_continue_without_a_question(
    tmp_path: Path,
    event: str,
    expected_action: str,
    expected_next: str,
) -> None:
    ledger = tmp_path / f"status-updates-{event}.jsonl"
    _, decision = record_recovery(
        event=event,
        run_id=event,
        phase="review" if event == "peer_unavailable" else 2,
        provider="orchestrator",
        evidence=f"verified evidence for {event}",
        state_path=ledger,
        at=BASE,
    )

    assert decision["recovery_action"] == expected_action
    assert decision["disposition"] == "continue"
    assert decision["next_actions"][0] == "status-append"
    assert expected_next in decision["next_actions"]
    assert decision["next_actions"][-1] == "continue-dispatch"
    assert decision["asks_continuation_question"] is False
    assert not decision["progress_update"].endswith("?")


@pytest.mark.parametrize("event", ["hard_stop", "user_abort"])
def test_true_terminal_events_are_recorded_and_halt(tmp_path: Path, event: str) -> None:
    ledger = tmp_path / f"status-updates-{event}.jsonl"
    _, decision = record_recovery(
        event=event,
        run_id=event,
        phase=4,
        provider="orchestrator",
        evidence="verified non-bypassable boundary",
        state_path=ledger,
        at=BASE,
    )

    assert decision["disposition"] == "terminal"
    assert decision["next_actions"] == ["status-append", "report-terminal", "halt"]
    assert decision["asks_continuation_question"] is False


def test_append_preserves_prior_bytes_and_uses_contiguous_sequence(tmp_path: Path) -> None:
    ledger = tmp_path / "status-updates-run-sequence.jsonl"
    record_recovery(
        event="peer_unavailable",
        run_id="run-sequence",
        phase=4,
        provider="claude",
        evidence="provider unavailable",
        state_path=ledger,
        at=BASE,
    )
    prior = ledger.read_bytes()
    record_recovery(
        event="check_failure_fixable",
        run_id="run-sequence",
        phase=9,
        provider="orchestrator",
        evidence="focused test identified a local defect",
        state_path=ledger,
        at=BASE + timedelta(seconds=1),
    )

    current = ledger.read_bytes()
    assert current.startswith(prior)
    assert [record["sequence"] for record in read_ledger(ledger, "run-sequence")] == [1, 2]


def test_malformed_ledger_fails_closed_without_append(tmp_path: Path) -> None:
    ledger = tmp_path / "status-updates-malformed.jsonl"
    ledger.write_text("{not-json}\n", encoding="utf-8")
    prior = ledger.read_bytes()

    with pytest.raises(RecoveryError, match="malformed"):
        record_recovery(
            event="peer_unavailable",
            run_id="malformed",
            phase=4,
            provider="claude",
            evidence="provider unavailable",
            state_path=ledger,
            at=BASE,
        )

    assert ledger.read_bytes() == prior


def test_mixed_run_ledger_fails_closed_without_append(tmp_path: Path) -> None:
    ledger = tmp_path / "status-updates-run-b.jsonl"
    _, run_a = record_recovery(
        event="peer_unavailable",
        run_id="run-a",
        phase=4,
        provider="claude",
        evidence="provider unavailable",
        state_path=ledger,
        at=BASE,
    )
    prior = ledger.read_bytes()
    assert run_a["run_id"] == "run-a"

    with pytest.raises(RecoveryError, match="mixed run_id"):
        record_recovery(
            event="phase_blocker_fixable",
            run_id="run-b",
            phase=2,
            provider="orchestrator",
            evidence="fixable blocker",
            state_path=ledger,
            at=BASE + timedelta(seconds=1),
        )

    assert ledger.read_bytes() == prior


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("recovery_action", "continue-anyway", "recovery_action does not match"),
        ("disposition", "terminal", "disposition does not match"),
        ("next_actions", ["status-append", "halt"], "next_actions does not match"),
    ],
)
def test_semantically_altered_ledger_fails_closed_without_append(
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    ledger = tmp_path / "status-updates-tampered.jsonl"
    _, decision = record_recovery(
        event="peer_unavailable",
        run_id="tampered",
        phase=4,
        provider="claude",
        evidence="provider unavailable",
        state_path=ledger,
        at=BASE,
    )
    decision[field] = value
    ledger.write_text(json.dumps(decision, separators=(",", ":")) + "\n", encoding="utf-8")
    prior = ledger.read_bytes()

    with pytest.raises(RecoveryError, match=error):
        record_recovery(
            event="check_failure_fixable",
            run_id="tampered",
            phase=9,
            provider="orchestrator",
            evidence="local test failure",
            state_path=ledger,
            at=BASE + timedelta(seconds=1),
        )

    assert ledger.read_bytes() == prior


def test_native_stall_rejects_an_unvalidated_terminal(tmp_path: Path) -> None:
    native_state = tmp_path / "native-phase-run-native-1.json"
    ledger = tmp_path / "status-updates-run-native.jsonl"
    write_native_stall(native_state)
    state = json.loads(native_state.read_text(encoding="utf-8"))
    state["same_thread_resumed_once"] = False
    native_state.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(RecoveryError, match="native phase state is invalid"):
        record_recovery(
            event="native_completion_stalled",
            run_id="run-native",
            phase=1,
            provider="codex-native",
            native_state_path=native_state,
            state_path=ledger,
            at=BASE + timedelta(seconds=500),
        )

    assert not ledger.exists()

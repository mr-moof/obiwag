#!/usr/bin/env python3
"""Record bounded autonomous recovery decisions in an append-only run ledger."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .native_phase_state import load_codex_policy, validate_timeline
else:
    from native_phase_state import load_codex_policy, validate_timeline


SCHEMA_VERSION = 1
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
PROVENANCE_VALUES = {"delegated", "inline-fallback", "primary-takeover"}
DISPOSITIONS = {"continue", "terminal"}

RECOVERY_SPECS: dict[str, dict[str, Any]] = {
    "native_completion_stalled": {
        "recovery_action": "primary-takeover",
        "provenance": "primary-takeover",
        "disposition": "continue",
        "next_actions": ["status-append", "primary-takeover", "continue-dispatch"],
        "progress": (
            "Phase {phase} worker stalled after bounded recovery; primary takeover "
            "recorded and execution continues."
        ),
    },
    "peer_unavailable": {
        "recovery_action": "local-review",
        "provenance": "inline-fallback",
        "disposition": "continue",
        "next_actions": ["status-append", "local-review", "continue-dispatch"],
        "progress": (
            "Peer review unavailable; no content was sent, local review recorded, "
            "and execution continues."
        ),
    },
    "prior_run_safe_resumable": {
        "recovery_action": "resume-existing-run",
        "provenance": "inline-fallback",
        "disposition": "continue",
        "next_actions": ["status-append", "resume-existing-run", "continue-dispatch"],
        "progress": (
            "Prior run state is safe to resume; same-run recovery recorded and "
            "execution continues."
        ),
    },
    "prior_run_terminal_or_corrupt": {
        "recovery_action": "archive-and-start-fresh",
        "provenance": "inline-fallback",
        "disposition": "continue",
        "next_actions": [
            "status-append",
            "manifest-archive",
            "start-fresh",
            "continue-dispatch",
        ],
        "progress": (
            "Prior run state is terminal or safely archivable; manifest-backed "
            "fresh-start recovery recorded and execution continues."
        ),
    },
    "reversible_default_selected": {
        "recovery_action": "select-conservative-default",
        "provenance": "inline-fallback",
        "disposition": "continue",
        "next_actions": ["status-append", "select-default", "continue-dispatch"],
        "progress": (
            "Phase {phase} selected a conservative reversible default; assumption recorded "
            "and execution continues."
        ),
    },
    "phase_blocker_fixable": {
        "recovery_action": "repair-and-rerun-phase",
        "provenance": "primary-takeover",
        "disposition": "continue",
        "next_actions": ["status-append", "primary-takeover", "rerun-phase", "continue-dispatch"],
        "progress": (
            "Phase {phase} blocker is fixable in scope; primary repair recorded and "
            "execution continues."
        ),
    },
    "check_failure_fixable": {
        "recovery_action": "repair-and-rerun-check",
        "provenance": "primary-takeover",
        "disposition": "continue",
        "next_actions": ["status-append", "repair-check", "rerun-check", "continue-dispatch"],
        "progress": (
            "Phase {phase} check failed on a fixable in-scope defect; repair and "
            "verification rerun recorded."
        ),
    },
    "user_abort": {
        "recovery_action": "halt-user-abort",
        "provenance": "delegated",
        "disposition": "terminal",
        "next_actions": ["status-append", "report-terminal", "halt"],
        "progress": "Phase {phase} received an explicit user abort; terminal state recorded.",
    },
    "hard_stop": {
        "recovery_action": "halt-hard-stop",
        "provenance": "delegated",
        "disposition": "terminal",
        "next_actions": ["status-append", "report-terminal", "halt"],
        "progress": (
            "Phase {phase} reached a non-bypassable hard stop; terminal evidence recorded."
        ),
    },
}


class RecoveryError(ValueError):
    """A recovery decision or ledger violates the autonomous contract."""


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise RecoveryError("run_id must be a filename-safe identifier of at most 80 characters")


def _validate_record(record: Any, expected_run_id: str, expected_sequence: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise RecoveryError("ledger line must be a JSON object")
    required = {
        "schema_version",
        "sequence",
        "timestamp_utc",
        "run_id",
        "phase",
        "provider",
        "trigger",
        "evidence",
        "recovery_action",
        "provenance",
        "disposition",
        "next_actions",
        "remaining_constraints",
        "uncertainty",
        "asks_continuation_question",
        "progress_update",
    }
    missing = sorted(required - record.keys())
    if missing:
        raise RecoveryError(f"ledger line is missing: {', '.join(missing)}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise RecoveryError("ledger schema_version is unsupported")
    if not isinstance(record["sequence"], int) or isinstance(record["sequence"], bool):
        raise RecoveryError("ledger sequence must be an integer")
    if record["run_id"] != expected_run_id:
        raise RecoveryError("ledger contains mixed run_id values")
    if record["sequence"] != expected_sequence:
        raise RecoveryError("ledger sequence is not contiguous")
    if not isinstance(record["timestamp_utc"], str) or not record["timestamp_utc"].endswith("Z"):
        raise RecoveryError("ledger timestamp_utc must be an ISO-8601 UTC value")
    try:
        datetime.fromisoformat(record["timestamp_utc"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise RecoveryError("ledger timestamp_utc must be an ISO-8601 UTC value") from exc
    phase = record["phase"]
    valid_phase = (
        isinstance(phase, int)
        and not isinstance(phase, bool)
        and phase >= 0
        or isinstance(phase, str)
        and bool(phase.strip())
    )
    if not valid_phase:
        raise RecoveryError("ledger phase must be a non-negative integer or non-empty string")
    if not isinstance(record["provider"], str) or not record["provider"].strip():
        raise RecoveryError("ledger provider must be a non-empty string")
    if not isinstance(record["evidence"], dict) or not record["evidence"]:
        raise RecoveryError("ledger evidence must be a non-empty object")
    trigger = record["trigger"]
    if not isinstance(trigger, str) or trigger not in RECOVERY_SPECS:
        raise RecoveryError("ledger trigger is unsupported")
    spec = RECOVERY_SPECS[trigger]
    for field in ("recovery_action", "provenance", "disposition", "next_actions"):
        expected = spec[field]
        if record[field] != expected:
            raise RecoveryError(f"ledger {field} does not match trigger {trigger}")
    if record["provenance"] not in PROVENANCE_VALUES:
        raise RecoveryError("ledger provenance is invalid")
    if record["disposition"] not in DISPOSITIONS:
        raise RecoveryError("ledger disposition is invalid")
    if record["asks_continuation_question"] is not False:
        raise RecoveryError("autonomous recovery must not ask a continuation question")
    if not isinstance(record["next_actions"], list) or not record["next_actions"]:
        raise RecoveryError("ledger next_actions must be a non-empty list")
    if record["next_actions"][0] != "status-append":
        raise RecoveryError("status-append must be the first recovery action")
    if not isinstance(record["remaining_constraints"], list) or not all(
        isinstance(value, str) for value in record["remaining_constraints"]
    ):
        raise RecoveryError("ledger remaining_constraints must be a string list")
    if not isinstance(record["uncertainty"], str):
        raise RecoveryError("ledger uncertainty must be a string")
    expected_progress = str(spec["progress"]).format(phase=phase)
    if record["progress_update"] != expected_progress:
        raise RecoveryError(f"ledger progress_update does not match trigger {trigger}")
    return record


def read_ledger(path: Path, run_id: str) -> list[dict[str, Any]]:
    """Replay and validate an existing run ledger without changing it."""
    _validate_run_id(run_id)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise RecoveryError("ledger is truncated because its final line has no newline")
    records: list[dict[str, Any]] = []
    for sequence, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            raise RecoveryError("ledger contains an empty line")
        try:
            parsed = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RecoveryError(f"ledger line {sequence} is malformed") from exc
        records.append(_validate_record(parsed, run_id, sequence))
    return records


def _native_stall_evidence(
    state_path: Path,
    phase: int,
    phase_table_path: Path | None,
) -> dict[str, Any]:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RecoveryError(f"native phase state not found: {state_path}") from exc
    except json.JSONDecodeError as exc:
        raise RecoveryError("native phase state is invalid JSON") from exc
    if not isinstance(state, dict):
        raise RecoveryError("native phase state must be a JSON object")
    if state.get("phase") != phase:
        raise RecoveryError("native phase state does not match the requested phase")
    policy = load_codex_policy(phase, phase_table_path)
    errors = validate_timeline(state, policy)
    if errors:
        raise RecoveryError(f"native phase state is invalid: {'; '.join(errors)}")
    if state.get("terminal_state") != "native_completion_stalled":
        raise RecoveryError("native recovery requires terminal_state native_completion_stalled")
    if not state.get("same_thread_resumed_once") or state.get("replacement_dispatched"):
        raise RecoveryError("native recovery requires one same-thread resume and no replacement")
    return {
        "state_path": str(state_path),
        "terminal_state": state["terminal_state"],
        "terminal_at_utc": state["terminal_at_utc"],
        "thread_id": state["thread_id"],
        "same_thread_resumed_once": state["same_thread_resumed_once"],
        "resumed_progress_count": state["resumed_progress_count"],
        "replacement_dispatched": state["replacement_dispatched"],
    }


def build_record(
    *,
    event: str,
    run_id: str,
    phase: int | str,
    provider: str,
    evidence: str | None,
    native_state_path: Path | None,
    phase_table_path: Path | None,
    sequence: int,
    remaining_constraints: list[str] | None = None,
    uncertainty: str = "",
    at: datetime | None = None,
) -> dict[str, Any]:
    """Resolve one observed event to its bounded recovery decision."""
    _validate_run_id(run_id)
    if event not in RECOVERY_SPECS:
        raise RecoveryError(f"unsupported autonomous recovery event: {event}")
    spec = RECOVERY_SPECS[event]
    if event == "native_completion_stalled":
        if not isinstance(phase, int) or native_state_path is None:
            raise RecoveryError("native_completion_stalled requires an integer phase and state path")
        exact_evidence: dict[str, Any] = _native_stall_evidence(
            native_state_path, phase, phase_table_path
        )
    else:
        if not evidence or not evidence.strip():
            raise RecoveryError(f"{event} requires exact decision evidence")
        exact_evidence = {"summary": evidence.strip()}
    progress = str(spec["progress"]).format(phase=phase)
    if progress.rstrip().endswith("?"):
        raise RecoveryError("autonomous progress updates must be informational, not questions")
    return {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "timestamp_utc": iso_utc(at or datetime.now(timezone.utc)),
        "run_id": run_id,
        "phase": phase,
        "provider": provider,
        "trigger": event,
        "evidence": exact_evidence,
        "recovery_action": spec["recovery_action"],
        "provenance": spec["provenance"],
        "disposition": spec["disposition"],
        "next_actions": list(spec["next_actions"]),
        "remaining_constraints": list(remaining_constraints or []),
        "uncertainty": uncertainty,
        "asks_continuation_question": False,
        "progress_update": progress,
    }


def append_record(path: Path, record: dict[str, Any]) -> None:
    """Append exactly one durable UTF-8 JSON line; never rewrite prior records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RecoveryError("ledger append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def record_recovery(
    *,
    event: str,
    run_id: str,
    phase: int | str,
    provider: str,
    evidence: str | None = None,
    native_state_path: Path | None = None,
    phase_table_path: Path | None = None,
    state_path: Path | None = None,
    remaining_constraints: list[str] | None = None,
    uncertainty: str = "",
    at: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Validate prior state, append one decision, and return its durable path."""
    ledger_path = state_path or Path.cwd() / ".obi" / "state" / f"status-updates-{run_id}.jsonl"
    records = read_ledger(ledger_path, run_id)
    record = build_record(
        event=event,
        run_id=run_id,
        phase=phase,
        provider=provider,
        evidence=evidence,
        native_state_path=native_state_path,
        phase_table_path=phase_table_path,
        sequence=len(records) + 1,
        remaining_constraints=remaining_constraints,
        uncertainty=uncertainty,
        at=at,
    )
    _validate_record(record, run_id, len(records) + 1)
    append_record(ledger_path, record)
    replayed = read_ledger(ledger_path, run_id)
    if replayed[-1] != record:
        raise RecoveryError("ledger read-back does not match the appended recovery decision")
    return ledger_path, record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event", choices=sorted(RECOVERY_SPECS))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--evidence")
    parser.add_argument("--native-state", type=Path)
    parser.add_argument("--phase-table", type=Path)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--remaining-constraint", action="append", default=[])
    parser.add_argument("--uncertainty", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    phase: int | str = int(args.phase) if args.phase.isdigit() else args.phase
    try:
        path, record = record_recovery(
            event=args.event,
            run_id=args.run_id,
            phase=phase,
            provider=args.provider,
            evidence=args.evidence,
            native_state_path=args.native_state,
            phase_table_path=args.phase_table,
            state_path=args.state_path,
            remaining_constraints=args.remaining_constraint,
            uncertainty=args.uncertainty,
        )
        print(
            json.dumps(
                {
                    "recorded": True,
                    "state_path": str(path),
                    "disposition": record["disposition"],
                    "next_actions": record["next_actions"],
                    "progress_update": record["progress_update"],
                },
                separators=(",", ":"),
            )
        )
        return 0
    except (OSError, RecoveryError) as exc:
        print(json.dumps({"recorded": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

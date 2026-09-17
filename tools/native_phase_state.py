#!/usr/bin/env python3
"""Atomic Codex-native delegated-phase timeline state (issue #203)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TIMESTAMP_FIELDS = (
    "started_at_utc",
    "synthesis_due_at_utc",
    "synthesis_sent_at_utc",
    "last_progress_at_utc",
    "first_interrupt_at_utc",
    "resume_started_at_utc",
    "terminal_at_utc",
)
NATIVE_TERMINAL_STATES = ["completed", "native_completion_stalled"]
NATIVE_INITIAL_CLASSIFICATIONS = [
    "completed",
    "productive_budget_exhausted",
    "interrupted_after_grace",
]
CANONICAL_SYNTHESIS_INSTRUCTION = (
    "No more tools. Return the phase artifact now from current evidence; "
    "list missing sources instead of researching further."
)


class TimelineError(ValueError):
    """A transition would violate the native-phase state machine."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TimelineError(f"{field} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise TimelineError(f"{field} is not a valid timestamp") from exc


def default_phase_table_path() -> Path:
    return Path(__file__).resolve().parents[1] / "phases" / "phase-table.json"


def validate_codex_policy(policy: Any, phase: int) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise TimelineError(
            f"phase {phase} has no Codex phase policy or delegation_policy_defaults fallback"
        )
    required = {
        "synthesis_due_sec",
        "grace_sec",
        "recent_progress_sec",
        "wait_window_max_sec",
        "resume_window_count",
        "resume_window_sec",
        "terminal_states",
        "initial_classifications",
        "synthesis_instruction",
    }
    missing = sorted(required - policy.keys())
    if missing:
        raise TimelineError(f"phase {phase} Codex policy is missing: {', '.join(missing)}")
    for field in required - {"terminal_states", "initial_classifications", "synthesis_instruction"}:
        if not isinstance(policy[field], int) or policy[field] < 1:
            raise TimelineError(f"phase {phase} Codex policy {field} must be a positive integer")
    if policy["wait_window_max_sec"] > 60:
        raise TimelineError(f"phase {phase} Codex wait_window_max_sec cannot exceed 60")
    if policy["terminal_states"] != NATIVE_TERMINAL_STATES:
        raise TimelineError(f"phase {phase} Codex terminal states must preserve native semantics")
    if policy["initial_classifications"] != NATIVE_INITIAL_CLASSIFICATIONS:
        raise TimelineError(
            f"phase {phase} Codex initial classifications must preserve native semantics"
        )
    if policy["synthesis_instruction"] != CANONICAL_SYNTHESIS_INSTRUCTION:
        raise TimelineError(f"phase {phase} Codex synthesis instruction is not canonical")
    return policy


def load_codex_policy(phase: int, phase_table_path: Path | None = None) -> dict[str, Any]:
    path = phase_table_path or default_phase_table_path()
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TimelineError(f"phase policy source not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TimelineError(f"phase policy source is invalid JSON: {exc}") from exc
    row = next((item for item in table.get("phases", []) if item.get("n") == phase), None)
    if row is None:
        raise TimelineError(f"phase {phase} is not defined in phase-table.json")
    policy = row.get("delegation_policy", {}).get("codex") or table.get(
        "delegation_policy_defaults", {}
    ).get("codex")
    return validate_codex_policy(policy, phase)


def new_timeline(
    run_id: str,
    phase: int,
    thread_id: str,
    policy: dict[str, Any],
    at: datetime | None = None,
) -> dict[str, Any]:
    validate_codex_policy(policy, phase)
    started = (at or utc_now()).astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "provider": "codex-native",
        "run_id": run_id,
        "phase": phase,
        "thread_id": thread_id,
        "started_at_utc": iso_utc(started),
        "synthesis_due_at_utc": iso_utc(
            started + timedelta(seconds=policy["synthesis_due_sec"])
        ),
        "synthesis_sent_at_utc": None,
        "last_progress_at_utc": None,
        "last_progress_kind": None,
        "first_interrupt_at_utc": None,
        "resume_started_at_utc": None,
        "terminal_at_utc": None,
        "initial_classification": None,
        "terminal_state": None,
        "host_status": None,
        "host_error_code": None,
        "message_progress_count": 0,
        "tool_progress_count": 0,
        "tool_calls_after_synthesis": 0,
        "resumed_progress_count": 0,
        "same_thread_resumed_once": False,
        "replacement_dispatched": False,
        "synthesis_instruction": policy["synthesis_instruction"],
        "policy_snapshot": policy,
    }


def _require_open(state: dict[str, Any]) -> None:
    if state.get("terminal_at_utc") is not None:
        raise TimelineError("native phase timeline is already terminal")


def _require_not_before(state: dict[str, Any], at: datetime, field: str) -> None:
    started = parse_utc(state.get("started_at_utc"), "started_at_utc")
    if started is None or at < started:
        raise TimelineError(f"{field} cannot precede started_at_utc")


def record_progress(state: dict[str, Any], kind: str, at: datetime | None = None) -> None:
    _require_open(state)
    observed = (at or utc_now()).astimezone(timezone.utc)
    _require_not_before(state, observed, "progress")
    if kind not in {"message", "tool"}:
        raise TimelineError("progress kind must be message or tool")
    state["last_progress_at_utc"] = iso_utc(observed)
    state["last_progress_kind"] = kind
    state[f"{kind}_progress_count"] = int(state.get(f"{kind}_progress_count", 0)) + 1
    synthesis = parse_utc(state.get("synthesis_sent_at_utc"), "synthesis_sent_at_utc")
    if kind == "tool" and synthesis is not None and observed >= synthesis:
        state["tool_calls_after_synthesis"] = int(
            state.get("tool_calls_after_synthesis", 0)
        ) + 1
    resume = parse_utc(state.get("resume_started_at_utc"), "resume_started_at_utc")
    if resume is not None and observed >= resume:
        state["resumed_progress_count"] = int(state.get("resumed_progress_count", 0)) + 1


def record_synthesis(state: dict[str, Any], at: datetime | None = None) -> None:
    _require_open(state)
    observed = (at or utc_now()).astimezone(timezone.utc)
    due = parse_utc(state.get("synthesis_due_at_utc"), "synthesis_due_at_utc")
    if due is None or observed < due:
        raise TimelineError("synthesis steer cannot be sent before synthesis_due_at_utc")
    if state.get("synthesis_sent_at_utc") is not None:
        raise TimelineError("synthesis steer was already sent")
    state["synthesis_sent_at_utc"] = iso_utc(observed)


def classify_initial_progress(
    state: dict[str, Any], policy: dict[str, Any], interrupt_at: datetime
) -> str:
    last = parse_utc(state.get("last_progress_at_utc"), "last_progress_at_utc")
    if last is not None and last <= interrupt_at and interrupt_at - last <= timedelta(
        seconds=policy["recent_progress_sec"]
    ):
        return "productive_budget_exhausted"
    return "interrupted_after_grace"


def record_interrupt(
    state: dict[str, Any], policy: dict[str, Any], at: datetime | None = None
) -> None:
    _require_open(state)
    observed = (at or utc_now()).astimezone(timezone.utc)
    synthesis = parse_utc(state.get("synthesis_sent_at_utc"), "synthesis_sent_at_utc")
    if synthesis is None:
        raise TimelineError("initial interrupt requires the recorded synthesis steer")
    if observed < synthesis + timedelta(seconds=policy["grace_sec"]):
        raise TimelineError("initial interrupt cannot precede the full synthesis grace window")
    if state.get("first_interrupt_at_utc") is not None:
        raise TimelineError("initial thread was already interrupted")
    state["first_interrupt_at_utc"] = iso_utc(observed)
    state["initial_classification"] = classify_initial_progress(state, policy, observed)


def record_resume(
    state: dict[str, Any], thread_id: str, at: datetime | None = None
) -> None:
    _require_open(state)
    observed = (at or utc_now()).astimezone(timezone.utc)
    interrupted = parse_utc(state.get("first_interrupt_at_utc"), "first_interrupt_at_utc")
    if interrupted is None or observed < interrupted:
        raise TimelineError("resume requires a completed initial interrupt")
    if thread_id != state.get("thread_id"):
        raise TimelineError("resume must use the same native thread id")
    if state.get("same_thread_resumed_once") or state.get("resume_started_at_utc") is not None:
        raise TimelineError("native thread can be resumed exactly once")
    state["resume_started_at_utc"] = iso_utc(observed)
    state["same_thread_resumed_once"] = True


def record_terminal(
    state: dict[str, Any], terminal_state: str, policy: dict[str, Any], at: datetime | None = None
) -> None:
    _require_open(state)
    observed = (at or utc_now()).astimezone(timezone.utc)
    _require_not_before(state, observed, "terminal event")
    if terminal_state not in policy["terminal_states"]:
        raise TimelineError(f"unsupported Codex-native terminal state: {terminal_state}")
    if terminal_state == "native_completion_stalled":
        resumed = parse_utc(state.get("resume_started_at_utc"), "resume_started_at_utc")
        if resumed is None or not state.get("same_thread_resumed_once"):
            raise TimelineError("native_completion_stalled requires the one same-thread resume")
        resume_bound = policy["resume_window_count"] * policy["resume_window_sec"]
        if observed < resumed + timedelta(seconds=resume_bound):
            raise TimelineError("native_completion_stalled requires the full resumed bound")
        if int(state.get("resumed_progress_count", 0)) != 0:
            raise TimelineError("a resumed thread with observable progress is not stalled")
    if state.get("initial_classification") is None:
        state["initial_classification"] = "completed"
    state["terminal_state"] = terminal_state
    state["terminal_at_utc"] = iso_utc(observed)


def record_host_failure(
    state: dict[str, Any], error_code: int, at: datetime | None = None
) -> None:
    _require_open(state)
    observed = (at or utc_now()).astimezone(timezone.utc)
    _require_not_before(state, observed, "host failure")
    if any(
        state.get(field)
        for field in ("synthesis_sent_at_utc", "first_interrupt_at_utc", "resume_started_at_utc")
    ):
        raise TimelineError("host pre-execution failure cannot be a worker lifecycle event")
    state["initial_classification"] = "host_pre_execution_failure"
    state["host_status"] = "host_pre_execution_failure"
    state["host_error_code"] = error_code
    state["terminal_state"] = None
    state["terminal_at_utc"] = iso_utc(observed)


def validate_timeline(state: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        validate_codex_policy(policy, int(state.get("phase", -1)))
    except (TypeError, ValueError, TimelineError) as exc:
        return [str(exc)]
    required = {
        "schema_version",
        "provider",
        "run_id",
        "phase",
        "thread_id",
        *TIMESTAMP_FIELDS,
        "initial_classification",
        "terminal_state",
        "host_status",
        "host_error_code",
        "tool_calls_after_synthesis",
        "resumed_progress_count",
        "same_thread_resumed_once",
        "replacement_dispatched",
    }
    missing = sorted(required - state.keys())
    if missing:
        return [f"timeline is missing: {', '.join(missing)}"]
    if state.get("provider") != "codex-native":
        errors.append("provider must be codex-native")
    parsed: dict[str, datetime | None] = {}
    for field in TIMESTAMP_FIELDS:
        try:
            parsed[field] = parse_utc(state.get(field), field)
        except TimelineError as exc:
            errors.append(str(exc))
            parsed[field] = None
    started = parsed["started_at_utc"]
    due = parsed["synthesis_due_at_utc"]
    synthesis = parsed["synthesis_sent_at_utc"]
    last = parsed["last_progress_at_utc"]
    interrupted = parsed["first_interrupt_at_utc"]
    resumed = parsed["resume_started_at_utc"]
    terminal = parsed["terminal_at_utc"]
    if started and due and due != started + timedelta(seconds=policy["synthesis_due_sec"]):
        errors.append("synthesis_due_at_utc does not match the configured phase budget")
    if synthesis and due and synthesis < due:
        errors.append("synthesis steer was sent before synthesis_due_at_utc")
    if int(state.get("tool_calls_after_synthesis", 0)) != 0:
        errors.append("tool calls after the synthesis steer are forbidden")
    if interrupted:
        if synthesis is None:
            errors.append("first interrupt requires a synthesis steer")
        elif interrupted < synthesis + timedelta(seconds=policy["grace_sec"]):
            errors.append("first interrupt precedes the full synthesis grace window")
        expected = classify_initial_progress(state, policy, interrupted)
        if state.get("initial_classification") != expected:
            errors.append(f"initial classification must be {expected}")
    if resumed:
        if interrupted is None or resumed < interrupted:
            errors.append("resume must follow the first interrupt")
        if not state.get("same_thread_resumed_once"):
            errors.append("resume must record same_thread_resumed_once")
    elif state.get("same_thread_resumed_once"):
        errors.append("same_thread_resumed_once requires resume_started_at_utc")
    if state.get("replacement_dispatched"):
        errors.append("replacement native dispatch is forbidden")
    host_code = state.get("host_error_code")
    if host_code is not None:
        if state.get("host_status") != "host_pre_execution_failure":
            errors.append("host error requires host_pre_execution_failure status")
        if state.get("terminal_state") is not None:
            errors.append("host pre-execution failure is not a worker terminal state")
        if synthesis or interrupted or resumed:
            errors.append("host pre-execution failure cannot contain worker lifecycle timestamps")
    elif state.get("host_status") is not None:
        errors.append("host_status requires host_error_code")
    terminal_state = state.get("terminal_state")
    if terminal is not None and terminal_state is None and host_code is None:
        errors.append("terminal_at_utc requires a worker terminal state or host failure")
    if terminal_state is not None and terminal is None:
        errors.append("worker terminal state requires terminal_at_utc")
    if terminal_state is not None and terminal_state not in policy["terminal_states"]:
        errors.append(f"unsupported Codex-native terminal state: {terminal_state}")
    initial = state.get("initial_classification")
    if (
        host_code is None
        and initial is not None
        and initial not in policy["initial_classifications"]
    ):
        errors.append(f"unsupported initial classification: {initial}")
    if terminal_state == "native_completion_stalled":
        if resumed is None or not state.get("same_thread_resumed_once"):
            errors.append("native_completion_stalled requires the one same-thread resume")
        elif terminal and terminal < resumed + timedelta(
            seconds=policy["resume_window_count"] * policy["resume_window_sec"]
        ):
            errors.append("native_completion_stalled precedes the full resumed bound")
        if int(state.get("resumed_progress_count", 0)) != 0:
            errors.append("native_completion_stalled cannot follow resumed progress")
    ordered = [started, synthesis, interrupted, resumed, terminal]
    prior: datetime | None = None
    for value in ordered:
        if value is None:
            continue
        if prior is not None and value < prior:
            errors.append("native lifecycle timestamps are not monotonic")
            break
        prior = value
    if last and started and last < started:
        errors.append("last_progress_at_utc precedes started_at_utc")
    return errors


def atomic_write(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TimelineError(f"native phase state not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise TimelineError(f"native phase state is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise TimelineError("native phase state must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("start", "progress", "synthesis", "interrupt", "resume", "complete", "stall", "host-failure", "validate"),
    )
    parser.add_argument("--run-id")
    parser.add_argument("--phase", type=int)
    parser.add_argument("--thread-id")
    parser.add_argument("--kind", choices=("message", "tool"))
    parser.add_argument("--error-code", type=int)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--phase-table", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "start":
            if not args.run_id or args.phase is None or not args.thread_id:
                raise TimelineError("start requires --run-id, --phase, and --thread-id")
            policy = load_codex_policy(args.phase, args.phase_table)
            state = new_timeline(args.run_id, args.phase, args.thread_id, policy)
            state_path = args.state_path or Path.cwd() / ".obi" / "state" / f"native-phase-{args.run_id}-{args.phase}.json"
        else:
            if args.state_path is None:
                raise TimelineError(f"{args.operation} requires --state-path")
            state_path = args.state_path
            state = read_state(state_path)
            policy = load_codex_policy(int(state["phase"]), args.phase_table)
            if args.operation == "progress":
                if not args.kind:
                    raise TimelineError("progress requires --kind")
                record_progress(state, args.kind)
            elif args.operation == "synthesis":
                record_synthesis(state)
            elif args.operation == "interrupt":
                record_interrupt(state, policy)
            elif args.operation == "resume":
                if not args.thread_id:
                    raise TimelineError("resume requires --thread-id")
                record_resume(state, args.thread_id)
            elif args.operation == "complete":
                record_terminal(state, "completed", policy)
            elif args.operation == "stall":
                record_terminal(state, "native_completion_stalled", policy)
            elif args.operation == "host-failure":
                if args.error_code is None:
                    raise TimelineError("host-failure requires --error-code")
                record_host_failure(state, args.error_code)
            elif args.operation == "validate":
                pass
        if args.operation != "validate":
            atomic_write(state_path, state)
        errors = validate_timeline(state, policy)
        print(json.dumps({"valid": not errors, "state_path": str(state_path), "errors": errors}))
        return 1 if errors else 0
    except (KeyError, TimelineError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

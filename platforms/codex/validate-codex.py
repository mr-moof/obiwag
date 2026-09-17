#!/usr/bin/env python3
"""Codex platform configuration health check.

Validates that the Obi Wag Codex target is structurally complete and avoids
Claude-only assumptions in the Codex runtime contract.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODEX_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.native_phase_state import (  # noqa: E402
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
from tools.efficiency import EfficiencyError, validate_policy  # noqa: E402

REQUIRED_SIGNALS = [
    "DISCOVERY COMPLETE",
    "AUTHOR COMPLETE",
    "SIMPLIFY COMPLETE",
    "REVIEW COMPLETE: PASS",
    "INTEGRATE COMPLETE",
    "RE-REVIEW COMPLETE",
    "README COMPLETE",
    "README REVIEW COMPLETE",
    "RELEASE GATE PASSED",
    "LEARNING CAPTURED",
]


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"[OK] {message}")

    def fail(self, message: str) -> None:
        print(f"[FAIL] {message}")
        self.failures.append(message)

    def warn(self, message: str) -> None:
        print(f"[WARN] {message}")
        self.warnings.append(message)


def check_file(check: Check, path: Path, label: str) -> str:
    if not path.is_file():
        check.fail(f"{label} missing: {path}")
        return ""
    check.ok(f"{label}: {path.name}")
    return path.read_text(encoding="utf-8", errors="ignore")


def check_agents_md(check: Check) -> None:
    source_path = CODEX_DIR / "AGENTS.md"
    content = check_file(check, source_path, "Codex AGENTS.md")
    if not content:
        return
    deployed_path = ROOT / "AGENTS.md"
    if not deployed_path.is_file():
        check.fail(f"generated root AGENTS.md missing: {deployed_path}")
    elif deployed_path.read_bytes() == source_path.read_bytes():
        check.ok("root AGENTS.md is byte-identical to platforms/codex/AGENTS.md")
    else:
        check.fail("root AGENTS.md is stale; run tools/deploy.ps1 to restore source parity")

    for marker in [
        "10-phase lifecycle",
        "zero-hallucination",
        "OBI_PLATFORM=codex",
        "slashless prompt aliases",
        "Windows error 1312",
        "native_completion_stalled",
        "inspect the native thread status",
        "synthesis steer",
        "interrupt that thread",
        "same thread exactly once",
        "do not dispatch a replacement",
        "bounded windows of at most 60 seconds",
        "fork_turns=\"none\"",
        "native_phase_state.py",
        "started_at_utc",
        "productive_budget_exhausted",
        "zero additional tool calls",
        "standing authorization for the primary",
        "autonomous_recovery.py",
        "status-updates-<run-id>.jsonl",
        "peer_unavailable",
        "Never ask merely to authorize",
        "No more tools. Return the phase artifact now",
        "standing_approved",
        "approval_required",
        "ApprovalScopeSha256",
        "accepted:false",
        "OBI_TRUSTED_ROOT",
        "never dump",
        "small excerpt of at most 100 lines",
        "codex --profile obi",
        "tools/efficiency.py route",
        "docs/agent-efficiency.md",
        "LEARNING SKIPPED",
        "gpt-5.6-terra",
        "task-base-<run_id>.txt",
        "archive-run-state.ps1",
        "prior_run_terminal_or_corrupt",
        "Special Signals",
        "policies/rigor-max-gates.md",
        ".obi/reports/<run_id>-plan.md",
        "classify-lane.ps1",
        "At most 3 serialized native threads",
    ]:
        if marker.lower() in content.lower():
            check.ok(f"AGENTS.md contains {marker}")
        else:
            check.fail(f"AGENTS.md missing {marker}")

    for signal in REQUIRED_SIGNALS:
        if signal in content:
            check.ok(f"AGENTS.md contains signal {signal}")
        else:
            check.fail(f"AGENTS.md missing signal {signal}")

    forbidden = ["~/.claude/commands", "~/.github/agents"]
    for marker in forbidden:
        if marker in content:
            check.fail(f"AGENTS.md leaks platform-specific path {marker}")


def check_obi_profile(check: Check) -> None:
    content = check_file(check, CODEX_DIR / "obi.config.toml", "Codex Obi profile")
    if not content:
        return

    lines = {line.strip() for line in content.splitlines()}
    for setting in [
        'model = "gpt-5.6-terra"',
        'model_reasoning_effort = "medium"',
        'default_subagent_model = "gpt-5.6-terra"',
        'default_subagent_reasoning_effort = "medium"',
    ]:
        if setting in lines:
            check.ok(f"obi.config.toml contains {setting}")
        else:
            check.fail(f"obi.config.toml missing {setting}")


def check_hooks_json(check: Check) -> None:
    hooks_path = CODEX_DIR / "hooks.json"
    raw = check_file(check, hooks_path, "Codex hooks.json")
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        check.fail(f"hooks.json invalid JSON: {exc}")
        return

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        check.fail("hooks.json missing top-level hooks object")
        return

    for hook_name in ["SessionStart", "PreToolUse", "PostToolUse", "Stop"]:
        entries = hooks.get(hook_name)
        if isinstance(entries, list) and entries:
            check.ok(f"hooks.json defines {hook_name}")
        else:
            check.fail(f"hooks.json missing {hook_name}")

    raw_lower = raw.lower()
    for marker in ["obi_platform=codex", "hook_wrapper_codex.cmd", "if not defined obi_home"]:
        if marker in raw_lower:
            check.ok(f"hooks.json contains {marker}")
        else:
            check.fail(f"hooks.json missing {marker}")

    if "!obi_home!" in raw_lower or "%obi_home%" in raw_lower:
        check.ok("hooks.json invokes OBI_HOME")
    else:
        check.fail("hooks.json does not invoke OBI_HOME")


def check_repo_support(check: Check) -> None:
    for path in [
        ROOT / "hooks" / "hook_wrapper_codex.cmd",
        ROOT / "hooks" / "core" / "paths.py",
        ROOT / "phases" / "README.md",
        ROOT / "policies" / "zero-hallucination.md",
        ROOT / "phases" / "phase-table.json",
        ROOT / "tools" / "native_phase_state.py",
        ROOT / "tools" / "autonomous_recovery.py",
        ROOT / "tools" / "efficiency.py",
        ROOT / "tools" / "efficiency_bundle.py",
        ROOT / "docs" / "agent-efficiency.md",
        ROOT / "tools" / "schemas" / "efficiency-handoff.schema.json",
    ]:
        if path.exists():
            check.ok(f"support file present: {path.relative_to(ROOT)}")
        else:
            check.fail(f"support file missing: {path.relative_to(ROOT)}")

    paths_py = (ROOT / "hooks" / "core" / "paths.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    if "OBI_PLATFORM" in paths_py and "'.codex'" in paths_py:
        check.ok("paths.py supports Codex root")
    else:
        check.fail("paths.py does not support Codex root")


def check_native_phase_policy(check: Check) -> None:
    table_path = ROOT / "phases" / "phase-table.json"
    try:
        table = json.loads(table_path.read_text(encoding="utf-8"))
        validate_policy(table)
        profile = (CODEX_DIR / "obi.config.toml").read_text(encoding="utf-8")
        routine = table["efficiency"]["routing"]["tiers"]["routine"]["codex"]
        if f'model = "{routine["model"]}"' not in profile or f'model_reasoning_effort = "{routine["effort"]}"' not in profile:
            raise EfficiencyError("Codex profile disagrees with canonical routine route")
        learning = next(phase for phase in table["phases"] if phase["n"] == 10)
        if not any(signal["value"] == "LEARNING SKIPPED:" for signal in learning["special_signals"]):
            raise EfficiencyError("phase 10 lacks conditional Learning signal")
        check.ok("efficiency policy and Codex routine profile agree")
    except (EfficiencyError, OSError, ValueError, KeyError) as exc:
        check.fail(f"efficiency policy: {exc}")
    policies: dict[int, dict[str, object]] = {}
    for phase in (1, 2, 10):
        try:
            policies[phase] = load_codex_policy(phase, table_path)
            check.ok(f"phase {phase} has a validated Codex delegation policy")
        except TimelineError as exc:
            check.fail(str(exc))
    if len(policies) != 3:
        return
    discovery = policies[1]
    expected_research = {
        "targeted_local_call_limit": 12,
        "official_source_batch_call_limit": 4,
        "source_open_stop_sec": 180,
    }
    if discovery.get("research") == expected_research:
        check.ok("Discovery policy has 12 local / 4 official / 180-second limits")
    else:
        check.fail("Discovery policy research limits do not match the canonical bounds")
    instruction = (
        "No more tools. Return the phase artifact now from current evidence; "
        "list missing sources instead of researching further."
    )
    if all(policy.get("synthesis_instruction") == instruction for policy in policies.values()):
        check.ok("delegated phases share the canonical no-tools synthesis steer")
    else:
        check.fail("delegated phases do not share the canonical no-tools synthesis steer")


def check_native_timeline_fixtures(check: Check) -> None:
    try:
        policy = load_codex_policy(1, ROOT / "phases" / "phase-table.json")
    except TimelineError as exc:
        check.fail(f"cannot run native timeline fixtures: {exc}")
        return
    base = datetime(2026, 8, 12, 20, 48, 52, tzinfo=timezone.utc)

    early = new_timeline("fixture", 1, "thread", policy, base)
    try:
        record_synthesis(early, base + timedelta(seconds=195))
        check.fail("native timeline accepted a 195-second early synthesis steer")
    except TimelineError:
        check.ok("native timeline rejects a 195-second early synthesis steer")

    recent = new_timeline("fixture", 1, "thread", policy, base)
    record_synthesis(recent, base + timedelta(seconds=270))
    interrupt_at = base + timedelta(seconds=330)
    record_progress(recent, "tool", interrupt_at - timedelta(seconds=9))
    record_interrupt(recent, policy, interrupt_at)
    if classify_initial_progress(recent, policy, interrupt_at) == "productive_budget_exhausted":
        check.ok("progress nine seconds ago prevents initial stalled classification")
    else:
        check.fail("recent observable progress was misclassified as initial stall")

    obedient = new_timeline("fixture", 1, "thread", policy, base)
    record_synthesis(obedient, base + timedelta(seconds=270))
    record_progress(obedient, "message", base + timedelta(seconds=280))
    record_terminal(obedient, "completed", policy, base + timedelta(seconds=290))
    if not validate_timeline(obedient, policy):
        check.ok("worker that obeys the synthesis steer and returns validates")
    else:
        check.fail("obedient synthesis-return fixture did not validate")

    stalled = new_timeline("fixture", 1, "thread", policy, base)
    record_progress(stalled, "message", base + timedelta(seconds=250))
    record_synthesis(stalled, base + timedelta(seconds=270))
    record_interrupt(stalled, policy, base + timedelta(seconds=330))
    record_resume(stalled, "thread", base + timedelta(seconds=334))
    record_terminal(stalled, "native_completion_stalled", policy, base + timedelta(seconds=454))
    if not validate_timeline(stalled, policy):
        check.ok("resumed no-progress full bound becomes native_completion_stalled")
    else:
        check.fail("resumed no-progress stall fixture did not validate")

    host = new_timeline("fixture", 1, "thread", policy, base)
    record_host_failure(host, 1312, base + timedelta(seconds=1))
    if not validate_timeline(host, policy) and host["terminal_state"] is None:
        check.ok("Windows error 1312 remains a host pre-execution failure")
    else:
        check.fail("Windows error 1312 leaked into worker terminal semantics")


def main() -> int:
    check = Check()
    print("=== Codex Platform Health Check ===")
    check_agents_md(check)
    check_obi_profile(check)
    check_hooks_json(check)
    check_repo_support(check)
    check_native_phase_policy(check)
    check_native_timeline_fixtures(check)

    print()
    print(f"Checks failed: {len(check.failures)}")
    print(f"Warnings: {len(check.warnings)}")
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())

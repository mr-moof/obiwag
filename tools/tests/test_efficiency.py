"""Focused regressions for the agent-efficiency facade."""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

import pytest

from tools.efficiency import (
    build_handoff,
    learning_eligibility,
    load_contract,
    read_independent_batch,
    route_effort,
    select_transition,
    validate_handoff,
    collect_learning_evidence,
)


REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "phases" / "phase-table.json"


@pytest.fixture()
def roots(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    project.mkdir()
    runtime.mkdir()
    return project, runtime


@pytest.fixture()
def contract(roots: tuple[Path, Path]) -> dict[str, object]:
    return load_contract(roots[0], roots[1], TABLE)


def routine_facts(platform: str = "codex") -> dict[str, object]:
    return {
        "platform": platform,
        "rigor": "standard",
        "familiarity": "familiar",
        "scope": "bounded",
        "security_impact": "none",
        "api_impact": "none",
        "integration_novelty": "known",
        "evidence_gaps": False,
        "tests_available": True,
    }


def test_routing_uses_single_policy_for_both_platforms(contract: dict[str, object]) -> None:
    assert route_effort(contract, routine_facts("claude"))["model"] == "sonnet"
    assert route_effort(contract, routine_facts("codex"))["model"] == "gpt-5.6-terra"
    strong = routine_facts()
    strong["security_impact"] = "unknown"
    assert route_effort(contract, strong)["model"] == "gpt-5.6-sol"


def test_unknown_or_max_routing_is_conservatively_strong(contract: dict[str, object]) -> None:
    facts = routine_facts("claude")
    del facts["tests_available"]
    assert route_effort(contract, facts)["tier"] == "strong"
    facts = routine_facts()
    facts['tests_available'] = 1
    assert route_effort(contract, facts)['tier'] == 'strong'
    facts = routine_facts("claude")
    facts["rigor"] = "max"
    assert route_effort(contract, facts)["tier"] == "strong"


def test_independent_read_batch_preserves_success_and_error(roots: tuple[Path, Path]) -> None:
    (roots[0] / "exists.txt").write_text("evidence", encoding="utf-8")
    results = read_independent_batch(
        [{"id": "ok", "root": "project", "path": "exists.txt"},
         {"id": "missing", "root": "runtime", "path": "missing.txt"}],
        {"project": roots[0], "runtime": roots[1]},
    )
    assert results[0]["unknown"] is False and results[0]["error"] is None
    assert results[1]["unknown"] is True and results[1]["error"]["type"] == "FileNotFoundError"


def test_handoff_hashes_sources_and_replaces_overflow_with_reachable_reference(
    contract: dict[str, object], roots: tuple[Path, Path]
) -> None:
    (roots[0] / "source.txt").write_text("x" * 200, encoding="utf-8")
    changed = json.loads(json.dumps(contract))
    changed["efficiency"]["handoff_budgets"] = {
        "total_bytes": 4096,
        "sections": {"narrative": 1024, "references": 2048, "excerpts": 2, "read_batches": 512},
    }
    packet = build_handoff(changed, {
        "run_id": "run", "from_phase": 1, "to_phase": 2,
        "acceptance_criteria": ["keep evidence reachable"],
        "source_artifacts": [{"id": "src", "root": "project", "path": "source.txt", "required": True}],
    }, *roots)
    source = packet["source_artifacts"][0]
    assert "excerpt" not in source
    assert source["retrieval"]["sha256"] == source["sha256"]
    assert validate_handoff(packet, *roots, changed) == {"valid": True, "errors": []}


def test_handoff_rejects_stale_source_hash(contract: dict[str, object], roots: tuple[Path, Path]) -> None:
    source = roots[0] / "source.txt"
    source.write_text("before", encoding="utf-8")
    packet = build_handoff(contract, {
        "run_id": "run", "from_phase": 1, "to_phase": 2,
        "source_artifacts": [{"root": "project", "path": "source.txt"}],
    }, *roots)
    source.write_text("after", encoding="utf-8")
    result = validate_handoff(packet, *roots, contract)
    assert result["valid"] is False
    assert any("stale source hash" in error for error in result["errors"])


def test_transition_uses_default_strategy_and_configured_readme_skip(
    contract: dict[str, object], roots: tuple[Path, Path]
) -> None:
    result = select_transition(contract, "standard", 7, "README SKIPPED: no changes", {}, roots[0], TABLE)
    assert result["default_strategy"] == "inline"
    assert result["skipped_phases"] == [8]
    assert result["next_phase"] == 9
    assert result == select_transition(contract, "standard", 7, "README SKIPPED: no changes", {}, roots[0], TABLE)


def test_integrate_skip_does_not_trust_caller_success_assertion(
    contract: dict[str, object], roots: tuple[Path, Path]
) -> None:
    result = select_transition(
        contract, "standard", 5, "INTEGRATE NO-OP: caller says pass",
        {"no_op_guard": "PASS", "guard_verified": True}, roots[0], TABLE,
    )
    assert result["guard"]["verified"] is False
    assert result["signal"] == "INTEGRATE COMPLETE"
    assert result["skipped_phases"] == []
    assert result["next_phase"] == 6


def valid_learning() -> dict[str, object]:
    return {
        "blindspot_evidence": {"available": True, "parse_ok": True, "candidates": 0},
        "pending_learnings": {"available": True, "parse_ok": True, "count": 0},
        "pending_evolutions": {"available": True, "parse_ok": True, "count": 0},
        "memory_health": {"available": True, "parse_ok": True, "due": False},
        "session_findings": {"available": True, "parse_ok": True, "corrections": 0,
                             "reusable_discoveries": 0, "unresolved_failures": 0},
        "codex_hook_evidence": {"available": True, "parse_ok": True, "candidates": 0},
    }


def test_learning_real_no_work_skip_and_fail_closed_unknown() -> None:
    assert learning_eligibility(valid_learning())["signal"] == "LEARNING SKIPPED: no actionable work"
    unavailable = valid_learning()
    unavailable["codex_hook_evidence"] = {"available": False, "parse_ok": False}
    assert learning_eligibility(unavailable)["action"] == "dispatch"


def test_learning_dispatches_for_pending_work_and_malformed_counts() -> None:
    pending = valid_learning()
    pending["pending_learnings"]["count"] = 1
    assert learning_eligibility(pending)["action"] == "dispatch"
    malformed = valid_learning()
    malformed["pending_evolutions"]["count"] = "unknown"
    assert learning_eligibility(malformed)["action"] == "dispatch"


def test_learning_collector_reads_real_queues_and_rejects_malformed_state(tmp_path):
    state = tmp_path / 'active-state'
    (state / 'pending').mkdir(parents=True)
    (state / 'state').mkdir()
    (state / 'pending-learnings.json').write_text('{"learnings": []}')
    (state / 'state/last-maintenance.json').write_text(json.dumps({'last_run_iso': datetime.now().isoformat()}))
    evidence = valid_learning()
    evidence['state_root'] = str(state)
    observed = collect_learning_evidence(evidence, tmp_path)
    assert learning_eligibility(observed)['action'] == 'skip'
    (state / 'pending-learnings.json').write_text('{broken')
    assert learning_eligibility(collect_learning_evidence(evidence, tmp_path))['action'] == 'dispatch'


def test_learning_collector_retains_blindspot_and_due_health_work(tmp_path):
    state = tmp_path / 'active-state'
    (state / 'pending').mkdir(parents=True)
    (state / 'state').mkdir()
    (state / 'pending-learnings.json').write_text('{"learnings": []}')
    (state / 'state/last-maintenance.json').write_text('{"last_run_iso":"2000-01-01T00:00:00"}')
    (tmp_path / '.obi').mkdir()
    (tmp_path / '.obi/codex-catches.jsonl').write_text('{"category":"security"}\n')
    evidence = valid_learning()
    evidence['state_root'] = str(state)
    observed = collect_learning_evidence(evidence, tmp_path)
    assert observed['blindspot_evidence']['candidates'] == 1
    assert observed['memory_health']['due'] is True
    assert learning_eligibility(observed)['action'] == 'dispatch'

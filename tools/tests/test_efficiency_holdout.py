"""Adversarial acceptance cases kept separate from routing development examples."""
from copy import deepcopy
from pathlib import Path

import pytest

from tools.efficiency import (
    EfficiencyError, build_handoff, inventory_inputs, learning_eligibility, load_contract,
    select_transition, validate_handoff,
)

TABLE = Path(__file__).resolve().parents[2] / 'phases/phase-table.json'


@pytest.fixture
def context(tmp_path):
    project, runtime = tmp_path / 'project', tmp_path / 'runtime'
    project.mkdir()
    runtime.mkdir()
    return project, runtime, load_contract(project, runtime, TABLE)


@pytest.mark.parametrize('phase,signal,disposition', [
    (1, 'HARD STOP: missing vendor source', 'stop'),
    (2, 'NEEDS_CONTEXT: missing API', 'resolve_blocker'),
    (2, '3-STRIKE LIMIT', 'resolve_blocker'),
    (3, 'COMPLETE_WITH_CONCERNS', 'inspect_concerns'),
    (6, 'RE-REVIEW COMPLETE', 'inspect_verdict'),
    (8, 'README REVIEW COMPLETE', 'inspect_verdict'),
    (9, 'RELEASE GATE FAILED: tests failed', 'repair_phase'),
])
def test_failure_signals_cannot_advance(context, phase, signal, disposition):
    project, _, contract = context
    result = select_transition(contract, 'standard', phase, signal, {}, project, TABLE)
    assert result['next_phase'] == phase
    assert result['disposition'] == disposition


def test_skip_signal_cannot_hide_unknown_learning(context):
    project, _, contract = context
    with pytest.raises(EfficiencyError, match='eligibility'):
        select_transition(contract, 'standard', 10, 'LEARNING SKIPPED: no actionable work', {}, project, TABLE)


def test_recovery_replay_reuses_authority_record(context):
    project, _, contract = context
    request = {'event': 'peer_unavailable', 'run_id': 'holdout', 'phase': 4,
               'provider': 'claude', 'evidence': 'Read-only peer unavailable; no content sent',
               'state_path': '.obi/state/status-updates-holdout.jsonl'}
    inputs = (contract, 'standard', 4, 'REVIEW COMPLETE: PASS', {'recovery_request': request}, project, TABLE)
    first = select_transition(*inputs)
    assert select_transition(*inputs) == first
    assert len((project / request['state_path']).read_text().splitlines()) == 1
    assert first['recovery_decision']['recovery_action'] == 'local-review'


def test_wrong_root_and_forged_excerpt_are_rejected(context):
    project, runtime, contract = context
    (project / 'evidence.txt').write_text('real defect: failed source verification', encoding='utf-8')
    payload = {'run_id': 'holdout', 'from_phase': 2, 'to_phase': 4,
               'source_artifacts': [{'root': 'project', 'path': 'evidence.txt'}]}
    packet = build_handoff(contract, payload, project, runtime)
    assert not validate_handoff(packet, runtime, project, contract)['valid']
    packet['source_artifacts'][0]['excerpt'] = 'all checks passed'
    assert not validate_handoff(packet, project, runtime, contract)['valid']
    payload['source_artifacts'][0]['path'] = '../outside.txt'
    with pytest.raises(EfficiencyError, match='escapes'):
        build_handoff(contract, payload, project, runtime)


def test_required_defect_evidence_survives_overflow(context):
    project, runtime, contract = context
    source = project / 'finding.txt'
    source.write_text('Padding\n' * 5000 + 'DEFECT: author omitted required validation', encoding='utf-8')
    packet = build_handoff(contract, {'run_id': 'heldout', 'from_phase': 2, 'to_phase': 4,
        'source_artifacts': [{'root': 'project', 'path': 'finding.txt', 'required': True}]}, project, runtime)
    ref = packet['source_artifacts'][0]
    assert 'excerpt' not in ref
    assert 'DEFECT:' in (project / ref['retrieval']['path']).read_text()
    assert validate_handoff(packet, project, runtime, contract)['valid']


def test_full_input_budget_catches_persona_plus_packet(context):
    project, runtime, contract = context
    contract = deepcopy(contract)
    contract['efficiency']['controlled_prompt_bytes'] = 100
    (project / 'packet').write_text('x' * 60)
    (runtime / 'persona').write_text('y' * 60)
    with pytest.raises(EfficiencyError, match='controlled prompt exceeds'):
        inventory_inputs(contract, {'components': [
            {'root': 'project', 'path': 'packet'}, {'root': 'runtime', 'path': 'persona'}]}, project, runtime)


def test_malformed_learning_counts_cannot_look_empty():
    evidence = {'pending_learnings': {'available': True, 'parse_ok': True, 'count': False}}
    assert learning_eligibility(evidence)['action'] == 'dispatch'


def test_terminal_recovery_wins_and_wrong_phase_is_rejected(context):
    project, _, contract = context
    request = {'event': 'user_abort', 'run_id': 'abort', 'phase': 2,
               'provider': 'claude', 'evidence': 'User cancelled this task',
               'state_path': '.obi/state/status-updates-abort.jsonl'}
    result = select_transition(contract, 'standard', 2, 'AUTHOR COMPLETE',
                               {'run_id': 'abort', 'recovery_request': request}, project, TABLE)
    assert result['disposition'] == 'stop' and result['next_phase'] is None
    request['phase'] = 4
    with pytest.raises(EfficiencyError, match='run/phase'):
        select_transition(contract, 'standard', 2, 'AUTHOR COMPLETE', {'recovery_request': request}, project, TABLE)


def test_actual_verdict_required_and_max_phase_zero_supported(context):
    project, _, contract = context
    assert select_transition(contract, 'standard', 6, 'RE-REVIEW COMPLETE',
                             {'verdict': 'PASS'}, project, TABLE)['next_phase'] == 6
    (project / 'review.md').write_text('Verdict: PASS\nSources verified.\n')
    assert select_transition(contract, 'standard', 6, 'RE-REVIEW COMPLETE',
                             {'verdict_report': 'review.md'}, project, TABLE)['next_phase'] == 7
    assert select_transition(contract, 'max', 0, 'PHASE 0 COMPLETE', {}, project, TABLE)['next_phase'] == 1
    failure = select_transition(contract, 'express', 4, 'REVIEW COMPLETE: FAIL 2 issues', {}, project, TABLE)
    assert failure['next_phase'] == 5 and failure['required_action'] == 'integrate_findings'


def test_stale_batch_evidence_is_rejected(context):
    project, runtime, contract = context
    source = project / 'policy.md'
    source.write_text('Require source verification')
    packet = build_handoff(contract, {'run_id': 'batch', 'from_phase': 1, 'to_phase': 2,
        'independent_read_batches': [{'name': 'policies', 'reads': [
            {'id': 'policy', 'root': 'project', 'path': 'policy.md'}]}]}, project, runtime)
    source.write_text('Changed policy')
    result = validate_handoff(packet, project, runtime, contract)
    assert not result['valid'] and any('stale independent read' in error for error in result['errors'])

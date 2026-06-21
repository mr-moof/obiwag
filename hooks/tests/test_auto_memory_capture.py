"""Unit tests for hooks/core/auto_memory_capture.py."""

import json
import sys
from pathlib import Path

import pytest

# Add hooks/ to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.auto_memory_capture import (
    ANS_ID_TO_PROBE,
    CONFIDENCE_THRESHOLD,
    VALID_MEMORY_TYPES,
    capture_surprise,
    compute_surprises,
    is_actionable,
    parse_classifier_verdict,
    render_surprise_md,
    slugify,
    write_surprise_artifacts,
)


# ---------- compute_surprises ----------

class TestComputeSurprises:

    def test_empty_inputs_return_empty(self):
        assert compute_surprises(phase=1, locked_answers=[], probe_outcomes=[]) == []

    def test_no_matching_probe_skips(self):
        answers = [{'id': 'target_namespace', 'answer': 'group'}]
        probes = [{'probe': 'unrelated', 'status': 'ok', 'data': {}}]
        assert compute_surprises(1, answers, probes) == []

    def test_routes_target_namespace_to_namespace_kind_probe(self):
        """Regression: bug found in Round 1 review. The phase0:id
        'target_namespace' must route to probe 'namespace_kind' per
        policies/obi-auto-max-schema.md, not look up by 'target_namespace'."""
        answers = [{'id': 'target_namespace', 'answer': 'group'}]
        probes = [{
            'probe': 'namespace_kind',  # real probe name, not the answer id
            'status': 'ok',
            'data': {'kind': 'user', 'id': 1, 'full_path': 'user'},
        }]
        out = compute_surprises(0, answers, probes)
        assert len(out) == 1, "Routing failed: ans_id 'target_namespace' did not resolve to probe 'namespace_kind'"
        assert out[0]['actual_probe'] == 'namespace_kind'

    def test_ans_id_to_probe_mirrors_schema_doc(self):
        """The mapping must include all five probe routes from the schema doc."""
        assert 'target_namespace' in ANS_ID_TO_PROBE
        assert 'runner_tags' in ANS_ID_TO_PROBE
        assert 'pages_access' in ANS_ID_TO_PROBE
        assert 'marketplace_url' in ANS_ID_TO_PROBE
        assert 'mirror_target' in ANS_ID_TO_PROBE
        assert ANS_ID_TO_PROBE['target_namespace'] == 'namespace_kind'
        assert ANS_ID_TO_PROBE['marketplace_url'] == 'marketplace_reach'
        assert ANS_ID_TO_PROBE['mirror_target'] == 'mirror_existence'

    def test_unknown_ans_id_falls_back_to_self_lookup(self):
        """Custom phase0 ids without an ANS_ID_TO_PROBE entry should still
        work if the user names their probe to match the answer id."""
        answers = [{'id': 'custom_probe', 'answer': 'expected'}]
        probes = [{'probe': 'custom_probe', 'status': 'auth_failure', 'error': '401'}]
        out = compute_surprises(0, answers, probes)
        assert len(out) == 1

    def test_probe_status_failure_is_surprise(self):
        answers = [{'id': 'target_namespace', 'answer': 'group'}]
        # Use the REAL probe name (routed via ANS_ID_TO_PROBE), not the
        # answer id — see policies/obi-auto-max-schema.md Probe Routing.
        probes = [{
            'probe': 'namespace_kind',
            'status': 'auth_failure',
            'data': {},
            'error': '401',
        }]
        out = compute_surprises(2, answers, probes)
        assert len(out) == 1
        assert out[0]['phase'] == 2
        assert out[0]['expected_id'] == 'target_namespace'
        assert out[0]['actual_probe'] == 'namespace_kind'
        assert out[0]['actual_status'] == 'auth_failure'
        assert 'auth_failure' in out[0]['divergence']

    def test_answer_not_in_probe_data_is_surprise(self):
        answers = [{'id': 'target_namespace', 'answer': 'group'}]
        probes = [{
            'probe': 'namespace_kind',
            'status': 'ok',
            'data': {'kind': 'user', 'id': 42, 'full_path': 'user'},
        }]
        out = compute_surprises(0, answers, probes)
        assert len(out) == 1
        assert 'not present' in out[0]['divergence']
        assert out[0]['actual_status'] == 'ok'

    def test_answer_in_probe_data_is_not_surprise(self):
        answers = [{'id': 'target_namespace', 'answer': 'group'}]
        probes = [{
            'probe': 'namespace_kind',
            'status': 'ok',
            'data': {'kind': 'group', 'id': 7, 'full_path': 'example-org/services'},
        }]
        # 'group' is in data, no surprise
        assert compute_surprises(0, answers, probes) == []


# ---------- parse_classifier_verdict ----------

class TestParseClassifierVerdict:

    def test_clean_json(self):
        v = parse_classifier_verdict(
            '{"confidence": 0.8, "summary": "ns is user not group", "type": "tool"}'
        )
        assert v == {'confidence': 0.8, 'summary': 'ns is user not group', 'type': 'tool'}

    def test_json_in_prose(self):
        raw = (
            'Yes this is actionable.\n\n'
            '{"confidence": 0.95, "summary": "x", "type": "feedback"}\n'
            'End of analysis.'
        )
        v = parse_classifier_verdict(raw)
        assert v is not None
        assert v['confidence'] == 0.95
        assert v['type'] == 'feedback'

    def test_invalid_json_returns_none(self):
        assert parse_classifier_verdict('not json') is None
        assert parse_classifier_verdict('') is None
        assert parse_classifier_verdict('{not valid}') is None

    def test_missing_required_fields(self):
        assert parse_classifier_verdict('{"confidence": 0.8}') is None
        assert parse_classifier_verdict('{"summary": "x", "type": "user"}') is None
        assert parse_classifier_verdict(
            '{"confidence": 0.8, "summary": "", "type": "user"}'
        ) is None

    def test_invalid_type_returns_none(self):
        v = parse_classifier_verdict(
            '{"confidence": 0.9, "summary": "x", "type": "not-a-type"}'
        )
        assert v is None

    def test_all_valid_types_accepted(self):
        for mtype in VALID_MEMORY_TYPES:
            v = parse_classifier_verdict(
                '{"confidence": 0.8, "summary": "x", "type": "' + mtype + '"}'
            )
            assert v is not None
            assert v['type'] == mtype


# ---------- is_actionable threshold ----------

class TestIsActionable:

    def test_above_threshold(self):
        assert is_actionable({'confidence': 0.8, 'summary': 'x', 'type': 'tool'}) is True
        assert is_actionable({'confidence': 0.7, 'summary': 'x', 'type': 'tool'}) is True

    def test_below_threshold(self):
        assert is_actionable({'confidence': 0.69, 'summary': 'x', 'type': 'tool'}) is False
        assert is_actionable({'confidence': 0.0, 'summary': 'x', 'type': 'tool'}) is False

    def test_none_verdict(self):
        assert is_actionable(None) is False

    def test_custom_threshold(self):
        assert is_actionable({'confidence': 0.5, 'summary': 'x', 'type': 'tool'}, threshold=0.4) is True
        assert is_actionable({'confidence': 0.5, 'summary': 'x', 'type': 'tool'}, threshold=0.6) is False


# ---------- slugify ----------

class TestSlugify:

    def test_basic(self):
        assert slugify('Hello World') == 'hello_world'

    def test_punctuation(self):
        assert slugify('foo, bar! baz?') == 'foo_bar_baz'

    def test_collapses_runs(self):
        assert slugify('foo   bar') == 'foo_bar'

    def test_caps_at_60_chars(self):
        s = slugify('x' * 100)
        assert len(s) == 60

    def test_empty_returns_unnamed(self):
        assert slugify('') == 'unnamed'
        assert slugify('!!!') == 'unnamed'


# ---------- render_surprise_md ----------

class TestRenderSurpriseMd:

    def test_renders_frontmatter_and_body(self):
        candidate = {
            'phase': 5,
            'expected_id': 'target_namespace',
            'expected_value': 'group',
            'actual_probe': 'target_namespace',
            'actual_value': 'kind=user',
            'actual_status': 'ok',
            'divergence': 'answer not present in probe data',
        }
        verdict = {
            'confidence': 0.85,
            'summary': 'fork target was user namespace not group',
            'type': 'tool',
        }
        filename, content = render_surprise_md(candidate, verdict)
        assert filename.startswith('surprise-5-')
        assert filename.endswith('.md')
        assert '---' in content
        assert 'auto_captured: true' in content
        assert 'confidence: 0.85' in content
        assert 'phase: 5' in content
        assert 'type: tool' in content
        assert 'target_namespace' in content
        assert 'fork target was user namespace not group' in content


# ---------- write_surprise_artifacts ----------

class TestWriteSurpriseArtifacts:

    def test_writes_md_and_appends_json(self, tmp_path):
        pending = tmp_path / 'pending'
        learnings = tmp_path / 'pending-learnings.json'

        candidate = {
            'phase': 3,
            'expected_id': 'runner_tags',
            'expected_value': 'win-container-bld',
            'actual_probe': 'runner_tags',
            'actual_value': 'pscodesign',
            'actual_status': 'ok',
            'divergence': 'answer not present in probe data',
        }
        verdict = {
            'confidence': 0.92,
            'summary': 'the runner tag is fast not slow',
            'type': 'tool',
        }

        result = write_surprise_artifacts(candidate, verdict, pending, learnings)

        # Markdown file
        assert pending.exists()
        md_files = list(pending.glob('surprise-3-*.md'))
        assert len(md_files) == 1
        body = md_files[0].read_text(encoding='utf-8')
        assert 'pscodesign' in body
        assert 'win-container-bld' in body

        # JSON entry
        assert learnings.exists()
        data = json.loads(learnings.read_text(encoding='utf-8'))
        assert 'learnings' in data
        assert len(data['learnings']) == 1
        e = data['learnings'][0]
        assert e['type'] == 'tool'
        assert e['title'].startswith('the runner tag')
        assert e['confidence'] == 0.92
        assert e['metadata']['auto_captured'] is True
        assert e['metadata']['phase'] == 3
        assert e['metadata']['actual_probe'] == 'runner_tags'

        # Result paths point at written files
        assert result['md_path'] == str(md_files[0])
        assert result['json_path'] == str(learnings)

    def test_appends_to_existing_pending_learnings(self, tmp_path):
        pending = tmp_path / 'pending'
        learnings = tmp_path / 'pending-learnings.json'
        learnings.write_text(json.dumps({
            'learnings': [{'title': 'pre-existing', 'type': 'user',
                           'content': '', 'target_file': '', 'confidence': 0.7,
                           'metadata': {}}]
        }), encoding='utf-8')

        candidate = {
            'phase': 1, 'expected_id': 'a', 'expected_value': 'b',
            'actual_probe': 'a', 'actual_value': 'c', 'actual_status': 'ok',
            'divergence': 'mismatch',
        }
        verdict = {'confidence': 0.9, 'summary': 'new entry', 'type': 'user'}

        write_surprise_artifacts(candidate, verdict, pending, learnings)

        data = json.loads(learnings.read_text(encoding='utf-8'))
        assert len(data['learnings']) == 2
        assert data['learnings'][0]['title'] == 'pre-existing'
        assert data['learnings'][1]['title'] == 'new entry'

    def test_sequential_appends_preserve_all_entries(self, tmp_path):
        """Sequential appends must preserve all prior entries plus add new."""
        import time
        pending = tmp_path / 'pending'
        learnings = tmp_path / 'pending-learnings.json'
        # Pre-seed with one entry
        learnings.write_text(json.dumps({
            'learnings': [{
                'title': 'pre-existing', 'type': 'user',
                'content': '', 'target_file': '', 'confidence': 0.7,
                'metadata': {},
            }]
        }), encoding='utf-8')

        candidate_a = {
            'phase': 1, 'expected_id': 'a', 'expected_value': 'b',
            'actual_probe': 'a', 'actual_value': 'c', 'actual_status': 'ok',
            'divergence': 'mismatch_a',
        }
        candidate_b = {
            'phase': 2, 'expected_id': 'x', 'expected_value': 'y',
            'actual_probe': 'x', 'actual_value': 'z', 'actual_status': 'ok',
            'divergence': 'mismatch_b',
        }
        verdict_a = {'confidence': 0.9, 'summary': 'entry-a', 'type': 'user'}
        verdict_b = {'confidence': 0.9, 'summary': 'entry-b', 'type': 'user'}

        write_surprise_artifacts(candidate_a, verdict_a, pending, learnings)
        time.sleep(0.005)  # ensure mtime tick
        write_surprise_artifacts(candidate_b, verdict_b, pending, learnings)

        data = json.loads(learnings.read_text(encoding='utf-8'))
        titles = [l.get('title') for l in data['learnings']]
        assert 'pre-existing' in titles
        assert 'entry-a' in titles
        assert 'entry-b' in titles
        assert len(data['learnings']) == 3

    def test_concurrent_threads_serialize_via_lock(self, tmp_path):
        """Round 3 regression: spawn N threads racing for the lock. Each
        thread appends its own entry. After all complete, every entry must
        be present (no drops from TOCTOU). Verifies the os.open(O_EXCL) lock
        fix actually serializes concurrent appends."""
        import threading

        pending = tmp_path / 'pending'
        learnings = tmp_path / 'pending-learnings.json'
        n_threads = 8

        def worker(i):
            candidate = {
                'phase': i,
                'expected_id': f'k{i}', 'expected_value': f'v{i}',
                'actual_probe': f'p{i}', 'actual_value': f'a{i}',
                'actual_status': 'ok',
                'divergence': f'diverge_{i}',
            }
            verdict = {'confidence': 0.9, 'summary': f'race-entry-{i}', 'type': 'user'}
            write_surprise_artifacts(candidate, verdict, pending, learnings)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        data = json.loads(learnings.read_text(encoding='utf-8'))
        titles = sorted(l.get('title') for l in data['learnings'])
        expected = sorted(f'race-entry-{i}' for i in range(n_threads))
        assert titles == expected, (
            f"Lost entries to race! Expected {expected}, got {titles}"
        )
        assert len(data['learnings']) == n_threads

        # Lock file should not be left behind after all threads complete
        lock_path = learnings.with_name(learnings.name + '.lock')
        assert not lock_path.exists()

    def test_recovers_from_corrupted_pending_learnings(self, tmp_path):
        pending = tmp_path / 'pending'
        learnings = tmp_path / 'pending-learnings.json'
        learnings.write_text('not json {{{', encoding='utf-8')

        candidate = {
            'phase': 1, 'expected_id': 'a', 'expected_value': 'b',
            'actual_probe': 'a', 'actual_value': 'c', 'actual_status': 'ok',
            'divergence': 'mismatch',
        }
        verdict = {'confidence': 0.9, 'summary': 'new', 'type': 'user'}

        write_surprise_artifacts(candidate, verdict, pending, learnings)
        data = json.loads(learnings.read_text(encoding='utf-8'))
        assert len(data['learnings']) == 1  # corrupted state replaced


# ---------- capture_surprise (end-to-end) ----------

class TestCaptureSurprise:

    @pytest.fixture
    def candidate(self):
        return {
            'phase': 5,
            'expected_id': 'target_namespace',
            'expected_value': 'group',
            'actual_probe': 'target_namespace',
            'actual_value': 'kind=user',
            'actual_status': 'ok',
            'divergence': 'answer not present in probe data',
        }

    def test_captures_above_threshold(self, tmp_path, candidate):
        result = capture_surprise(
            candidate=candidate,
            verdict_raw='{"confidence": 0.85, "summary": "ns mismatch", "type": "tool"}',
            pending_dir=tmp_path / 'pending',
            learnings_json_path=tmp_path / 'pending-learnings.json',
            quality_log_path=tmp_path / 'session-quality.jsonl',
        )
        assert result is not None
        assert Path(result['md_path']).exists()
        assert Path(result['json_path']).exists()

    def test_skips_below_threshold(self, tmp_path, candidate):
        result = capture_surprise(
            candidate=candidate,
            verdict_raw='{"confidence": 0.4, "summary": "weak", "type": "tool"}',
            pending_dir=tmp_path / 'pending',
            learnings_json_path=tmp_path / 'pending-learnings.json',
            quality_log_path=tmp_path / 'session-quality.jsonl',
        )
        assert result is None
        # Skip should be logged
        log = (tmp_path / 'session-quality.jsonl')
        assert log.exists()
        entries = [json.loads(l) for l in log.read_text(encoding='utf-8').splitlines() if l.strip()]
        assert any(e.get('event') == 'auto-memory-skip' for e in entries)
        assert any('below threshold' in e.get('reason', '') for e in entries)

    def test_skips_invalid_classifier_json(self, tmp_path, candidate):
        result = capture_surprise(
            candidate=candidate,
            verdict_raw='absolutely not json',
            pending_dir=tmp_path / 'pending',
            learnings_json_path=tmp_path / 'pending-learnings.json',
            quality_log_path=tmp_path / 'session-quality.jsonl',
        )
        assert result is None
        log = (tmp_path / 'session-quality.jsonl')
        assert log.exists()
        entries = [json.loads(l) for l in log.read_text(encoding='utf-8').splitlines() if l.strip()]
        assert any('invalid' in e.get('reason', '').lower() for e in entries)

    def test_threshold_constant_matches_plan(self):
        assert CONFIDENCE_THRESHOLD == 0.7

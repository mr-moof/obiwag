"""Synthetic end-to-end task-type persistence through the Stop handler."""

import io
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import stop  # noqa: E402
import user_prompt_submit as ups  # noqa: E402
from core.session_state import SessionState, _file_lock  # noqa: E402


class _Timer:
    def set_input_summary(self, *a):
        pass

    def set_output_summary(self, *a):
        pass


@pytest.fixture
def captured(tmp_path, monkeypatch):
    """Run Stop against an isolated root; capture what it persists."""
    monkeypatch.setenv('OBI_ROOT', str(tmp_path))

    import core.stop_pipeline as sp

    calls = {}

    def fake_write(session_id, task_type, outcome, metrics, summary_text,
                   detailed_corrections):
        calls['task_type'] = task_type
        calls['outcome'] = outcome
        return str(tmp_path / 'summary.md')

    monkeypatch.setattr(sp, 'write_session_outputs', fake_write)
    monkeypatch.setattr(sp, 'run_quality_signal_write',
                        lambda *a, **k: calls.setdefault('quality_task_type', a[1]))
    monkeypatch.setattr(sp, 'update_calibration_metrics', lambda *a, **k: None)
    monkeypatch.setattr(sp, 'run_evolution_proposal_if_due', lambda *a, **k: None)
    return calls


def _run_stop(session_id, monkeypatch):
    """Invoke the real Stop handler with a minimal payload."""
    payload = {'session_id': session_id, 'transcript': 'user: do a thing\n'}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit):
        stop.main()


class TestTaskTypeSurvivesToStop:
    def test_grounded_session_reports_its_real_task_type(self, captured, monkeypatch):
        """The end-to-end assertion for #202: prompt -> grounding -> summary."""
        ups._handle({'prompt': 'fix the mail script on the example job in widgetapi',
                     'session_id': 'e2e-1'}, _Timer())
        assert SessionState('e2e-1').get('task_type') == 'widgetapi'

        _run_stop('e2e-1', monkeypatch)

        assert captured['task_type'] == 'widgetapi', (
            'Stop wrote the wrong task_type -- it used to always be "unknown" '
            'because state was deleted before this value was read'
        )
        assert captured.get('quality_task_type') == 'widgetapi'

    def test_ungrounded_session_still_reports_unknown(self, captured, monkeypatch):
        """No grounding means no task type -- 'unknown' is correct here."""
        _run_stop('e2e-none', monkeypatch)
        assert captured['task_type'] == 'unknown'

    def test_state_is_consumed_before_it_is_deleted(self, captured, monkeypatch):
        """Regression for the read-after-delete ordering.

        The state file must be gone once Stop finishes, AND the value must have
        reached the summary. Deleting first would satisfy only the former.
        """
        ups._handle({'prompt': "let's work on CanvasAPI", 'session_id': 'e2e-2'},
                    _Timer())
        state_file = SessionState('e2e-2').state_file

        _run_stop('e2e-2', monkeypatch)

        assert captured['task_type'] == 'canvasapi'
        assert not Path(state_file).exists(), 'Stop left the state file behind'

    def test_busy_lock_degrades_without_destroying_state(self, captured, monkeypatch):
        """If Stop cannot read the state, it must NOT delete it.

        The lock can free between the failed read and the delete, so an
        unconditional cleanup would discard state nobody consumed.
        """
        ups._handle({'prompt': 'widgetapi update set', 'session_id': 'e2e-3'},
                    _Timer())
        s = SessionState('e2e-3')

        with _file_lock(s.lock_file):
            _run_stop('e2e-3', monkeypatch)

        assert captured['task_type'] == 'unknown', 'expected degraded read'
        assert Path(s.state_file).exists(), 'state was deleted despite an unread snapshot'
        assert SessionState('e2e-3').get('task_type') == 'widgetapi', (
            'the preserved state should still hold the real task type'
        )


def test_stop_rejects_oversized_stdin_through_bounded_error_path(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv('OBI_ROOT', str(tmp_path))
    import core.hook_runtime as hook_runtime

    monkeypatch.setattr(hook_runtime, 'MAX_HOOK_INPUT_CHARS', 32)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('x' * 33))
    with pytest.raises(SystemExit):
        stop.main()
    output = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert 'HookInputTooLarge' in output['systemMessage']

"""Tests for hooks/core/detector_registry.py — registry, executor, DirtySessionDetector."""

import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.detector_registry import (  # noqa: E402
    Detector,
    DetectorContext,
    run_detectors,
    run_stop_detectors,
    run_session_start_detectors,
    _build_stop_detectors,
    _build_session_start_detectors,
)
from core.detectors.capability_claim_detector import CapabilityClaimDetector  # noqa: E402
from core.detectors.learning_detector_stop import LearningDetector  # noqa: E402
from core.detectors.correction_retriever_detector import CorrectionRetrieverDetector  # noqa: E402
from core.detectors.dirty_session_detector import DirtySessionDetector  # noqa: E402
from core.detectors.drift_detector_session_start import DriftDetectorSessionStart  # noqa: E402
from core.detectors.drift_detector_stop import DriftDetectorStop  # noqa: E402
from core.detectors.drift_nag_stop import DriftNagStop  # noqa: E402
from core.detectors.obi_state_backup_detector import ObiStateBackupDetector  # noqa: E402
from core.detectors.project_memory_backup_detector import ProjectMemoryBackupDetector  # noqa: E402
from core.detectors.strike_counter_detector import StrikeCounterDetector  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: stub detectors for executor tests
# ---------------------------------------------------------------------------

class StubDetector:
    """Minimal detector for executor tests."""

    def __init__(
        self,
        name: str = 'stub',
        hook_point: str = 'stop',
        enabled: bool = True,
        result: Optional[str] = None,
        raises: Optional[Exception] = None,
    ):
        self._name = name
        self._hook_point = hook_point
        self._enabled = enabled
        self._result = result
        self._raises = raises
        self.run_called = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def hook_point(self) -> str:
        return self._hook_point

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        return self._enabled

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        self.run_called = True
        if self._raises:
            raise self._raises
        return self._result


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestDetectorProtocol:
    """Verify that concrete detectors satisfy the Detector protocol."""

    def test_dirty_session_detector_is_protocol_instance(self):
        assert isinstance(DirtySessionDetector(), Detector)

    def test_strike_counter_detector_is_protocol_instance(self):
        assert isinstance(StrikeCounterDetector(), Detector)

    def test_project_memory_backup_detector_is_protocol_instance(self):
        assert isinstance(ProjectMemoryBackupDetector(), Detector)

    def test_obi_state_backup_detector_is_protocol_instance(self):
        assert isinstance(ObiStateBackupDetector(), Detector)

    def test_capability_claim_detector_is_protocol_instance(self):
        assert isinstance(CapabilityClaimDetector(), Detector)

    def test_drift_detector_stop_is_protocol_instance(self):
        assert isinstance(DriftDetectorStop(), Detector)

    def test_drift_nag_stop_is_protocol_instance(self):
        assert isinstance(DriftNagStop(), Detector)

    def test_drift_detector_session_start_is_protocol_instance(self):
        assert isinstance(DriftDetectorSessionStart(), Detector)

    def test_learning_detector_is_protocol_instance(self):
        assert isinstance(LearningDetector(), Detector)

    def test_correction_retriever_detector_is_protocol_instance(self):
        assert isinstance(CorrectionRetrieverDetector(), Detector)

    def test_stub_detector_is_protocol_instance(self):
        assert isinstance(StubDetector(), Detector)

    def test_object_is_not_protocol_instance(self):
        assert not isinstance(object(), Detector)


# ---------------------------------------------------------------------------
# DetectorContext
# ---------------------------------------------------------------------------

class TestDetectorContext:
    def test_construction_with_required_fields(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/tmp',
            start_time=time.time(),
        )
        assert ctx.hook_point == 'stop'
        assert ctx.cwd == '/tmp'
        assert ctx.calibration == {}
        assert ctx.transcript_text == ''
        assert ctx.metrics == {}
        assert ctx.session_id == ''

    def test_construction_with_all_fields(self):
        cal = {'safety': {'dirty_session_nag': True}}
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=100.0,
            calibration=cal,
            transcript_text='hello',
            metrics={'corrections': 1},
            session_id='abc-123',
            task_type='bugfix',
            task_text='fix it',
        )
        assert ctx.calibration is cal
        assert ctx.session_id == 'abc-123'
        assert ctx.task_type == 'bugfix'


# ---------------------------------------------------------------------------
# Executor: run_detectors
# ---------------------------------------------------------------------------

class TestRunDetectors:
    """Core executor behaviour: ordering, enablement, budget, error handling."""

    def _make_ctx(self, **overrides) -> DetectorContext:
        defaults = dict(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            calibration={'safety': {'dirty_session_nag': True}},
        )
        defaults.update(overrides)
        return DetectorContext(**defaults)

    def test_runs_enabled_detectors_in_order(self):
        order = []

        class OrderedDetector(StubDetector):
            def run(self, ctx):
                order.append(self._name)
                return f"\nmsg-{self._name}"

        d1 = OrderedDetector(name='first')
        d2 = OrderedDetector(name='second')
        d3 = OrderedDetector(name='third')

        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_detectors([d1, d2, d3], self._make_ctx())

        assert order == ['first', 'second', 'third']
        assert 'msg-first' in result
        assert 'msg-second' in result
        assert 'msg-third' in result

    def test_skips_disabled_detectors(self):
        enabled = StubDetector(name='on', enabled=True, result='\nhello')
        disabled = StubDetector(name='off', enabled=False, result='\nshould-not-appear')

        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_detectors([disabled, enabled], self._make_ctx())

        assert not disabled.run_called
        assert enabled.run_called
        assert 'hello' in result
        assert 'should-not-appear' not in (result or '')

    def test_time_budget_exhausted_emits_skip_message(self):
        """When time budget is exhausted, remaining detectors get a skip message."""
        d = StubDetector(name='late_detector', result='\nshould-not-run')

        # start_time far in the past => budget exhausted
        ctx = self._make_ctx(start_time=time.time() - 100)
        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_detectors([d], ctx, time_budget_seconds=7.0)

        assert not d.run_called
        assert result is not None
        assert "[Skipped: late_detector]" in result
        assert "over time budget" in result

    def test_time_budget_skip_message_format_matches_legacy(self):
        """The skip message format must match the legacy stop_advisories format."""
        d = StubDetector(name='dirty_session_nag')
        ctx = self._make_ctx(start_time=time.time() - 100)
        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_detectors([d], ctx, time_budget_seconds=7.0)
        # Legacy format was: "\n[Skipped: dirty_session_nag] Stop hook over time budget."
        assert "\n[Skipped: dirty_session_nag] Stop hook over time budget." in result

    def test_exception_swallowed_via_log_swallowed(self):
        """A raising detector is caught; log_swallowed is called; executor continues."""
        boom = StubDetector(name='boom', raises=RuntimeError("kaboom"))
        ok = StubDetector(name='ok', result='\nstill-here')

        with patch("core.hook_logger.HookTimer") as MockTimer, \
             patch("core.hook_logger.log_swallowed") as mock_log:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_detectors([boom, ok], self._make_ctx())

        mock_log.assert_called_once()
        component_arg = mock_log.call_args[0][0]
        assert component_arg == 'detector_boom'
        assert ok.run_called
        assert 'still-here' in result

    def test_none_result_not_accumulated(self):
        d = StubDetector(name='silent', result=None)

        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_detectors([d], self._make_ctx())

        assert result is None

    def test_empty_detector_list_returns_none(self):
        result = run_detectors([], self._make_ctx())
        assert result is None

    def test_hook_timer_segment_uses_correct_name(self):
        d = StubDetector(name='dirty_session', result='\nmsg')

        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            run_detectors([d], self._make_ctx())

        MockTimer.assert_called_once_with("Stop/dirty_session")

    def test_hook_timer_segment_uses_session_start_label(self):
        d = StubDetector(name='test_det', hook_point='session_start', result='\nmsg')

        ctx = self._make_ctx(hook_point='session_start')
        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            run_detectors([d], ctx)

        MockTimer.assert_called_once_with("Session_Start/test_det")


# ---------------------------------------------------------------------------
# DirtySessionDetector
# ---------------------------------------------------------------------------

class TestDirtySessionDetector:
    def test_name(self):
        assert DirtySessionDetector().name == 'dirty_session'

    def test_hook_point(self):
        assert DirtySessionDetector().hook_point == 'stop'

    def test_enabled_when_calibration_true(self):
        cal = {'safety': {'dirty_session_nag': True}}
        assert DirtySessionDetector().is_enabled(cal) is True

    def test_disabled_when_calibration_false(self):
        cal = {'safety': {'dirty_session_nag': False}}
        assert DirtySessionDetector().is_enabled(cal) is False

    def test_enabled_by_default_when_key_missing(self):
        assert DirtySessionDetector().is_enabled({}) is True

    def test_run_delegates_to_core_dirty_session(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/my/repo',
            start_time=time.time(),
        )
        files = [{"status": " M", "path": "file.py"}]
        with patch("core.dirty_session.get_dirty_file_list", return_value=files) as mock_list, \
             patch("core.dirty_session.format_dirty_session_nag", return_value="[Git] 1 file(s)") as mock_fmt:
            result = DirtySessionDetector().run(ctx)

        mock_list.assert_called_once_with('/my/repo')
        mock_fmt.assert_called_once_with(files)
        assert result == "\n[Git] 1 file(s)"

    def test_run_returns_none_for_clean_tree(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/my/repo',
            start_time=time.time(),
        )
        with patch("core.dirty_session.get_dirty_file_list", return_value=[]):
            result = DirtySessionDetector().run(ctx)
        assert result is None

    def test_run_passes_ctx_cwd(self, tmp_path):
        """Verify the detector passes ctx.cwd to get_dirty_file_list."""
        captured = {}

        def fake_get_dirty(path):
            captured['cwd'] = path
            return []

        ctx = DetectorContext(
            hook_point='stop',
            cwd=str(tmp_path),
            start_time=time.time(),
        )
        with patch("core.dirty_session.get_dirty_file_list", side_effect=fake_get_dirty):
            DirtySessionDetector().run(ctx)
        assert captured['cwd'] == str(tmp_path)


# ---------------------------------------------------------------------------
# StrikeCounterDetector
# ---------------------------------------------------------------------------

class TestStrikeCounterDetector:
    def test_name(self):
        assert StrikeCounterDetector().name == 'strike_counter'

    def test_hook_point(self):
        assert StrikeCounterDetector().hook_point == 'stop'

    def test_always_enabled(self):
        assert StrikeCounterDetector().is_enabled({}) is True
        assert StrikeCounterDetector().is_enabled({'safety': {'anything': False}}) is True

    def test_run_delegates_to_stop_pipeline(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            metrics={'corrections': 1, 'correction_details': [
                {'correction_type': 'x', 'confidence': 0.9, 'text': 'bad'},
            ]},
            session_id='abc-123',
        )
        with patch("core.stop_pipeline.run_strike_counter",
                   return_value="\n\nSTOP NOW") as mock_sc:
            result = StrikeCounterDetector().run(ctx)
        mock_sc.assert_called_once_with({'corrections': 1, 'correction_details': [
            {'correction_type': 'x', 'confidence': 0.9, 'text': 'bad'},
        ]}, 'abc-123')
        assert result == "\n\nSTOP NOW"

    def test_run_returns_none_when_no_checkpoint(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            metrics={'corrections': 0},
            session_id='sid',
        )
        with patch("core.stop_pipeline.run_strike_counter", return_value=None):
            result = StrikeCounterDetector().run(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# ProjectMemoryBackupDetector
# ---------------------------------------------------------------------------

class TestProjectMemoryBackupDetector:
    def test_name(self):
        assert ProjectMemoryBackupDetector().name == 'project_memory_backup'

    def test_hook_point(self):
        assert ProjectMemoryBackupDetector().hook_point == 'stop'

    def test_always_enabled(self):
        assert ProjectMemoryBackupDetector().is_enabled({}) is True

    def test_run_returns_message_on_backup(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
        )
        with patch("core.project_memory.backup_project_memory",
                   return_value="/some/snapshot/dir"):
            result = ProjectMemoryBackupDetector().run(ctx)
        assert result == "\n[Backup] Project memory snapshot saved."

    def test_run_returns_none_when_no_backup_needed(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
        )
        with patch("core.project_memory.backup_project_memory",
                   return_value=None):
            result = ProjectMemoryBackupDetector().run(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# ObiStateBackupDetector
# ---------------------------------------------------------------------------

class TestObiStateBackupDetector:
    def test_name(self):
        assert ObiStateBackupDetector().name == 'obi_state_backup'

    def test_hook_point(self):
        assert ObiStateBackupDetector().hook_point == 'stop'

    def test_always_enabled(self):
        assert ObiStateBackupDetector().is_enabled({}) is True

    def test_run_returns_message_on_backup(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
        )
        with patch("core.obi_state_backup.backup_obi_state",
                   return_value="/some/snapshot/dir"):
            result = ObiStateBackupDetector().run(ctx)
        assert result == "\n[Backup] Obi state snapshot saved."

    def test_run_returns_none_when_no_backup_needed(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
        )
        with patch("core.obi_state_backup.backup_obi_state",
                   return_value=None):
            result = ObiStateBackupDetector().run(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# CapabilityClaimDetector
# ---------------------------------------------------------------------------

class TestCapabilityClaimDetector:
    def test_name(self):
        assert CapabilityClaimDetector().name == 'capability_claim'

    def test_hook_point(self):
        assert CapabilityClaimDetector().hook_point == 'stop'

    def test_enabled_when_safety_key_true(self):
        cal = {'safety': {'capability_claim': True}}
        assert CapabilityClaimDetector().is_enabled(cal) is True

    def test_disabled_when_safety_key_false(self):
        cal = {'safety': {'capability_claim': False}}
        assert CapabilityClaimDetector().is_enabled(cal) is False

    def test_enabled_by_default_when_key_missing(self):
        assert CapabilityClaimDetector().is_enabled({}) is True

    def test_run_returns_none_when_detector_config_disabled(self):
        """Per-detector enable gate (detectors.capability_claim.enabled)
        is distinct from the safety toggle checked in is_enabled."""
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            transcript_text='some transcript',
        )
        with patch("core.calibration.get_detector_config",
                   return_value={'enabled': False,
                                 'firings_total': 0,
                                 'review_after_sessions': 20,
                                 'review_after_firings': 15}):
            result = CapabilityClaimDetector().run(ctx)
        assert result is None

    def test_run_returns_none_when_no_denials(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            transcript_text='clean transcript',
        )
        with patch("core.calibration.get_detector_config",
                   return_value={'enabled': True,
                                 'firings_total': 0,
                                 'review_after_sessions': 9999,
                                 'review_after_firings': 9999}), \
             patch("core.calibration.load_calibration",
                   return_value={'performance': {'total_sessions': 0}}), \
             patch("core.transcript_analyzer.get_recent_assistant_turns",
                   return_value=[]), \
             patch("core.capability_claim_detector.detect_unverified_denials",
                   return_value=[]):
            result = CapabilityClaimDetector().run(ctx)
        assert result is None

    def test_run_returns_nag_when_denials_found(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            transcript_text='I cannot access that file',
        )
        with patch("core.calibration.get_detector_config",
                   return_value={'enabled': True,
                                 'firings_total': 0,
                                 'review_after_sessions': 9999,
                                 'review_after_firings': 9999}), \
             patch("core.calibration.load_calibration",
                   return_value={'performance': {'total_sessions': 0}}), \
             patch("core.transcript_analyzer.get_recent_assistant_turns",
                   return_value=[{'content': 'I cannot access that'}]), \
             patch("core.capability_claim_detector.detect_unverified_denials",
                   return_value=["I cannot access that"]), \
             patch("core.capability_claim_detector.format_capability_denial_nag",
                   return_value="[Deflection] denial detected"), \
             patch("core.calibration.increment_detector_firings",
                   return_value=True):
            result = CapabilityClaimDetector().run(ctx)
        assert result is not None
        assert "[Deflection]" in result

    def test_run_uses_ctx_transcript_text(self):
        """Verify the detector passes ctx.transcript_text to get_recent_assistant_turns."""
        captured = {}

        def fake_get_recent(text, n=2):
            captured['text'] = text
            return []

        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            transcript_text='my special transcript',
        )
        with patch("core.calibration.get_detector_config",
                   return_value={'enabled': True,
                                 'firings_total': 0,
                                 'review_after_sessions': 9999,
                                 'review_after_firings': 9999}), \
             patch("core.calibration.load_calibration",
                   return_value={'performance': {'total_sessions': 0}}), \
             patch("core.transcript_analyzer.get_recent_assistant_turns",
                   side_effect=fake_get_recent), \
             patch("core.capability_claim_detector.detect_unverified_denials",
                   return_value=[]):
            CapabilityClaimDetector().run(ctx)
        assert captured['text'] == 'my special transcript'


# ---------------------------------------------------------------------------
# LearningDetector
# ---------------------------------------------------------------------------

class TestLearningDetector:
    def test_name(self):
        assert LearningDetector().name == 'learning'

    def test_hook_point(self):
        assert LearningDetector().hook_point == 'stop'

    def test_enabled_when_autonomous_learning_true(self):
        cal = {'safety': {'autonomous_learning': True}}
        assert LearningDetector().is_enabled(cal) is True

    def test_disabled_when_autonomous_learning_false(self):
        cal = {'safety': {'autonomous_learning': False}}
        assert LearningDetector().is_enabled(cal) is False

    def test_enabled_by_default_when_key_missing(self):
        assert LearningDetector().is_enabled({}) is True


# ---------------------------------------------------------------------------
# Registry wiring: run_stop_detectors
# ---------------------------------------------------------------------------

class TestRunStopDetectors:
    def test_build_stop_detectors_returns_all_eight(self):
        detectors = _build_stop_detectors()
        assert len(detectors) == 8
        names = [d.name for d in detectors]
        assert names == [
            'learning',
            'strike_counter',
            'dirty_session',
            'project_memory_backup',
            'obi_state_backup',
            'capability_claim',
            'drift_detector_stop',
            'drift_nag_stop',
        ]
        for d in detectors:
            assert isinstance(d, Detector)

    def test_run_stop_detectors_invokes_dirty_session(self):
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            calibration={'safety': {'dirty_session_nag': True}},
        )
        files = [{"status": " M", "path": "x.py"}]
        with patch("core.dirty_session.get_dirty_file_list", return_value=files), \
             patch("core.dirty_session.format_dirty_session_nag",
                   return_value="[Git] 1 file(s)"), \
             patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_stop_detectors(ctx)
        assert result is not None
        assert "[Git]" in result

    def test_run_stop_detectors_all_quiet_returns_none(self):
        """When every detector produces no output, result is None."""
        ctx = DetectorContext(
            hook_point='stop',
            cwd='/repo',
            start_time=time.time(),
            calibration={'safety': {'dirty_session_nag': False}},
        )
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 0}), \
             patch("core.drift_nag.compute_drift_delta",
                   return_value=(0, 0)):
            result = run_stop_detectors(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Registry wiring: run_session_start_detectors
# ---------------------------------------------------------------------------

class TestRunSessionStartDetectors:
    def test_build_session_start_detectors_returns_two(self):
        detectors = _build_session_start_detectors()
        assert len(detectors) == 2
        names = [d.name for d in detectors]
        assert names == [
            'correction_retriever',
            'drift_detector_session',
        ]
        for d in detectors:
            assert isinstance(d, Detector)

    def test_run_session_start_detectors_invokes_correction_retriever(self):
        ctx = DetectorContext(
            hook_point='session_start',
            cwd='/repo',
            start_time=time.time(),
            calibration={'safety': {'auto_inject_sources': True}},
            task_type='review',
            task_text='fix the bug',
        )
        with patch("core.correction_retriever.get_correction_injection",
                   return_value="## Relevant Past Corrections") as mock_inj, \
             patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_session_start_detectors(ctx)
        mock_inj.assert_called_once_with('review', 'fix the bug')
        assert result is not None
        assert "Relevant Past Corrections" in result

    def test_run_session_start_detectors_all_quiet_returns_none(self):
        """When no task text + no maintenance due, nothing fires."""
        ctx = DetectorContext(
            hook_point='session_start',
            cwd='/repo',
            start_time=time.time(),
            calibration={'safety': {'auto_inject_sources': True}},
            task_text='',
            maintenance_due=False,
        )
        with patch("core.hook_logger.HookTimer") as MockTimer:
            MockTimer.return_value.__enter__ = MagicMock(return_value=MockTimer.return_value)
            MockTimer.return_value.__exit__ = MagicMock(return_value=False)
            result = run_session_start_detectors(ctx)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

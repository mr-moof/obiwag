"""Tests for dual-invocation + session_start-only detectors (OPT-15 increment 6).

Covers:
  - DriftDetectorStop        (stop-side drift message)
  - DriftNagStop             (stop-side drift-nag delta message)
  - DriftDetectorSessionStart (session_start drift + baseline save)
  - CorrectionRetrieverDetector (session_start correction injection)
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.detector_registry import Detector, DetectorContext  # noqa: E402
from core.detectors.correction_retriever_detector import CorrectionRetrieverDetector  # noqa: E402
from core.detectors.drift_detector_session_start import DriftDetectorSessionStart  # noqa: E402
from core.detectors.drift_detector_stop import DriftDetectorStop  # noqa: E402
from core.detectors.drift_nag_stop import DriftNagStop  # noqa: E402


def _make_ctx(**overrides) -> DetectorContext:
    defaults = dict(
        hook_point='stop',
        cwd='/repo',
        start_time=time.time(),
        calibration={},
    )
    defaults.update(overrides)
    return DetectorContext(**defaults)


# ---------------------------------------------------------------------------
# DriftDetectorStop
# ---------------------------------------------------------------------------

class TestDriftDetectorStop:
    def test_name(self):
        assert DriftDetectorStop().name == 'drift_detector_stop'

    def test_hook_point(self):
        assert DriftDetectorStop().hook_point == 'stop'

    def test_always_enabled(self):
        assert DriftDetectorStop().is_enabled({}) is True
        assert DriftDetectorStop().is_enabled({'safety': {'anything': False}}) is True

    def test_run_returns_message_when_drift_detected(self):
        ctx = _make_ctx()
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 5}):
            result = DriftDetectorStop().run(ctx)
        assert result is not None
        assert "5 file(s) changed" in result
        assert "/obi-collect" in result

    def test_run_returns_none_when_no_drift(self):
        ctx = _make_ctx()
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 0}):
            result = DriftDetectorStop().run(ctx)
        assert result is None

    def test_output_matches_legacy_format(self):
        """Output must match the exact format from stop_advisories._drift_message."""
        ctx = _make_ctx()
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 3}):
            result = DriftDetectorStop().run(ctx)
        assert result == (
            "\n[Drift] 3 file(s) changed. "
            "Run /obi-collect to sync back to source."
        )


# ---------------------------------------------------------------------------
# DriftNagStop
# ---------------------------------------------------------------------------

class TestDriftNagStop:
    def test_name(self):
        assert DriftNagStop().name == 'drift_nag_stop'

    def test_hook_point(self):
        assert DriftNagStop().hook_point == 'stop'

    def test_always_enabled(self):
        assert DriftNagStop().is_enabled({}) is True

    def test_run_returns_nag_when_delta_positive(self):
        ctx = _make_ctx()
        with patch("core.drift_nag.compute_drift_delta",
                   return_value=(2, 5)), \
             patch("core.drift_nag.format_drift_nag",
                   return_value="[Obi] 2 new drifted files this session. Run /obi-collect to sync back."):
            result = DriftNagStop().run(ctx)
        assert result is not None
        assert "[Obi]" in result
        assert "2 new drifted files" in result

    def test_run_returns_none_when_delta_zero(self):
        ctx = _make_ctx()
        with patch("core.drift_nag.compute_drift_delta",
                   return_value=(0, 3)):
            result = DriftNagStop().run(ctx)
        assert result is None

    def test_run_returns_none_when_delta_negative(self):
        ctx = _make_ctx()
        with patch("core.drift_nag.compute_drift_delta",
                   return_value=(-1, 2)):
            result = DriftNagStop().run(ctx)
        assert result is None

    def test_run_returns_none_when_delta_is_none(self):
        ctx = _make_ctx()
        with patch("core.drift_nag.compute_drift_delta",
                   return_value=(None, None)):
            result = DriftNagStop().run(ctx)
        assert result is None


# ---------------------------------------------------------------------------
# DriftDetectorSessionStart
# ---------------------------------------------------------------------------

class TestDriftDetectorSessionStart:
    def test_name(self):
        assert DriftDetectorSessionStart().name == 'drift_detector_session'

    def test_hook_point(self):
        assert DriftDetectorSessionStart().hook_point == 'session_start'

    def test_always_enabled(self):
        assert DriftDetectorSessionStart().is_enabled({}) is True

    def test_run_skips_when_maintenance_not_due(self):
        ctx = _make_ctx(hook_point='session_start', maintenance_due=False)
        with patch("core.drift_detector.detect_drift") as mock_drift:
            result = DriftDetectorSessionStart().run(ctx)
        mock_drift.assert_not_called()
        assert result is None

    def test_run_returns_message_when_drift_detected(self):
        ctx = _make_ctx(hook_point='session_start', maintenance_due=True)
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 3}), \
             patch("core.drift_nag.save_drift_baseline") as mock_save:
            result = DriftDetectorSessionStart().run(ctx)
        assert result is not None
        assert "3 file(s) differ from source repo" in result
        assert "/obi-collect" in result
        mock_save.assert_called_once_with(3)

    def test_run_returns_none_when_no_drift(self):
        ctx = _make_ctx(hook_point='session_start', maintenance_due=True)
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 0}), \
             patch("core.drift_nag.save_drift_baseline") as mock_save:
            result = DriftDetectorSessionStart().run(ctx)
        assert result is None
        # Baseline should still be saved even when total is 0.
        mock_save.assert_called_once_with(0)

    def test_run_saves_baseline_even_when_no_drift(self):
        """Baseline must be saved regardless of drift count, so the
        session-end nag can compute a correct delta."""
        ctx = _make_ctx(hook_point='session_start', maintenance_due=True)
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 0}), \
             patch("core.drift_nag.save_drift_baseline") as mock_save:
            DriftDetectorSessionStart().run(ctx)
        mock_save.assert_called_once_with(0)

    def test_output_matches_legacy_format(self):
        """Output format must match the original session_start._handle code."""
        ctx = _make_ctx(hook_point='session_start', maintenance_due=True)
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 7}), \
             patch("core.drift_nag.save_drift_baseline"):
            result = DriftDetectorSessionStart().run(ctx)
        assert result == (
            "\n[Drift] 7 file(s) differ from source repo. "
            "Run /obi-collect to sync back."
        )

    def test_baseline_save_failure_does_not_propagate(self):
        """If save_drift_baseline raises, the detector still returns
        the drift message (matches original log_swallowed behavior)."""
        ctx = _make_ctx(hook_point='session_start', maintenance_due=True)
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 2}), \
             patch("core.drift_nag.save_drift_baseline",
                   side_effect=OSError("disk full")), \
             patch("core.hook_logger.log_swallowed") as mock_log:
            result = DriftDetectorSessionStart().run(ctx)
        assert result is not None
        assert "2 file(s) differ" in result
        mock_log.assert_called_once()


# ---------------------------------------------------------------------------
# CorrectionRetrieverDetector
# ---------------------------------------------------------------------------

class TestCorrectionRetrieverDetector:
    def test_name(self):
        assert CorrectionRetrieverDetector().name == 'correction_retriever'

    def test_hook_point(self):
        assert CorrectionRetrieverDetector().hook_point == 'session_start'

    def test_enabled_when_auto_inject_sources_true(self):
        cal = {'safety': {'auto_inject_sources': True}}
        assert CorrectionRetrieverDetector().is_enabled(cal) is True

    def test_disabled_when_auto_inject_sources_false(self):
        cal = {'safety': {'auto_inject_sources': False}}
        assert CorrectionRetrieverDetector().is_enabled(cal) is False

    def test_enabled_by_default_when_key_missing(self):
        assert CorrectionRetrieverDetector().is_enabled({}) is True

    def test_run_returns_injection_when_corrections_found(self):
        ctx = _make_ctx(
            hook_point='session_start',
            task_type='review',
            task_text='fix the cloud API call',
        )
        with patch("core.correction_retriever.get_correction_injection",
                   return_value="## Relevant Past Corrections\n\nsome corrections") as mock_inj:
            result = CorrectionRetrieverDetector().run(ctx)
        mock_inj.assert_called_once_with('review', 'fix the cloud API call')
        assert result is not None
        assert "Relevant Past Corrections" in result

    def test_run_returns_none_when_no_corrections(self):
        ctx = _make_ctx(
            hook_point='session_start',
            task_type='review',
            task_text='fix the bug',
        )
        with patch("core.correction_retriever.get_correction_injection",
                   return_value=None):
            result = CorrectionRetrieverDetector().run(ctx)
        assert result is None

    def test_run_returns_none_when_no_task_text(self):
        ctx = _make_ctx(
            hook_point='session_start',
            task_type='',
            task_text='',
        )
        with patch("core.correction_retriever.get_correction_injection") as mock_inj:
            result = CorrectionRetrieverDetector().run(ctx)
        mock_inj.assert_not_called()
        assert result is None

    def test_output_has_leading_newline(self):
        """Output must have a leading newline to match original welcome_parts assembly."""
        ctx = _make_ctx(
            hook_point='session_start',
            task_type='author',
            task_text='write a test',
        )
        with patch("core.correction_retriever.get_correction_injection",
                   return_value="## Corrections"):
            result = CorrectionRetrieverDetector().run(ctx)
        assert result.startswith("\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

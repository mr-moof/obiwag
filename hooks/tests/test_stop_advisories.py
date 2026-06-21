"""Tests for hooks/core/stop_advisories.py — shutdown advisories cluster."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core import stop_advisories  # noqa: E402


class TestRunShutdownAdvisories:
    """Behaviour contract for the orchestrator: each advisory is independent
    and best-effort; the orchestrator chains their outputs but never lets one
    failure propagate."""

    def test_no_advisories_returns_none(self):
        assert stop_advisories.run_shutdown_advisories(True, "") is None

    def test_backups_not_run_in_advisories(self):
        """project_memory_backup and obi_state_backup migrated to detector
        registry (OPT-15 increments 3-4); verify they no longer fire from
        run_shutdown_advisories."""
        with patch("core.project_memory.backup_project_memory") as pm_backup, \
             patch("core.obi_state_backup.backup_obi_state") as state_backup:
            stop_advisories.run_shutdown_advisories(True, "")
        pm_backup.assert_not_called()
        state_backup.assert_not_called()

    def test_dirty_session_not_run_in_advisories(self):
        """dirty_session migrated to detector registry (OPT-15); verify it
        no longer fires from run_shutdown_advisories even when enabled."""
        with patch("core.dirty_session.get_dirty_file_list",
                   return_value=[{"status": " M", "path": "file.py"}]) as mock_dirty:
            result = stop_advisories.run_shutdown_advisories(False, "")
        mock_dirty.assert_not_called()
        if result is not None:
            assert "dirty_session_nag" not in result

    def test_drift_not_run_in_advisories(self):
        """drift_detector and drift_nag migrated to detector registry
        (OPT-15 increment 6); verify they no longer fire from
        run_shutdown_advisories."""
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 5}) as mock_drift, \
             patch("core.drift_nag.compute_drift_delta",
                   return_value=(3, 5)) as mock_nag:
            result = stop_advisories.run_shutdown_advisories(True, "")
        mock_drift.assert_not_called()
        mock_nag.assert_not_called()
        assert result is None

    def test_dirty_session_helper_still_usable_directly(self):
        """The _dirty_session_nag_message helper is still defined (used by
        tests / direct callers) even though run_shutdown_advisories no
        longer invokes it (OPT-15 migration)."""
        with patch("core.dirty_session.get_dirty_file_list", return_value=[]):
            assert stop_advisories._dirty_session_nag_message("/anywhere") is None

    def test_capability_claim_not_run_in_advisories(self):
        """capability_claim migrated to detector registry (OPT-15 increment 5);
        verify it no longer fires from run_shutdown_advisories."""
        with patch("core.capability_claim_detector.detect_unverified_denials",
                   return_value=["some denial"]) as mock_detect:
            result = stop_advisories.run_shutdown_advisories(True, "transcript")
        mock_detect.assert_not_called()
        if result is not None:
            assert "capability_claim" not in result


class TestIndividualAdvisories:
    """Direct tests on the underscore-prefixed helpers -- locks each advisory's
    contract so future changes can't silently strip a system message."""

    def test_drift_message_returns_none_without_drift(self):
        with patch("core.drift_detector.detect_drift",
                   return_value={'total_drifted': 0}):
            assert stop_advisories._drift_message() is None

    def test_drift_message_swallows_exception(self):
        with patch("core.drift_detector.detect_drift",
                   side_effect=RuntimeError("boom")):
            assert stop_advisories._drift_message() is None

    def test_drift_nag_returns_none_when_delta_is_none(self):
        with patch("core.drift_nag.compute_drift_delta",
                   return_value=(None, None)):
            assert stop_advisories._drift_nag_message() is None

    def test_dirty_session_returns_none_for_clean_tree(self):
        with patch("core.dirty_session.get_dirty_file_list", return_value=[]):
            assert stop_advisories._dirty_session_nag_message("/anywhere") is None

    def test_capability_claim_returns_none_when_no_flags(self):
        with patch("core.calibration.get_detector_config",
                   return_value={'firings_total': 0,
                                 'review_after_sessions': 9999,
                                 'review_after_firings': 9999}), \
             patch("core.calibration.load_calibration",
                   return_value={'performance': {'total_sessions': 0}}), \
             patch("core.transcript_analyzer.get_recent_assistant_turns",
                   return_value=[]), \
             patch("core.capability_claim_detector.detect_unverified_denials",
                   return_value=[]):
            assert stop_advisories._capability_claim_message("") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

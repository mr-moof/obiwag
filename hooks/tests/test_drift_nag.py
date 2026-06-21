"""Tests for drift_nag — the session-end drift-grew advisory."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core import drift_nag  # noqa: E402


class TestSaveLoadBaseline:
    def test_round_trip(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            assert drift_nag.save_drift_baseline(7) is True
            assert drift_nag._load_baseline() == 7

    def test_load_missing_returns_none(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            assert drift_nag._load_baseline() is None

    def test_load_corrupt_returns_none(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        baseline_path.write_text("{not json", encoding="utf-8")
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            assert drift_nag._load_baseline() is None

    def test_load_wrong_type_returns_none(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        baseline_path.write_text(json.dumps({"total_drifted": "not-an-int"}), encoding="utf-8")
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            assert drift_nag._load_baseline() is None


class TestComputeDriftDelta:
    def test_drift_increased(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            drift_nag.save_drift_baseline(3)
            with patch("core.drift_detector.detect_drift", return_value={"total_drifted": 5}):
                delta, current = drift_nag.compute_drift_delta()
        assert delta == 2
        assert current == 5

    def test_drift_unchanged(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            drift_nag.save_drift_baseline(4)
            with patch("core.drift_detector.detect_drift", return_value={"total_drifted": 4}):
                delta, current = drift_nag.compute_drift_delta()
        assert delta == 0
        assert current == 4

    def test_drift_decreased(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            drift_nag.save_drift_baseline(6)
            with patch("core.drift_detector.detect_drift", return_value={"total_drifted": 2}):
                delta, current = drift_nag.compute_drift_delta()
        assert delta == -4
        assert current == 2

    def test_missing_baseline_returns_none_delta(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            with patch("core.drift_detector.detect_drift", return_value={"total_drifted": 7}):
                delta, current = drift_nag.compute_drift_delta()
        assert delta is None
        assert current == 7

    def test_detect_drift_failure_returns_none_pair(self, tmp_path):
        baseline_path = tmp_path / "drift-baseline.json"
        with patch("core.drift_nag.get_baseline_path", return_value=str(baseline_path)):
            drift_nag.save_drift_baseline(3)
            with patch("core.drift_detector.detect_drift", side_effect=RuntimeError("boom")):
                delta, current = drift_nag.compute_drift_delta()
        assert delta is None
        assert current is None


class TestFormatDriftNag:
    def test_singular(self):
        msg = drift_nag.format_drift_nag(1)
        assert "1 new drifted file " in msg
        assert "/obi-collect" in msg

    def test_plural(self):
        msg = drift_nag.format_drift_nag(3)
        assert "3 new drifted files " in msg
        assert "/obi-collect" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

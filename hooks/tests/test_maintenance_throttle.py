"""Tests for the SessionStart maintenance throttle (OPT-06 #179).

The drift hash + GC sweeps + MEMORY.md check should run at most once per 24h so
session-open latency does not pay for them every start. OBI_FORCE_MAINTENANCE=1
bypasses the throttle for testing / healthcheck.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import core.session_state as ss  # noqa: E402
import session_start as sstart  # noqa: E402


class _StubTimer:
    def set_input_summary(self, *_a, **_k):
        pass

    def set_output_summary(self, *_a, **_k):
        pass


class TestMaintenanceDue:
    def test_due_when_marker_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        monkeypatch.delenv('OBI_FORCE_MAINTENANCE', raising=False)
        assert sstart._maintenance_due() is True

    def test_not_due_when_recent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        monkeypatch.delenv('OBI_FORCE_MAINTENANCE', raising=False)
        (tmp_path / "last-maintenance.json").write_text(
            json.dumps({"last_run_iso": datetime.now().isoformat()}), encoding="utf-8")
        assert sstart._maintenance_due() is False

    def test_due_when_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        monkeypatch.delenv('OBI_FORCE_MAINTENANCE', raising=False)
        (tmp_path / "last-maintenance.json").write_text(
            json.dumps({"last_run_iso": (datetime.now() - timedelta(hours=25)).isoformat()}),
            encoding="utf-8")
        assert sstart._maintenance_due() is True

    def test_force_env_bypasses_throttle(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        (tmp_path / "last-maintenance.json").write_text(
            json.dumps({"last_run_iso": datetime.now().isoformat()}), encoding="utf-8")
        monkeypatch.setenv('OBI_FORCE_MAINTENANCE', '1')
        assert sstart._maintenance_due() is True

    def test_record_then_not_due(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        monkeypatch.delenv('OBI_FORCE_MAINTENANCE', raising=False)
        assert sstart._maintenance_due() is True
        sstart._record_maintenance_run()
        assert sstart._maintenance_due() is False


class TestHandleThrottles:
    """Acceptance: a second SessionStart within 24h skips all maintenance."""

    def test_second_start_within_24h_skips_maintenance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        monkeypatch.delenv('OBI_FORCE_MAINTENANCE', raising=False)

        # Spy on the two remaining non-destructive maintenance functions.
        gc = MagicMock()
        mem = MagicMock()
        monkeypatch.setattr(sstart, '_gc_maintenance', gc)
        monkeypatch.setattr(sstart, '_check_memory_md_size', mem)
        # Keep the rest of _handle hermetic + fast.
        monkeypatch.setattr(sstart, '_resolve_project_working_dir', lambda: None)
        import core.drift_detector as dd
        import core.drift_nag as dn
        monkeypatch.setattr(dd, 'detect_drift', lambda **_k: {'total_drifted': 0})
        monkeypatch.setattr(dn, 'save_drift_baseline', lambda *_a, **_k: None)

        timer = _StubTimer()
        sstart._handle({}, timer)   # first start -> maintenance runs + records marker
        sstart._handle({}, timer)   # second start within 24h -> throttled

        assert gc.call_count == 1, "GC ran more than once within 24h"
        assert mem.call_count == 1
        assert os.path.isfile(str(tmp_path / "last-maintenance.json"))

    def test_force_env_runs_every_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        monkeypatch.setenv('OBI_FORCE_MAINTENANCE', '1')

        gc = MagicMock()
        monkeypatch.setattr(sstart, '_gc_maintenance', gc)
        monkeypatch.setattr(sstart, '_check_memory_md_size', MagicMock())
        monkeypatch.setattr(sstart, '_resolve_project_working_dir', lambda: None)
        import core.drift_detector as dd
        import core.drift_nag as dn
        monkeypatch.setattr(dd, 'detect_drift', lambda **_k: {'total_drifted': 0})
        monkeypatch.setattr(dn, 'save_drift_baseline', lambda *_a, **_k: None)

        timer = _StubTimer()
        sstart._handle({}, timer)
        sstart._handle({}, timer)
        assert gc.call_count == 2, "OBI_FORCE_MAINTENANCE should bypass the throttle every start"

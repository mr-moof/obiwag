"""Tests for the current-session sentinel (OPT-04 #177).

The sentinel pins PostToolUse / PreCompact to one session_id when their input
lacks one, so a session crossing an hour boundary no longer forks its state
into a second hour-bucket-seeded file.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import core.session_state as ss


class _StubTimer:
    def set_input_summary(self, *_a, **_k):
        pass

    def set_output_summary(self, *_a, **_k):
        pass


class TestCurrentSessionHelpers:
    def test_write_read_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        ss.write_current_session("sess-AAAA")
        assert ss.read_current_session() == "sess-AAAA"
        data = json.loads((tmp_path / "current-session.json").read_text(encoding="utf-8"))
        assert data["session_id"] == "sess-AAAA"
        assert "started_at" in data
        assert "pid" in data

    def test_read_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        assert ss.read_current_session() is None

    def test_read_none_when_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        stale = {
            "session_id": "old",
            "started_at": (datetime.now() - timedelta(hours=25)).isoformat(),
            "pid": 1,
        }
        (tmp_path / "current-session.json").write_text(json.dumps(stale), encoding="utf-8")
        assert ss.read_current_session(max_age_hours=24) is None

    def test_read_none_when_malformed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        (tmp_path / "current-session.json").write_text("{not json", encoding="utf-8")
        assert ss.read_current_session() is None

    def test_clear_removes_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(tmp_path))
        ss.write_current_session("x")
        assert os.path.isfile(str(tmp_path / "current-session.json"))
        ss.clear_current_session()
        assert not os.path.isfile(str(tmp_path / "current-session.json"))
        # Idempotent — clearing a missing sentinel must not raise.
        ss.clear_current_session()


class TestHourBoundaryNoFork:
    """Acceptance: two PostToolUse calls with no input session_id accumulate
    tool_count in ONE state file (the sentinel), not two hour-bucket files."""

    def test_two_tool_calls_share_one_state_file(self, tmp_path, monkeypatch):
        import post_tool_use

        state_dir = tmp_path / "state"
        active_dir = tmp_path / "active"
        monkeypatch.setattr(ss, 'get_state_dir', lambda: str(state_dir))
        monkeypatch.setattr(ss, 'get_active_sessions_path', lambda: str(active_dir))

        # SessionStart wrote the sentinel.
        ss.write_current_session("sess-PINNED")

        import core.calibration as cal
        monkeypatch.setattr(cal, 'is_safety_enabled', lambda *_a, **_k: True)

        timer = _StubTimer()
        evt = {"tool_name": "Read", "tool_input": {"file_path": "x.py"}}  # no session_id
        post_tool_use._handle(evt, timer)
        post_tool_use._handle(evt, timer)

        files = sorted(f for f in os.listdir(str(active_dir)) if f.endswith(".json"))
        assert files == ["sess-PINNED.json"], f"expected one pinned state file, got {files}"
        state = json.loads((active_dir / "sess-PINNED.json").read_text(encoding="utf-8"))
        assert state["tool_count"] == 2

    def test_sentinel_is_hour_independent(self):
        """Contrast: the old hour-bucket seed forks across a boundary; the
        sentinel does not depend on the hour at all."""
        from core.memory_reader import generate_session_id
        id_1359 = generate_session_id("2026-06-15-13")
        id_1401 = generate_session_id("2026-06-15-14")
        assert id_1359 != id_1401  # the fork the sentinel prevents

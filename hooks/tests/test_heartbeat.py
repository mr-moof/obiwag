"""Tests for OPT-22 heartbeat touch in post_tool_use."""

import json
import os
import sys
from pathlib import Path


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import post_tool_use  # noqa: E402  (sys.path is set up above)


def _reset_heartbeat_cache():
    """Reset the module-level run-id cache between tests."""
    post_tool_use._RUN_ID = None
    post_tool_use._RUN_ID_LOADED = False


class TestHeartbeatTouch:
    """Heartbeat file is written during autonomous runs and skipped otherwise."""

    def test_writes_json_when_run_id_exists(self, tmp_path, monkeypatch):
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)

        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "run-id.txt").write_text("20260617T161502Z")

        post_tool_use._touch_heartbeat("Edit")

        hb_path = state_dir / "heartbeat-20260617T161502Z.json"
        assert hb_path.exists()
        data = json.loads(hb_path.read_text())
        assert data["tool"] == "Edit"
        assert "ts" in data

    def test_noop_when_run_id_absent(self, tmp_path, monkeypatch):
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)

        # No .obi/state/run-id.txt — heartbeat must be a silent no-op
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)

        post_tool_use._touch_heartbeat("Bash")

        # No heartbeat file should be created
        heartbeat_files = list(state_dir.glob("heartbeat-*.json"))
        assert heartbeat_files == []

    def test_correct_filename_includes_run_id(self, tmp_path, monkeypatch):
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)

        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "run-id.txt").write_text("20260101T000000Z")

        post_tool_use._touch_heartbeat("Read")

        assert (state_dir / "heartbeat-20260101T000000Z.json").exists()
        # No other heartbeat files
        all_hb = list(state_dir.glob("heartbeat-*.json"))
        assert len(all_hb) == 1

    def test_cache_prevents_rereading_run_id(self, tmp_path, monkeypatch):
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)
        # Two touches in a row would otherwise be collapsed by the throttle.
        monkeypatch.setattr(post_tool_use, "_HEARTBEAT_MIN_INTERVAL_SEC", 0.0)

        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "run-id.txt").write_text("cached-run")

        post_tool_use._touch_heartbeat("Read")
        assert (state_dir / "heartbeat-cached-run.json").exists()

        # Remove run-id.txt — cached value should still be used
        (state_dir / "run-id.txt").unlink()
        post_tool_use._touch_heartbeat("Write")

        data = json.loads((state_dir / "heartbeat-cached-run.json").read_text())
        assert data["tool"] == "Write"

    def test_noop_when_obi_dir_missing(self, tmp_path, monkeypatch):
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)

        # No .obi directory at all — must not raise
        post_tool_use._touch_heartbeat("Bash")

    def test_overwrites_previous_heartbeat(self, tmp_path, monkeypatch):
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(post_tool_use, "_HEARTBEAT_MIN_INTERVAL_SEC", 0.0)

        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "run-id.txt").write_text("overwrite-test")

        post_tool_use._touch_heartbeat("Read")
        first = json.loads((state_dir / "heartbeat-overwrite-test.json").read_text())

        post_tool_use._touch_heartbeat("Edit")
        second = json.loads((state_dir / "heartbeat-overwrite-test.json").read_text())

        assert first["tool"] == "Read"
        assert second["tool"] == "Edit"

    def test_throttles_writes_within_the_interval(self, tmp_path, monkeypatch):
        """PostToolUse fires on every tool call; the heartbeat is only a
        staleness signal, so a fresh file must NOT be rewritten each time."""
        _reset_heartbeat_cache()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(post_tool_use, "_HEARTBEAT_MIN_INTERVAL_SEC", 60.0)

        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "run-id.txt").write_text("throttle-test")
        hb_path = state_dir / "heartbeat-throttle-test.json"

        post_tool_use._touch_heartbeat("Read")
        post_tool_use._touch_heartbeat("Edit")

        assert json.loads(hb_path.read_text())["tool"] == "Read"

        # A stale heartbeat (mtime pushed into the past) is rewritten.
        stale = os.path.getmtime(hb_path) - 120
        os.utime(hb_path, (stale, stale))
        post_tool_use._touch_heartbeat("Edit")
        assert json.loads(hb_path.read_text())["tool"] == "Edit"

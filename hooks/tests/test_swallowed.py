"""Unit tests for log_swallowed / get_swallowed_stats (OPT-05 #178).

These cover the swallowed-exception logger that replaced bare
`except Exception: pass` handlers across hooks/: it must record one JSONL line
per swallow, flatten/truncate the message, NEVER raise, and aggregate by
component within a time window.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.hook_logger import (  # noqa: E402
    get_swallowed_log_path,
    get_swallowed_stats,
    log_swallowed,
)


class TestLogSwallowed:
    """log_swallowed appends a structured line and never raises."""

    def test_writes_one_jsonl_line(self, tmp_path):
        sw = tmp_path / "swallowed-errors.jsonl"
        with patch('core.hook_logger.get_swallowed_log_path', return_value=sw):
            log_swallowed("gc_maintenance", ValueError("boom"))
        lines = sw.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["component"] == "gc_maintenance"
        assert rec["error_class"] == "ValueError"
        assert rec["message"] == "boom"
        assert "timestamp" in rec

    def test_flattens_multiline_message(self, tmp_path):
        sw = tmp_path / "sw.jsonl"
        with patch('core.hook_logger.get_swallowed_log_path', return_value=sw):
            log_swallowed("comp", RuntimeError("line1\nline2\rline3"))
        rec = json.loads(sw.read_text(encoding="utf-8").strip())
        assert "\n" not in rec["message"]
        assert "\r" not in rec["message"]

    def test_truncates_long_message(self, tmp_path):
        sw = tmp_path / "sw.jsonl"
        with patch('core.hook_logger.get_swallowed_log_path', return_value=sw):
            log_swallowed("comp", ValueError("x" * 1000))
        rec = json.loads(sw.read_text(encoding="utf-8").strip())
        assert len(rec["message"]) <= 300

    def test_never_raises_when_logging_fails(self):
        # If path resolution itself raises, log_swallowed must still not raise —
        # that is the entire contract of the bare-except sites it replaces.
        with patch('core.hook_logger.get_swallowed_log_path',
                   side_effect=OSError("disk gone")):
            log_swallowed("comp", ValueError("orig"))  # must not raise


class TestGetSwallowedStats:
    """get_swallowed_stats aggregates by component within the window."""

    @staticmethod
    def _write(path, records):
        with open(path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_empty_when_missing(self, tmp_path):
        sw = tmp_path / "missing.jsonl"
        with patch('core.hook_logger.get_swallowed_log_path', return_value=sw):
            stats = get_swallowed_stats(hours=168)
        assert stats == {'window_hours': 168, 'total': 0, 'by_component': {}}

    def test_aggregates_by_component_within_window(self, tmp_path):
        sw = tmp_path / "sw.jsonl"
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=1)).isoformat().replace('+00:00', 'Z')
        old = (now - timedelta(hours=200)).isoformat().replace('+00:00', 'Z')
        self._write(sw, [
            {"timestamp": recent, "component": "gc_maintenance", "error_class": "E", "message": "a"},
            {"timestamp": recent, "component": "gc_maintenance", "error_class": "E", "message": "b"},
            {"timestamp": recent, "component": "drift_detect", "error_class": "E", "message": "c"},
            {"timestamp": old, "component": "gc_maintenance", "error_class": "E", "message": "old"},
        ])
        with patch('core.hook_logger.get_swallowed_log_path', return_value=sw):
            stats = get_swallowed_stats(hours=168)
        assert stats['total'] == 3  # the 200h-old entry is outside the 7d window
        assert stats['by_component'] == {'gc_maintenance': 2, 'drift_detect': 1}

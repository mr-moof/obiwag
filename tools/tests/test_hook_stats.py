"""Tests for tools/hook_stats.py — the hook observability CLI."""

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the tools/ directory importable.
TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLS_DIR))

# hook_stats prepends hooks/ to sys.path on import; do the same here so the
# test process can import core.hook_logger directly for fixture setup.
HOOKS_DIR = TOOLS_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import hook_stats  # noqa: E402


def _write_log(path: Path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _run_cli(argv, log_path):
    """Run hook_stats.main() with the given argv and a redirected log path.

    Returns (exit_code, stdout_text).
    """
    buf = io.StringIO()
    with patch("core.hook_logger.get_hook_log_path", return_value=log_path), \
         redirect_stdout(buf):
        code = hook_stats.main(argv)
    return code, buf.getvalue()


class TestHookStatsCli:
    def test_no_data_table_output(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        code, out = _run_cli([], log_path)
        assert code == 0
        assert "No hook executions" in out

    def test_no_data_json_output(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        code, out = _run_cli(["--json"], log_path)
        assert code == 0
        payload = json.loads(out)
        assert payload["total_executions"] == 0
        assert payload["window_hours"] == 24

    def test_table_output_with_data(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        now = datetime.now(timezone.utc).isoformat()
        _write_log(log_path, [
            {"timestamp": now, "hook_name": "Stop",         "status": "success", "duration_ms": 50},
            {"timestamp": now, "hook_name": "PreToolUse",   "status": "success", "duration_ms": 10},
            {"timestamp": now, "hook_name": "PreToolUse",   "status": "error",   "duration_ms": 12},
        ])
        code, out = _run_cli(["--hours", "24"], log_path)
        assert code == 0
        assert "Total executions: 3" in out
        assert "errors: 1" in out
        assert "Stop" in out
        assert "PreToolUse" in out
        assert "Top" in out  # Slowest hooks section

    def test_json_output_shape(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        now = datetime.now(timezone.utc).isoformat()
        _write_log(log_path, [
            {"timestamp": now, "hook_name": "A", "status": "success", "duration_ms": 100},
        ])
        code, out = _run_cli(["--json"], log_path)
        assert code == 0
        payload = json.loads(out)
        for key in (
            "window_hours", "total_executions", "by_hook",
            "by_hook_avg_ms", "by_hook_errors", "errors",
            "avg_duration_ms", "p50_duration_ms", "p95_duration_ms",
            "slowest_hooks",
        ):
            assert key in payload, f"missing key {key}"

    def test_top_argument_caps_slowest_list(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {"timestamp": now, "hook_name": f"H{i}", "status": "success", "duration_ms": i * 10}
            for i in range(8)
        ]
        _write_log(log_path, entries)
        code, out = _run_cli(["--top", "2", "--json"], log_path)
        assert code == 0
        payload = json.loads(out)
        assert len(payload["slowest_hooks"]) == 2

    def test_hours_window(self, tmp_path):
        log_path = tmp_path / "log.jsonl"
        from datetime import timedelta
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _write_log(log_path, [
            {"timestamp": old,    "hook_name": "OLD", "status": "success", "duration_ms": 1},
            {"timestamp": recent, "hook_name": "NEW", "status": "success", "duration_ms": 2},
        ])
        # Default 24h excludes the 48h-old entry.
        code, out = _run_cli(["--json"], log_path)
        payload = json.loads(out)
        assert payload["by_hook"] == {"NEW": 1}

        # Wider window includes both.
        code, out = _run_cli(["--hours", "72", "--json"], log_path)
        payload = json.loads(out)
        assert set(payload["by_hook"].keys()) == {"OLD", "NEW"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

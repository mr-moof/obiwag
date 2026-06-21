"""Unit tests for hook_logger module."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.hook_logger import (
    HookTimer,
    _percentile,
    _rotate_log_if_needed,
    get_hook_log_path,
    get_hook_stats,
    get_hook_stats_extended,
    get_recent_hook_logs,
)


class TestGetHookLogPath:
    """Tests for get_hook_log_path function."""

    def test_returns_path_object(self, tmp_path):
        """Test that function returns a Path object."""
        with patch('core.hook_logger.Path.home', return_value=tmp_path):
            path = get_hook_log_path()
            assert isinstance(path, Path)

    def test_path_in_obi_directory(self, tmp_path):
        """Test that log file is in .obi directory."""
        with patch('core.hook_logger.Path.home', return_value=tmp_path):
            path = get_hook_log_path()
            assert '.obi' in str(path)
            assert path.name == 'hook-execution-log.jsonl'

    def test_creates_obi_directory(self, tmp_path):
        """Test that .obi directory is created if missing."""
        with patch('core.hook_logger.Path.home', return_value=tmp_path):
            path = get_hook_log_path()
            assert path.parent.exists()


class TestHookTimer:
    """Tests for HookTimer context manager."""

    def test_basic_timing(self, tmp_path):
        """Test that timer measures duration."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            with HookTimer("TestHook") as timer:
                time.sleep(0.01)  # 10ms

            assert timer.duration_ms >= 10
            assert timer.status == 'success'

    def test_sets_input_summary(self, tmp_path):
        """Test setting input summary."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            with HookTimer("TestHook") as timer:
                timer.set_input_summary("test input")

            assert timer.input_summary == "test input"

    def test_sets_output_summary(self, tmp_path):
        """Test setting output summary."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            with HookTimer("TestHook") as timer:
                timer.set_output_summary("test output")

            assert timer.output_summary == "test output"

    def test_truncates_long_summaries(self, tmp_path):
        """Test that summaries are truncated to 200 chars."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            long_text = "a" * 300
            with HookTimer("TestHook") as timer:
                timer.set_input_summary(long_text)
                timer.set_output_summary(long_text)

            assert len(timer.input_summary) == 200
            assert len(timer.output_summary) == 200

    def test_captures_exception(self, tmp_path):
        """Test that exceptions set error status."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            try:
                with HookTimer("TestHook") as timer:
                    raise ValueError("Test error")
            except ValueError:
                pass

            assert timer.status == 'error'
            assert 'Test error' in timer.error_message

    def test_manual_error_setting(self, tmp_path):
        """Test manual error setting."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            with HookTimer("TestHook") as timer:
                timer.set_error("Manual error")

            assert timer.status == 'error'
            assert timer.error_message == "Manual error"

    def test_manual_error_preserved_when_systemexit_propagates(self, tmp_path):
        """Manual set_error message must survive __exit__ on SystemExit.

        run_hook calls timer.set_error(real_msg) and then routes through
        respond_with_error, which raises SystemExit(0). Without the
        preservation guard, __exit__ would overwrite error_message with
        ``str(SystemExit(0))`` == '0', destroying the diagnostic.
        """
        log_file = tmp_path / 'test.jsonl'
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            try:
                with HookTimer("TestHook") as timer:
                    timer.set_error("the real error message")
                    raise SystemExit(0)
            except SystemExit:
                pass
        assert timer.status == 'error'
        assert timer.error_message == "the real error message"

    def test_writes_log_entry(self, tmp_path):
        """Test that log entry is written to file."""
        log_file = tmp_path / 'test.jsonl'
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            with HookTimer("TestHook") as timer:
                timer.set_input_summary("test input")

        assert log_file.exists()
        with open(log_file) as f:
            entry = json.loads(f.readline())
            assert entry['hook_name'] == 'TestHook'
            assert entry['status'] == 'success'
            assert 'duration_ms' in entry
            assert 'timestamp' in entry

    def test_does_not_suppress_exceptions(self, tmp_path):
        """Test that exceptions are not suppressed."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'test.jsonl'):
            with pytest.raises(ValueError):
                with HookTimer("TestHook"):
                    raise ValueError("Test error")


class TestGetRecentHookLogs:
    """Tests for get_recent_hook_logs function."""

    def test_returns_empty_for_missing_file(self, tmp_path):
        """Test that empty list is returned if log file doesn't exist."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'missing.jsonl'):
            result = get_recent_hook_logs()
            assert result == []

    def test_reads_log_entries(self, tmp_path):
        """Test reading log entries from file."""
        log_file = tmp_path / 'test.jsonl'
        entries = [
            {'timestamp': '2024-01-01T00:00:00Z', 'hook_name': 'Test1', 'status': 'success', 'duration_ms': 10},
            {'timestamp': '2024-01-01T00:01:00Z', 'hook_name': 'Test2', 'status': 'success', 'duration_ms': 20},
        ]
        with open(log_file, 'w') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')

        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            result = get_recent_hook_logs()
            assert len(result) == 2
            # Most recent first
            assert result[0]['hook_name'] == 'Test2'
            assert result[1]['hook_name'] == 'Test1'

    def test_respects_limit(self, tmp_path):
        """Test that limit parameter is respected."""
        log_file = tmp_path / 'test.jsonl'
        with open(log_file, 'w') as f:
            for i in range(10):
                entry = {'timestamp': f'2024-01-01T00:0{i}:00Z', 'hook_name': f'Test{i}', 'status': 'success'}
                f.write(json.dumps(entry) + '\n')

        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            result = get_recent_hook_logs(limit=3)
            assert len(result) == 3

    def test_skips_malformed_lines(self, tmp_path):
        """Test that malformed JSON lines are skipped."""
        log_file = tmp_path / 'test.jsonl'
        with open(log_file, 'w') as f:
            f.write('not valid json\n')
            f.write('{"hook_name": "Valid", "status": "success", "timestamp": "2024-01-01T00:00:00Z"}\n')
            f.write('{incomplete\n')

        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            result = get_recent_hook_logs()
            assert len(result) == 1
            assert result[0]['hook_name'] == 'Valid'


class TestGetHookStats:
    """Tests for get_hook_stats function."""

    def test_returns_empty_stats_for_no_logs(self, tmp_path):
        """Test that empty stats are returned when no logs exist."""
        with patch('core.hook_logger.get_hook_log_path', return_value=tmp_path / 'missing.jsonl'):
            result = get_hook_stats()
            assert result['total_executions'] == 0
            assert result['by_hook'] == {}
            assert result['errors'] == 0
            assert result['avg_duration_ms'] == 0.0

    def test_calculates_stats(self, tmp_path):
        """Test that stats are calculated correctly."""
        log_file = tmp_path / 'test.jsonl'
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        entries = [
            {'timestamp': now, 'hook_name': 'SessionStart', 'status': 'success', 'duration_ms': 100},
            {'timestamp': now, 'hook_name': 'SessionStart', 'status': 'success', 'duration_ms': 200},
            {'timestamp': now, 'hook_name': 'PreToolUse', 'status': 'error', 'duration_ms': 50},
        ]
        with open(log_file, 'w') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')

        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            result = get_hook_stats(hours=24)
            assert result['total_executions'] == 3
            assert result['by_hook']['SessionStart'] == 2
            assert result['by_hook']['PreToolUse'] == 1
            assert result['errors'] == 1


class TestRotateLogIfNeeded:
    """Tests for _rotate_log_if_needed function."""

    def test_no_rotation_for_small_file(self, tmp_path):
        """Test that small files are not rotated."""
        log_file = tmp_path / 'test.jsonl'
        log_file.write_text('small content\n')

        _rotate_log_if_needed(log_file, max_size_bytes=1000)

        assert log_file.read_text() == 'small content\n'

    def test_rotates_large_file(self, tmp_path):
        """Test that large files are rotated."""
        log_file = tmp_path / 'test.jsonl'
        # Create a file with 100 lines
        lines = [f'line {i}\n' for i in range(100)]
        log_file.write_text(''.join(lines))

        original_size = log_file.stat().st_size
        # Set max size to trigger rotation
        _rotate_log_if_needed(log_file, max_size_bytes=100)

        # File should be smaller after rotation
        new_size = log_file.stat().st_size
        assert new_size < original_size

    def test_handles_missing_file(self, tmp_path):
        """Test that missing files don't cause errors."""
        log_file = tmp_path / 'missing.jsonl'
        # Should not raise
        _rotate_log_if_needed(log_file)


class TestPercentile:
    """Tests for the _percentile helper used by get_hook_stats_extended."""

    def test_empty_returns_zero(self):
        assert _percentile([], 95) == 0.0

    def test_single_value_returns_value(self):
        assert _percentile([42.0], 95) == 42.0

    def test_p50_picks_middle(self):
        # Odd-length input — p50 is unambiguous across rounding conventions.
        # For 5-element [10,20,30,40,50], every definition agrees on 30.
        assert _percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50) == 30.0

    def test_p95_picks_near_top(self):
        values = [float(i) for i in range(100)]  # 0..99
        result = _percentile(values, 95)
        assert 90.0 <= result <= 99.0

    def test_p100_returns_max(self):
        values = [float(i) for i in range(10)]
        assert _percentile(values, 100) == 9.0


class TestGetHookStatsExtended:
    """Tests for get_hook_stats_extended (Phase 4)."""

    def _entries_to_log(self, log_file, entries):
        with open(log_file, 'w', encoding='utf-8') as f:
            for entry in entries:
                f.write(json.dumps(entry) + '\n')

    def test_empty_log_returns_zeroed_shape(self, tmp_path):
        with patch('core.hook_logger.get_hook_log_path',
                   return_value=tmp_path / 'missing.jsonl'):
            stats = get_hook_stats_extended(hours=24)
        assert stats['total_executions'] == 0
        assert stats['errors'] == 0
        assert stats['avg_duration_ms'] == 0.0
        assert stats['p50_duration_ms'] == 0.0
        assert stats['p95_duration_ms'] == 0.0
        assert stats['by_hook'] == {}
        assert stats['by_hook_avg_ms'] == {}
        assert stats['by_hook_errors'] == {}
        assert stats['slowest_hooks'] == []
        assert stats['window_hours'] == 24

    def test_corrupt_lines_dont_crash(self, tmp_path):
        log_file = tmp_path / 'log.jsonl'
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write('garbage\n')
            f.write(json.dumps({
                'timestamp': now, 'hook_name': 'A',
                'status': 'success', 'duration_ms': 50,
            }) + '\n')
            f.write('{incomplete\n')
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            stats = get_hook_stats_extended(hours=24)
        assert stats['total_executions'] == 1
        assert stats['by_hook'] == {'A': 1}
        assert stats['by_hook_avg_ms'] == {'A': 50.0}

    def test_aggregates_durations_and_errors(self, tmp_path):
        log_file = tmp_path / 'log.jsonl'
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {'timestamp': now, 'hook_name': 'A', 'status': 'success', 'duration_ms': 10},
            {'timestamp': now, 'hook_name': 'A', 'status': 'success', 'duration_ms': 20},
            {'timestamp': now, 'hook_name': 'A', 'status': 'error',   'duration_ms': 30},
            {'timestamp': now, 'hook_name': 'B', 'status': 'success', 'duration_ms': 100},
        ]
        self._entries_to_log(log_file, entries)
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            stats = get_hook_stats_extended(hours=24)
        assert stats['total_executions'] == 4
        assert stats['errors'] == 1
        assert stats['by_hook'] == {'A': 3, 'B': 1}
        assert stats['by_hook_errors'] == {'A': 1}
        assert stats['by_hook_avg_ms'] == {'A': 20.0, 'B': 100.0}
        assert stats['avg_duration_ms'] == 40.0
        # B is slower on average — must be at the top of slowest_hooks.
        assert stats['slowest_hooks'][0]['hook_name'] == 'B'

    def test_p95_calculation(self, tmp_path):
        log_file = tmp_path / 'log.jsonl'
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        # 100 entries with duration 0..99 → p95 ≈ 95.
        entries = [
            {
                'timestamp': now,
                'hook_name': 'X',
                'status': 'success',
                'duration_ms': i,
            }
            for i in range(100)
        ]
        self._entries_to_log(log_file, entries)
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            stats = get_hook_stats_extended(hours=24)
        assert stats['p95_duration_ms'] >= 90
        assert stats['p95_duration_ms'] <= 99
        assert stats['p50_duration_ms'] >= 45
        assert stats['p50_duration_ms'] <= 55

    def test_window_filters_old_entries(self, tmp_path):
        log_file = tmp_path / 'log.jsonl'
        from datetime import datetime, timedelta, timezone
        recent = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        entries = [
            {'timestamp': old, 'hook_name': 'OLD', 'status': 'success', 'duration_ms': 1},
            {'timestamp': recent, 'hook_name': 'NEW', 'status': 'success', 'duration_ms': 2},
        ]
        self._entries_to_log(log_file, entries)
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            stats = get_hook_stats_extended(hours=24)
        assert stats['by_hook'] == {'NEW': 1}

    def test_slowest_n_limit(self, tmp_path):
        log_file = tmp_path / 'log.jsonl'
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {'timestamp': now, 'hook_name': f'H{i}', 'status': 'success', 'duration_ms': i * 10}
            for i in range(10)
        ]
        self._entries_to_log(log_file, entries)
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            stats = get_hook_stats_extended(hours=24, slowest_n=3)
        assert len(stats['slowest_hooks']) == 3
        # Highest duration_ms first.
        assert stats['slowest_hooks'][0]['hook_name'] == 'H9'
        assert stats['slowest_hooks'][1]['hook_name'] == 'H8'
        assert stats['slowest_hooks'][2]['hook_name'] == 'H7'

    def test_non_numeric_duration_treated_as_zero(self, tmp_path):
        log_file = tmp_path / 'log.jsonl'
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        entries = [
            {'timestamp': now, 'hook_name': 'X', 'status': 'success', 'duration_ms': 'oops'},
            {'timestamp': now, 'hook_name': 'X', 'status': 'success', 'duration_ms': 100},
        ]
        self._entries_to_log(log_file, entries)
        with patch('core.hook_logger.get_hook_log_path', return_value=log_file):
            stats = get_hook_stats_extended(hours=24)
        # Bad duration falls back to 0; avg over [0, 100] = 50.
        assert stats['avg_duration_ms'] == 50.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

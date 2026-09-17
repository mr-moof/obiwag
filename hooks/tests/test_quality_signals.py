"""Unit tests for quality_signals module."""

import json
import sys
from pathlib import Path
from unittest.mock import patch


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.quality_signals import (
    _normalize_path,
    check_file_size,
    find_edits_without_read,
    find_file_thrashing,
    compute_quality_signals,
    write_quality_signals_jsonl,
)


class TestNormalizePath:
    """Tests for _normalize_path helper."""

    def test_backslash_to_forward(self):
        assert _normalize_path("C:\\Users\\foo\\bar.py") == "c:/users/foo/bar.py"

    def test_lowercase(self):
        assert _normalize_path("C:/Users/Foo/BAR.py") == "c:/users/foo/bar.py"

    def test_already_normalized(self):
        assert _normalize_path("c:/users/foo/bar.py") == "c:/users/foo/bar.py"


class TestFindEditsWithoutRead:
    """Tests for find_edits_without_read."""

    def test_edit_after_read_no_violation(self):
        tools = [
            {"tool": "Read", "path": "C:/foo/bar.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "2"},
        ]
        assert find_edits_without_read(tools) == []

    def test_edit_without_read_violation(self):
        tools = [
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "1"},
        ]
        result = find_edits_without_read(tools)
        assert result == ["C:/foo/bar.py"]

    def test_write_without_read_no_violation(self):
        """Write (new file creation) without prior Read is not a violation."""
        tools = [
            {"tool": "Write", "path": "C:/foo/new.py", "timestamp": "1"},
        ]
        result = find_edits_without_read(tools)
        assert result == []

    def test_empty_tools_list(self):
        assert find_edits_without_read([]) == []

    def test_none_path_skipped(self):
        tools = [
            {"tool": "Edit", "path": None, "timestamp": "1"},
            {"tool": "Bash", "path": None, "timestamp": "2"},
        ]
        assert find_edits_without_read(tools) == []

    def test_missing_path_key_skipped(self):
        tools = [
            {"tool": "Edit", "timestamp": "1"},
        ]
        assert find_edits_without_read(tools) == []

    def test_case_insensitive_path_match(self):
        tools = [
            {"tool": "Read", "path": "C:/Foo/BAR.py", "timestamp": "1"},
            {"tool": "Edit", "path": "c:/foo/bar.py", "timestamp": "2"},
        ]
        assert find_edits_without_read(tools) == []

    def test_backslash_forward_slash_match(self):
        tools = [
            {"tool": "Read", "path": "C:\\foo\\bar.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "2"},
        ]
        assert find_edits_without_read(tools) == []

    def test_non_tracked_tools_ignored(self):
        tools = [
            {"tool": "Bash", "path": "/tmp/script.sh", "timestamp": "1"},
            {"tool": "Grep", "path": "C:/foo/", "timestamp": "2"},
            {"tool": "Glob", "path": "C:/foo/", "timestamp": "3"},
        ]
        assert find_edits_without_read(tools) == []

    def test_iterative_edits_after_read_no_repeated_flags(self):
        """Read then 5 Edits of the same file: zero violations."""
        tools = [
            {"tool": "Read", "path": "C:/foo/bar.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "2"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "3"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "4"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "5"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "6"},
        ]
        assert find_edits_without_read(tools) == []

    def test_first_edit_without_read_flags_once_then_silent(self):
        """N Edits without prior Read flag only the first (Edit seeds seen-set)."""
        tools = [
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "2"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "3"},
        ]
        result = find_edits_without_read(tools)
        assert len(result) == 1
        assert result[0] == "C:/foo/bar.py"

    def test_edit_after_write_no_violation(self):
        """Write seeds the seen-set so a later Edit doesn't flag."""
        tools = [
            {"tool": "Write", "path": "C:/foo/new.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo/new.py", "timestamp": "2"},
            {"tool": "Edit", "path": "C:/foo/new.py", "timestamp": "3"},
        ]
        assert find_edits_without_read(tools) == []


class TestFindFileThrashing:
    """Tests for find_file_thrashing."""

    def test_three_reads_triggers(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "3"},
        ]
        result = find_file_thrashing(tools)
        assert "c:/foo.py" in result
        assert result["c:/foo.py"] == 3

    def test_below_threshold_not_flagged(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
        ]
        assert find_file_thrashing(tools) == {}

    def test_empty_tools_list(self):
        assert find_file_thrashing([]) == {}

    def test_none_path_skipped(self):
        tools = [
            {"tool": "Read", "path": None, "timestamp": "1"},
            {"tool": "Read", "path": None, "timestamp": "2"},
            {"tool": "Read", "path": None, "timestamp": "3"},
        ]
        assert find_file_thrashing(tools) == {}

    def test_only_read_tool_counted(self):
        """Edit/Write/Grep/Glob don't count toward thrashing."""
        tools = [
            {"tool": "Edit", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo.py", "timestamp": "2"},
            {"tool": "Edit", "path": "C:/foo.py", "timestamp": "3"},
            {"tool": "Grep", "path": "C:/foo.py", "timestamp": "4"},
        ]
        assert find_file_thrashing(tools) == {}

    def test_custom_threshold(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
        ]
        result = find_file_thrashing(tools, threshold=2)
        assert "c:/foo.py" in result

    def test_path_normalization(self):
        tools = [
            {"tool": "Read", "path": "C:\\Foo\\BAR.py", "timestamp": "1"},
            {"tool": "Read", "path": "c:/foo/bar.py", "timestamp": "2"},
            {"tool": "Read", "path": "C:/Foo/bar.py", "timestamp": "3"},
        ]
        result = find_file_thrashing(tools)
        assert "c:/foo/bar.py" in result
        assert result["c:/foo/bar.py"] == 3


class TestCheckFileSize:
    """Tests for check_file_size."""

    def test_large_file_returns_count(self, tmp_path):
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"line {i}" for i in range(450)))
        assert check_file_size(str(f), threshold=400) == 450

    def test_small_file_returns_none(self, tmp_path):
        f = tmp_path / "small.py"
        f.write_text("\n".join(f"line {i}" for i in range(100)))
        assert check_file_size(str(f), threshold=400) is None

    def test_exactly_at_threshold_returns_none(self, tmp_path):
        f = tmp_path / "exact.py"
        f.write_text("\n".join(f"line {i}" for i in range(400)))
        assert check_file_size(str(f), threshold=400) is None

    def test_one_over_threshold_returns_count(self, tmp_path):
        f = tmp_path / "over.py"
        f.write_text("\n".join(f"line {i}" for i in range(401)))
        assert check_file_size(str(f), threshold=400) == 401

    def test_nonexistent_file_returns_none(self):
        assert check_file_size("C:/nonexistent/path/file.py") is None

    def test_custom_threshold(self, tmp_path):
        f = tmp_path / "medium.py"
        f.write_text("\n".join(f"line {i}" for i in range(250)))
        assert check_file_size(str(f), threshold=200) == 250
        assert check_file_size(str(f), threshold=300) is None

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        assert check_file_size(str(f), threshold=400) is None


class TestComputeQualitySignals:
    """Tests for compute_quality_signals."""

    def test_signals_when_all_clear(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo.py", "timestamp": "2"},
        ]
        result = compute_quality_signals(tools, [])
        assert "quality_clean" not in result  # computed at JSONL write time
        assert result["edit_without_read"] == []
        assert result["file_thrashing"] == {}
        assert result["over_building_corrections"] == 0

    def test_edit_without_read_flagged(self):
        tools = [
            {"tool": "Edit", "path": "C:/foo.py", "timestamp": "1"},
        ]
        result = compute_quality_signals(tools, [])
        assert len(result["edit_without_read"]) == 1

    def test_write_without_read_not_flagged(self):
        """Write (file creation) should not trigger edit_without_read."""
        tools = [
            {"tool": "Write", "path": "C:/foo/new.py", "timestamp": "1"},
        ]
        result = compute_quality_signals(tools, [])
        assert result["edit_without_read"] == []

    def test_thrashing_flagged(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "3"},
        ]
        result = compute_quality_signals(tools, [])
        assert len(result["file_thrashing"]) > 0

    def test_over_building_counted(self):
        corrections = [{"type": "over_building", "context": "test"}]
        result = compute_quality_signals([], corrections)
        assert result["over_building_corrections"] == 1

    def test_non_over_building_corrections_ignored(self):
        corrections = [
            {"type": "api_hallucination", "context": "test"},
            {"type": "logic_error", "context": "test"},
        ]
        result = compute_quality_signals([], corrections)
        assert result["over_building_corrections"] == 0

    def test_empty_inputs(self):
        result = compute_quality_signals([], [])
        assert result["edit_without_read"] == []
        assert result["file_thrashing"] == {}
        assert result["over_building_corrections"] == 0


class TestWriteQualitySignalsJsonl:
    """Tests for write_quality_signals_jsonl."""

    def test_writes_valid_jsonl(self, tmp_path):
        log_path = tmp_path / "session-quality.jsonl"
        signals = {
            "edit_without_read": [],
            "file_thrashing": {},
            "over_building_corrections": 0,
        }
        with patch("core.quality_signals.Path.home", return_value=tmp_path), \
             patch("core.hook_logger._rotate_log_if_needed") as mock_rotate:
            # Patch home to use tmp_path, but we need .claude/.obi subdir
            obi_dir = tmp_path / ".claude" / ".obi"
            obi_dir.mkdir(parents=True)

            write_quality_signals_jsonl("test-session", "unknown", signals)

            written_path = obi_dir / "session-quality.jsonl"
            assert written_path.exists()
            line = written_path.read_text().strip()
            entry = json.loads(line)
            assert entry["session_id"] == "test-session"
            assert entry["task_type"] == "unknown"
            assert entry["quality_clean"] is True
            assert "timestamp" in entry

    def test_calls_rotate(self, tmp_path):
        signals = {"edit_without_read": [], "file_thrashing": {}, "over_building_corrections": 0}
        with patch("core.quality_signals.Path.home", return_value=tmp_path), \
             patch("core.hook_logger.append_rotating_jsonl") as mock_append:
            obi_dir = tmp_path / ".claude" / ".obi"
            obi_dir.mkdir(parents=True)

            write_quality_signals_jsonl("test", "unknown", signals)
            mock_append.assert_called_once()
            assert mock_append.call_args.kwargs["max_size_bytes"] == 500_000

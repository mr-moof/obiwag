"""Tests for post_tool_use in-session quality alerts."""

import sys
import tempfile
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from post_tool_use import compute_in_session_alerts


class TestEditWithoutReadAlert:
    """Alert when editing a file that wasn't Read first."""

    def test_edit_after_read_no_alert(self):
        tools = [
            {"tool": "Read", "path": "C:/foo/bar.py", "timestamp": "1"},
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "2"},
        ]
        alerts = compute_in_session_alerts(tools, "Edit", "C:/foo/bar.py")
        edit_alerts = [a for a in alerts if "without reading" in a]
        assert len(edit_alerts) == 0

    def test_edit_without_read_fires_alert(self):
        tools = [
            {"tool": "Edit", "path": "C:/foo/bar.py", "timestamp": "1"},
        ]
        alerts = compute_in_session_alerts(tools, "Edit", "C:/foo/bar.py")
        edit_alerts = [a for a in alerts if "without reading" in a]
        assert len(edit_alerts) == 1
        assert "C:/foo/bar.py" in edit_alerts[0]

    def test_read_tool_does_not_trigger_edit_alert(self):
        tools = [
            {"tool": "Read", "path": "C:/foo/bar.py", "timestamp": "1"},
        ]
        alerts = compute_in_session_alerts(tools, "Read", "C:/foo/bar.py")
        edit_alerts = [a for a in alerts if "without reading" in a]
        assert len(edit_alerts) == 0

    def test_case_insensitive_path_match(self):
        tools = [
            {"tool": "Read", "path": "C:/Foo/BAR.py", "timestamp": "1"},
            {"tool": "Edit", "path": "c:/foo/bar.py", "timestamp": "2"},
        ]
        alerts = compute_in_session_alerts(tools, "Edit", "c:/foo/bar.py")
        edit_alerts = [a for a in alerts if "without reading" in a]
        assert len(edit_alerts) == 0


class TestFileThrashingAlert:
    """Alert when reading the same file 3+ times."""

    def test_third_read_fires_alert(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "3"},
        ]
        alerts = compute_in_session_alerts(tools, "Read", "C:/foo.py")
        thrash_alerts = [a for a in alerts if "times" in a]
        assert len(thrash_alerts) == 1
        assert "3 times" in thrash_alerts[0]

    def test_second_read_no_alert(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
        ]
        alerts = compute_in_session_alerts(tools, "Read", "C:/foo.py")
        thrash_alerts = [a for a in alerts if "times" in a]
        assert len(thrash_alerts) == 0

    def test_edit_does_not_trigger_thrashing(self):
        tools = [
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "1"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "2"},
            {"tool": "Read", "path": "C:/foo.py", "timestamp": "3"},
        ]
        # Edit of the same file shouldn't trigger the thrashing alert
        alerts = compute_in_session_alerts(tools, "Edit", "C:/foo.py")
        thrash_alerts = [a for a in alerts if "times" in a]
        assert len(thrash_alerts) == 0


class TestFileSizeAlert:
    """Alert when a file exceeds 400 lines."""

    def test_large_file_fires_alert(self):
        # Create a temp file with >400 lines
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            for i in range(450):
                f.write(f"line {i}\n")
            path = f.name

        try:
            tools = [{"tool": "Read", "path": path, "timestamp": "1"}]
            alerts = compute_in_session_alerts(tools, "Read", path)
            size_alerts = [a for a in alerts if "lines" in a and "threshold" in a]
            assert len(size_alerts) == 1
            assert "450" in size_alerts[0]
        finally:
            Path(path).unlink(missing_ok=True)

    def test_small_file_no_alert(self):
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            for i in range(100):
                f.write(f"line {i}\n")
            path = f.name

        try:
            tools = [{"tool": "Read", "path": path, "timestamp": "1"}]
            alerts = compute_in_session_alerts(tools, "Read", path)
            size_alerts = [a for a in alerts if "threshold" in a]
            assert len(size_alerts) == 0
        finally:
            Path(path).unlink(missing_ok=True)

    def test_missing_file_no_alert(self):
        tools = [{"tool": "Read", "path": "C:/nonexistent/file.py", "timestamp": "1"}]
        alerts = compute_in_session_alerts(tools, "Read", "C:/nonexistent/file.py")
        size_alerts = [a for a in alerts if "threshold" in a]
        assert len(size_alerts) == 0


class TestFileSizeRedAlert:
    """Red alert when a file exceeds the hard 600-line limit."""

    def test_red_file_fires_red_alert(self):
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.py', delete=False, encoding='utf-8'
        ) as f:
            for i in range(650):
                f.write(f"line {i}\n")
            path = f.name

        try:
            tools = [{"tool": "Read", "path": path, "timestamp": "1"}]
            alerts = compute_in_session_alerts(tools, "Read", path)
            red_alerts = [a for a in alerts if "[Quality RED]" in a]
            yellow_alerts = [a for a in alerts if a.startswith("[Quality]") and "threshold" in a]
            assert len(red_alerts) == 1
            assert "650" in red_alerts[0]
            assert "hard limit" in red_alerts[0]
            assert len(yellow_alerts) == 0
        finally:
            Path(path).unlink(missing_ok=True)


class TestFileSizeIgnoreGlobs:
    """ignore_globs skips size alerts for matching paths."""

    def test_vendor_path_suppresses_alert(self, monkeypatch):
        fake_calibration = {
            'quality_thresholds': {
                'file_size_yellow': 400,
                'file_size_red': 600,
                'ignore_globs': ['**/vendor/**'],
            }
        }
        import post_tool_use
        monkeypatch.setattr(
            "core.calibration.load_calibration",
            lambda: fake_calibration,
        )

        with tempfile.TemporaryDirectory() as tmp:
            vendor_dir = Path(tmp) / "vendor"
            vendor_dir.mkdir()
            path = vendor_dir / "big.py"
            with open(path, 'w', encoding='utf-8') as f:
                for i in range(700):
                    f.write(f"line {i}\n")

            tools = [{"tool": "Read", "path": str(path), "timestamp": "1"}]
            alerts = post_tool_use.compute_in_session_alerts(
                tools, "Read", str(path)
            )
            size_alerts = [a for a in alerts if "threshold" in a or "hard limit" in a]
            assert len(size_alerts) == 0


class TestNoPathNoAlerts:
    """No alerts when tool_path is None."""

    def test_none_path_no_alerts(self):
        alerts = compute_in_session_alerts([], "Bash", None)
        assert alerts == []

    def test_empty_tools_no_alerts(self):
        alerts = compute_in_session_alerts([], "Read", "C:/foo.py")
        # No thrashing, no edit-without-read (not an Edit), file may not exist
        thrash_alerts = [a for a in alerts if "times" in a]
        edit_alerts = [a for a in alerts if "without reading" in a]
        assert len(thrash_alerts) == 0
        assert len(edit_alerts) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

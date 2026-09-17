"""Tests for dirty_session — the session-end uncommitted-changes advisory."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.dirty_session import (  # noqa: E402
    is_repo_dirty,
    get_dirty_file_list,
    format_dirty_session_nag,
)


class TestIsRepoDirty:
    def test_clean_repo_returns_false(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr=""
            )
            assert is_repo_dirty("/fake/repo") is False

    def test_dirty_tracked_modification_returns_true(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=" M file.py\n",
                stderr=""
            )
            assert is_repo_dirty("/fake/repo") is True

    def test_invokes_git_status_porcelain_with_repo_path(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            is_repo_dirty("/fake/repo")
            args, kwargs = mock_run.call_args
            assert args[0] == ["git", "-C", "/fake/repo", "status", "--porcelain"]
            assert kwargs.get("timeout") == 2.5
            assert kwargs.get("capture_output") is True
            assert kwargs.get("text") is True

    def test_staged_change_returns_true(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="A  file.py\n",
                stderr=""
            )
            assert is_repo_dirty("/fake/repo") is True

    def test_untracked_file_returns_true(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="?? newfile.txt\n",
                stderr=""
            )
            assert is_repo_dirty("/fake/repo") is True

    def test_git_failure_returns_false(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=128,
                stdout="",
                stderr="not a git repo"
            )
            assert is_repo_dirty("/fake/repo") is False

    def test_subprocess_exception_returns_false(self):
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            assert is_repo_dirty("/fake/repo") is False


class TestGetDirtyFileList:
    def test_parses_modified_file(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=" M file.py\n",
                stderr=""
            )
            result = get_dirty_file_list("/fake/repo")
            assert result == [{"status": " M", "path": "file.py"}]

    def test_parses_untracked(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="?? newfile.txt\n",
                stderr=""
            )
            result = get_dirty_file_list("/fake/repo")
            assert result == [{"status": "??", "path": "newfile.txt"}]

    def test_parses_rename(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="R  old.py -> new.py\n",
                stderr=""
            )
            result = get_dirty_file_list("/fake/repo")
            assert result == [{"status": "R ", "path": "new.py"}]

    def test_parses_mixed(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=" M file.py\nA  added.py\n?? untracked.txt\n",
                stderr=""
            )
            result = get_dirty_file_list("/fake/repo")
            assert len(result) == 3
            assert result[0] == {"status": " M", "path": "file.py"}
            assert result[1] == {"status": "A ", "path": "added.py"}
            assert result[2] == {"status": "??", "path": "untracked.txt"}

    def test_empty_output_returns_empty_list(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr=""
            )
            result = get_dirty_file_list("/fake/repo")
            assert result == []

    def test_failure_returns_empty_list(self):
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=128,
                stdout="",
                stderr="fatal: not a git repository"
            )
            result = get_dirty_file_list("/fake/repo")
            assert result == []


class TestFormatDirtySessionNag:
    def test_single_file_enumerated(self):
        file_list = [{"status": " M", "path": "file.py"}]
        msg = format_dirty_session_nag(file_list)
        assert "1 file(s)" in msg
        assert "file.py" in msg

    def test_five_files_enumerated(self):
        file_list = [
            {"status": " M", "path": f"file{i}.py"}
            for i in range(5)
        ]
        msg = format_dirty_session_nag(file_list)
        assert "5 file(s)" in msg
        for i in range(5):
            assert f"file{i}.py" in msg

    def test_six_files_count_only(self):
        file_list = [
            {"status": " M", "path": f"file{i}.py"}
            for i in range(6)
        ]
        msg = format_dirty_session_nag(file_list)
        assert "6 file(s)" in msg
        # Individual files should NOT be listed
        assert "file0.py" not in msg

    def test_empty_list_returns_empty_string(self):
        assert format_dirty_session_nag([]) == ""

    def test_message_has_git_prefix(self):
        file_list = [{"status": " M", "path": "file.py"}]
        msg = format_dirty_session_nag(file_list)
        assert msg.startswith("[Git]")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

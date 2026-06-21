"""Unit tests for git_sync module."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.git_sync import (
    PushStrategy,
    SyncResult,
    FileChange,
    get_obiwag_repo_path,
    get_push_strategy,
    run_git,
    configure_git_identity,
    apply_learning_to_file,
    format_commit_message,
    format_sync_result,
    GIT_EMAIL,
    GIT_NAME,
)


class TestPushStrategy:
    """Tests for push strategy determination."""

    def test_personal_repo_direct_to_master(self):
        """Test personal repos use direct to master."""
        url = "https://github.com/user/obiwag-agents.git"
        result = get_push_strategy(url)
        assert result == PushStrategy.DIRECT_TO_MASTER

    def test_shared_repo_branch_and_pr(self):
        """Test shared repos use branch and PR."""
        url = "https://github.com/team/shared-project.git"
        result = get_push_strategy(url)
        assert result == PushStrategy.BRANCH_AND_PR

    def test_none_url_defaults_to_branch(self):
        """Test None URL defaults to branch strategy (safe)."""
        result = get_push_strategy(None)
        assert result == PushStrategy.BRANCH_AND_PR

    def test_case_insensitive_matching(self):
        """Test URL matching is case insensitive."""
        url = "https://github.com/USER/project.git"
        result = get_push_strategy(url)
        assert result == PushStrategy.DIRECT_TO_MASTER


class TestGetObiwagRepoPath:
    """Tests for get_obiwag_repo_path function."""

    def test_finds_repo_in_source(self, tmp_path, monkeypatch):
        """Test finding repo in source directory."""
        # Create mock repo structure
        repo_path = tmp_path / "source" / "obiwag-agents"
        (repo_path / ".git").mkdir(parents=True)

        # Clear OBIWAG_SOURCE so the search_roots fallback is exercised.
        monkeypatch.delenv("OBIWAG_SOURCE", raising=False)

        result = get_obiwag_repo_path(search_roots=[repo_path])
        assert result == repo_path

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        """Test returns None when repo not found."""
        monkeypatch.delenv("OBIWAG_SOURCE", raising=False)

        result = get_obiwag_repo_path(search_roots=[tmp_path / "nonexistent"])
        assert result is None


class TestRunGit:
    """Tests for run_git function."""

    def test_successful_command(self, tmp_path):
        """Test running a successful git command."""
        # Create a minimal git repo
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="output",
                stderr=""
            )
            code, stdout, stderr = run_git(repo, "status")
            assert code == 0
            assert stdout == "output"
            mock_run.assert_called_once()

    def test_failed_command(self, tmp_path):
        """Test handling a failed git command."""
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error message"
            )
            code, stdout, stderr = run_git(repo, "invalid")
            assert code == 1
            assert stderr == "error message"

    def test_timeout_handling(self, tmp_path):
        """Test handling command timeout."""
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("git", 60)
            code, stdout, stderr = run_git(repo, "long-command")
            assert code == -1
            assert "timed out" in stderr.lower()


class TestConfigureGitIdentity:
    """Tests for configure_git_identity function."""

    def test_configures_email_and_name(self, tmp_path):
        """Test that email and name are configured."""
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch('core.git_sync.run_git') as mock_run:
            mock_run.return_value = (0, "", "")
            result = configure_git_identity(repo)
            assert result is True
            # Should be called twice: once for email, once for name
            assert mock_run.call_count == 2

    def test_returns_false_on_error(self, tmp_path):
        """Test returns False when git config fails."""
        repo = tmp_path / "repo"
        repo.mkdir()

        with patch('core.git_sync.run_git') as mock_run:
            mock_run.return_value = (1, "", "error")
            result = configure_git_identity(repo)
            assert result is False


class TestApplyLearningToFile:
    """Tests for apply_learning_to_file function."""

    def test_create_new_file(self, tmp_path):
        """Test creating a new file."""
        result = apply_learning_to_file(
            tmp_path,
            "new_file.md",
            "# Content",
            "create"
        )
        assert result is True
        assert (tmp_path / "new_file.md").exists()
        assert (tmp_path / "new_file.md").read_text() == "# Content"

    def test_append_to_existing(self, tmp_path):
        """Test appending to existing file."""
        existing = tmp_path / "existing.md"
        existing.write_text("# Original")

        result = apply_learning_to_file(
            tmp_path,
            "existing.md",
            "New content",
            "append"
        )
        assert result is True
        content = existing.read_text()
        assert "# Original" in content
        assert "New content" in content

    def test_replace_content(self, tmp_path):
        """Test replacing file content."""
        existing = tmp_path / "replace.md"
        existing.write_text("Old content")

        result = apply_learning_to_file(
            tmp_path,
            "replace.md",
            "New content",
            "replace"
        )
        assert result is True
        assert existing.read_text() == "New content"

    def test_creates_parent_directories(self, tmp_path):
        """Test creating parent directories."""
        result = apply_learning_to_file(
            tmp_path,
            "nested/dir/file.md",
            "Content",
            "create"
        )
        assert result is True
        assert (tmp_path / "nested" / "dir" / "file.md").exists()


class TestFormatCommitMessage:
    """Tests for format_commit_message function."""

    def test_single_file_message(self):
        """Test commit message for single file."""
        changes = [
            FileChange("docs/gotchas.md", "content", "append")
        ]
        result = format_commit_message(changes)
        assert "gotchas.md" in result
        assert "docs:" in result

    def test_multiple_files_message(self):
        """Test commit message for multiple files."""
        changes = [
            FileChange("docs/gotchas.md", "content", "append"),
            FileChange("docs/example.md", "content", "append"),
        ]
        result = format_commit_message(changes)
        assert "2 files" in result

    def test_includes_session_summary(self):
        """Test session summary is included."""
        changes = [FileChange("test.md", "content", "append")]
        result = format_commit_message(changes, "example doc session")
        assert "example" in result


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_successful_result(self):
        """Test creating successful result."""
        result = SyncResult(
            success=True,
            files_changed=["file1.md", "file2.md"],
            commit_hash="abc123",
            branch="master",
            deployed=True
        )
        assert result.success is True
        assert len(result.files_changed) == 2
        assert result.deployed is True

    def test_failed_result(self):
        """Test creating failed result."""
        result = SyncResult(
            success=False,
            error_message="Push failed"
        )
        assert result.success is False
        assert result.error_message == "Push failed"


class TestFormatSyncResult:
    """Tests for format_sync_result function."""

    def test_format_success(self):
        """Test formatting successful result."""
        result = SyncResult(
            success=True,
            files_changed=["test.md"],
            commit_hash="abc123",
            branch="master",
            deployed=True
        )
        formatted = format_sync_result(result)
        assert "successfully" in formatted.lower()
        assert "abc123" in formatted
        assert "test.md" in formatted
        assert "deployed" in formatted.lower()

    def test_format_failure(self):
        """Test formatting failed result."""
        result = SyncResult(
            success=False,
            error_message="Authentication failed"
        )
        formatted = format_sync_result(result)
        assert "failed" in formatted.lower()
        assert "Authentication failed" in formatted

    def test_format_branch_and_pr(self):
        """Test formatting result with branch/PR strategy."""
        result = SyncResult(
            success=True,
            files_changed=["test.md"],
            commit_hash="abc123",
            branch="learning/20240115-120000",
            push_strategy=PushStrategy.BRANCH_AND_PR
        )
        formatted = format_sync_result(result)
        assert "PR" in formatted
        assert "learning/20240115-120000" in formatted


class TestFileChange:
    """Tests for FileChange dataclass."""

    def test_default_operation(self):
        """Test default operation is append."""
        change = FileChange("path.md", "content")
        assert change.operation == "append"

    def test_custom_operation(self):
        """Test custom operation."""
        change = FileChange("path.md", "content", "replace")
        assert change.operation == "replace"


class TestConstants:
    """Tests for module constants."""

    def test_git_email_configured(self):
        """Test Git email is set to a valid address."""
        assert "@" in GIT_EMAIL

    def test_git_name_is_set(self):
        """Test Git name is set."""
        assert len(GIT_NAME) > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

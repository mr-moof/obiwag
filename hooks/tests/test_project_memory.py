"""Unit tests for project_memory module."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.project_memory import (
    _encode_path_for_claude,
    _fingerprint_directory,
    _fingerprint_file,
    _has_changes,
    _load_latest_index,
    _rotate_snapshots,
    MAX_SNAPSHOTS,
    backup_project_memory,
    get_project_memory_dir,
)


class TestEncodePathForClaude:
    """Tests for _encode_path_for_claude."""

    def test_windows_path(self):
        result = _encode_path_for_claude("C:\\Users\\user")
        assert result == "C--Users-user"

    def test_windows_path_with_subdir(self):
        result = _encode_path_for_claude("C:\\Users\\user\\source")
        assert result == "C--Users-user-source"

    def test_forward_slashes(self):
        result = _encode_path_for_claude("C:/Users/user")
        assert result == "C--Users-user"

    def test_trailing_separator_stripped(self):
        result = _encode_path_for_claude("C:\\Users\\user\\")
        assert result == "C--Users-user"

    def test_drive_letter_encoding(self):
        result = _encode_path_for_claude("D:\\Projects")
        assert result == "D--Projects"

    def test_deep_path(self):
        result = _encode_path_for_claude("C:\\Users\\user\\source\\obiwag-agents")
        assert result == "C--Users-user-source-obiwag-agents"


class TestGetProjectMemoryDir:
    """Tests for get_project_memory_dir."""

    def test_exact_match(self, tmp_path):
        projects_dir = tmp_path / ".claude" / "projects"
        encoded = _encode_path_for_claude(str(tmp_path))
        memory_dir = projects_dir / encoded / "memory"
        memory_dir.mkdir(parents=True)

        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)):
            result = get_project_memory_dir(str(tmp_path))
            assert result == str(memory_dir)

    def test_case_insensitive_match(self, tmp_path):
        projects_dir = tmp_path / ".claude" / "projects"
        encoded = _encode_path_for_claude(str(tmp_path))
        # Directory on disk uses swapped case for first char
        swapped = encoded[0].swapcase() + encoded[1:]
        memory_dir = projects_dir / swapped / "memory"
        memory_dir.mkdir(parents=True)

        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)):
            result = get_project_memory_dir(str(tmp_path))
            assert result is not None

    def test_missing_projects_dir(self, tmp_path):
        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)):
            result = get_project_memory_dir(str(tmp_path))
            assert result is None

    def test_no_memory_subdir(self, tmp_path):
        projects_dir = tmp_path / ".claude" / "projects"
        # Project dir exists but has no memory/ subdirectory
        (projects_dir / "C--Users-testuser").mkdir(parents=True)

        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)):
            result = get_project_memory_dir(str(tmp_path))
            assert result is None

    def test_defaults_to_populated_home(self, tmp_path):
        """When called without an explicit path, prefer a populated home-encoded dir."""
        projects_dir = tmp_path / ".claude" / "projects"
        encoded = _encode_path_for_claude(str(tmp_path))
        memory_dir = projects_dir / encoded / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("- entry\n")

        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)), \
             patch("core.project_memory.os.getcwd", return_value=str(tmp_path / "nonexistent")):
            result = get_project_memory_dir()
            assert result == str(memory_dir)

    def test_default_falls_back_to_populated_dir(self, tmp_path):
        """When neither cwd nor home is populated, fall back to any populated memory dir.

        This is the contract change that fixes the project-memory backup bug:
        the old default returned an empty home-encoded dir, which silently broke
        ``backup_project_memory()`` because Claude Code keys project memory by
        the cwd at session start, not by user home.
        """
        projects_dir = tmp_path / ".claude" / "projects"
        # Home-encoded dir exists but is empty
        home_encoded = _encode_path_for_claude(str(tmp_path))
        (projects_dir / home_encoded / "memory").mkdir(parents=True)
        # A different project has the populated memory
        other_memory = projects_dir / "C--SomeProject" / "memory"
        other_memory.mkdir(parents=True)
        (other_memory / "MEMORY.md").write_text("- real entry\n")

        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)), \
             patch("core.project_memory.os.getcwd", return_value=str(tmp_path / "nonexistent")):
            result = get_project_memory_dir()
            assert result == str(other_memory)

    def test_default_prefers_cwd_over_home(self, tmp_path):
        """Cwd-encoded populated dir wins over home-encoded populated dir."""
        projects_dir = tmp_path / ".claude" / "projects"
        cwd_path = tmp_path / "active-project"
        cwd_path.mkdir()

        home_encoded = _encode_path_for_claude(str(tmp_path))
        home_memory = projects_dir / home_encoded / "memory"
        home_memory.mkdir(parents=True)
        (home_memory / "MEMORY.md").write_text("- home\n")

        cwd_encoded = _encode_path_for_claude(str(cwd_path))
        cwd_memory = projects_dir / cwd_encoded / "memory"
        cwd_memory.mkdir(parents=True)
        (cwd_memory / "MEMORY.md").write_text("- cwd\n")

        with patch("core.project_memory.os.path.expanduser", return_value=str(tmp_path)), \
             patch("core.project_memory.os.getcwd", return_value=str(cwd_path)):
            result = get_project_memory_dir()
            assert result == str(cwd_memory)


class TestFingerprintFile:
    """Tests for _fingerprint_file."""

    def test_returns_size_and_mtime(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("hello world")
        fp = _fingerprint_file(str(f))
        assert "size" in fp
        assert "mtime" in fp
        assert fp["size"] == 11
        assert isinstance(fp["mtime"], int)

    def test_mtime_is_integer(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("content")
        fp = _fingerprint_file(str(f))
        # mtime should be truncated to integer seconds
        assert fp["mtime"] == int(fp["mtime"])


class TestFingerprintDirectory:
    """Tests for _fingerprint_directory."""

    def test_empty_directory(self, tmp_path):
        result = _fingerprint_directory(str(tmp_path))
        assert result == {}

    def test_captures_files(self, tmp_path):
        (tmp_path / "a.md").write_text("alpha")
        (tmp_path / "b.md").write_text("beta")
        result = _fingerprint_directory(str(tmp_path))
        assert "a.md" in result
        assert "b.md" in result

    def test_ignores_subdirectories(self, tmp_path):
        (tmp_path / "file.md").write_text("content")
        (tmp_path / "subdir").mkdir()
        result = _fingerprint_directory(str(tmp_path))
        assert "subdir" not in result
        assert "file.md" in result


class TestHasChanges:
    """Tests for _has_changes."""

    def test_no_previous_backup(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("content")
        backup_dir = tmp_path / "backups"
        # backup_dir doesn't exist
        assert _has_changes(str(memory_dir), str(backup_dir)) is True

    def test_no_changes(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "MEMORY.md"
        f.write_text("content")

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        fp = _fingerprint_file(str(f))
        index = {"fingerprints": {"MEMORY.md": fp}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        assert _has_changes(str(memory_dir), str(backup_dir)) is False

    def test_file_added(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("content")
        (memory_dir / "new_file.md").write_text("new")

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        fp = _fingerprint_file(str(memory_dir / "MEMORY.md"))
        index = {"fingerprints": {"MEMORY.md": fp}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        assert _has_changes(str(memory_dir), str(backup_dir)) is True

    def test_file_removed(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("content")

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        fp = _fingerprint_file(str(memory_dir / "MEMORY.md"))
        index = {"fingerprints": {"MEMORY.md": fp, "old_file.md": {"size": 10, "mtime": 100}}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        assert _has_changes(str(memory_dir), str(backup_dir)) is True

    def test_file_modified_size(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "MEMORY.md"
        f.write_text("longer content now")

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        index = {"fingerprints": {"MEMORY.md": {"size": 5, "mtime": int(time.time())}}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        assert _has_changes(str(memory_dir), str(backup_dir)) is True

    def test_mtime_within_tolerance(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "MEMORY.md"
        f.write_text("content")

        fp = _fingerprint_file(str(f))

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        # Shift mtime by 1 second (within 2s tolerance)
        index = {"fingerprints": {"MEMORY.md": {"size": fp["size"], "mtime": fp["mtime"] + 1}}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        assert _has_changes(str(memory_dir), str(backup_dir)) is False

    def test_mtime_beyond_tolerance(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "MEMORY.md"
        f.write_text("content")

        fp = _fingerprint_file(str(f))

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        # Shift mtime by 5 seconds (beyond 2s tolerance)
        index = {"fingerprints": {"MEMORY.md": {"size": fp["size"], "mtime": fp["mtime"] + 5}}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        assert _has_changes(str(memory_dir), str(backup_dir)) is True


class TestRotateSnapshots:
    """Tests for _rotate_snapshots."""

    def test_keeps_max_snapshots(self, tmp_path):
        for i in range(5):
            (tmp_path / f"snapshot-2026033{i}-120000").mkdir()
        _rotate_snapshots(str(tmp_path))
        remaining = [d for d in os.listdir(str(tmp_path)) if d.startswith("snapshot-")]
        assert len(remaining) == MAX_SNAPSHOTS

    def test_keeps_newest(self, tmp_path):
        for i in range(5):
            (tmp_path / f"snapshot-2026033{i}-120000").mkdir()
        _rotate_snapshots(str(tmp_path))
        remaining = sorted(os.listdir(str(tmp_path)), reverse=True)
        # Should keep the 3 newest (highest timestamps)
        assert "snapshot-20260334-120000" in remaining
        assert "snapshot-20260333-120000" in remaining
        assert "snapshot-20260332-120000" in remaining

    def test_noop_under_limit(self, tmp_path):
        (tmp_path / "snapshot-20260331-120000").mkdir()
        _rotate_snapshots(str(tmp_path))
        remaining = [d for d in os.listdir(str(tmp_path)) if d.startswith("snapshot-")]
        assert len(remaining) == 1


class TestBackupProjectMemory:
    """Tests for backup_project_memory."""

    def test_first_backup(self, tmp_path):
        memory_dir = tmp_path / ".claude" / "projects" / "C--test" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "MEMORY.md").write_text("# Memory\ncontent here")
        (memory_dir / "ref.md").write_text("reference file")

        backup_dir = tmp_path / ".claude" / ".obi" / "backups" / "project-memory"

        with patch("core.project_memory.get_project_memory_dir", return_value=str(memory_dir)), \
             patch("core.project_memory.get_backup_dir", return_value=str(backup_dir)):
            result = backup_project_memory()

        assert result is not None
        assert os.path.isdir(result)
        # Check sentinel file
        index_path = os.path.join(result, "backup-index.json")
        assert os.path.isfile(index_path)
        with open(index_path) as f:
            index = json.load(f)
        assert index["file_count"] == 2
        assert "MEMORY.md" in index["fingerprints"]
        assert "ref.md" in index["fingerprints"]
        # Check files were copied
        assert os.path.isfile(os.path.join(result, "MEMORY.md"))
        assert os.path.isfile(os.path.join(result, "ref.md"))

    def test_no_changes_skips_backup(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        f = memory_dir / "MEMORY.md"
        f.write_text("content")

        backup_dir = tmp_path / "backups"
        snapshot = backup_dir / "snapshot-20260331-120000"
        snapshot.mkdir(parents=True)

        fp = _fingerprint_file(str(f))
        index = {"fingerprints": {"MEMORY.md": fp}}
        (snapshot / "backup-index.json").write_text(json.dumps(index))

        with patch("core.project_memory.get_project_memory_dir", return_value=str(memory_dir)), \
             patch("core.project_memory.get_backup_dir", return_value=str(backup_dir)):
            result = backup_project_memory()

        assert result is None

    def test_returns_none_when_memory_dir_missing(self):
        with patch("core.project_memory.get_project_memory_dir", return_value=None):
            assert backup_project_memory() is None

    def test_returns_none_when_memory_dir_empty(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()  # Empty directory

        with patch("core.project_memory.get_project_memory_dir", return_value=str(memory_dir)):
            assert backup_project_memory() is None

    def test_rotation_triggered(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("content")

        backup_dir = tmp_path / "backups"
        # Create MAX_SNAPSHOTS existing snapshots
        for i in range(MAX_SNAPSHOTS):
            snap = backup_dir / f"snapshot-2026030{i}-120000"
            snap.mkdir(parents=True)
            idx = {"fingerprints": {"old.md": {"size": 1, "mtime": 100}}}
            (snap / "backup-index.json").write_text(json.dumps(idx))

        with patch("core.project_memory.get_project_memory_dir", return_value=str(memory_dir)), \
             patch("core.project_memory.get_backup_dir", return_value=str(backup_dir)):
            result = backup_project_memory()

        assert result is not None
        # Should have MAX_SNAPSHOTS total (old ones rotated)
        remaining = [d for d in os.listdir(str(backup_dir)) if d.startswith("snapshot-")]
        assert len(remaining) == MAX_SNAPSHOTS


class TestLoadLatestIndex:
    """Tests for _load_latest_index."""

    def test_no_backup_dir(self, tmp_path):
        assert _load_latest_index(str(tmp_path / "nonexistent")) is None

    def test_empty_backup_dir(self, tmp_path):
        assert _load_latest_index(str(tmp_path)) is None

    def test_loads_newest_snapshot(self, tmp_path):
        old = tmp_path / "snapshot-20260330-120000"
        old.mkdir()
        (old / "backup-index.json").write_text(json.dumps({"version": "old"}))

        new = tmp_path / "snapshot-20260331-120000"
        new.mkdir()
        (new / "backup-index.json").write_text(json.dumps({"version": "new"}))

        result = _load_latest_index(str(tmp_path))
        assert result["version"] == "new"

    def test_missing_index_file(self, tmp_path):
        (tmp_path / "snapshot-20260331-120000").mkdir()
        # No backup-index.json inside
        assert _load_latest_index(str(tmp_path)) is None

    def test_corrupt_index_file(self, tmp_path):
        snap = tmp_path / "snapshot-20260331-120000"
        snap.mkdir()
        (snap / "backup-index.json").write_text("not valid json{{{")
        assert _load_latest_index(str(tmp_path)) is None

"""Tests for obi_state_backup — authoritative .obi/ file snapshots."""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hook root to path
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.obi_state_backup import (  # noqa: E402
    MAX_SNAPSHOTS,
    TRACKED_FILES,
    backup_obi_state,
    get_obi_state_backup_dir,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestBackupObiState:
    def test_first_backup_copies_tracked_files(self, tmp_path):
        source = tmp_path / ".obi"
        backup = tmp_path / "backups" / "obi-state"
        _write(source / "calibration.md", "---\nversion: 1.0\n---\n")
        _write(source / "pending-learnings.json", '{"learnings": []}')

        with patch("core.obi_state_backup._obi_dir", return_value=str(source)), \
             patch("core.obi_state_backup.get_obi_state_backup_dir", return_value=str(backup)):
            result = backup_obi_state()

        assert result is not None
        snapshot = Path(result)
        assert (snapshot / "calibration.md").read_text(encoding="utf-8") == "---\nversion: 1.0\n---\n"
        assert (snapshot / "pending-learnings.json").read_text(encoding="utf-8") == '{"learnings": []}'
        index = json.loads((snapshot / "backup-index.json").read_text(encoding="utf-8"))
        assert set(index["files"]) == {"calibration.md", "pending-learnings.json"}
        assert index["file_count"] == 2

    def test_no_backup_when_nothing_changed(self, tmp_path):
        source = tmp_path / ".obi"
        backup = tmp_path / "backups" / "obi-state"
        _write(source / "calibration.md", "---\nversion: 1.0\n---\n")

        with patch("core.obi_state_backup._obi_dir", return_value=str(source)), \
             patch("core.obi_state_backup.get_obi_state_backup_dir", return_value=str(backup)):
            first = backup_obi_state()
            second = backup_obi_state()

        assert first is not None
        assert second is None

    def test_backup_when_content_changes(self, tmp_path):
        source = tmp_path / ".obi"
        backup = tmp_path / "backups" / "obi-state"
        _write(source / "calibration.md", "---\nversion: 1.0\n---\n")

        with patch("core.obi_state_backup._obi_dir", return_value=str(source)), \
             patch("core.obi_state_backup.get_obi_state_backup_dir", return_value=str(backup)):
            first = backup_obi_state()

            # Sleep so the second snapshot gets a distinct %Y%m%d-%H%M%S timestamp.
            time.sleep(1.1)

            # Edit file and bump mtime by >2s so fingerprint diverges
            _write(source / "calibration.md", "---\nversion: 2.0\n---\n")
            new_mtime = time.time() + 10
            os.utime(source / "calibration.md", (new_mtime, new_mtime))

            second = backup_obi_state()

        assert first is not None
        assert second is not None
        assert first != second

    def test_no_backup_when_source_missing(self, tmp_path):
        with patch("core.obi_state_backup._obi_dir", return_value=str(tmp_path / "nonexistent")):
            assert backup_obi_state() is None

    def test_no_backup_when_no_tracked_files(self, tmp_path):
        source = tmp_path / ".obi"
        backup = tmp_path / "backups" / "obi-state"
        source.mkdir()
        # Only an untracked file present
        _write(source / "deployment-manifest.json", "{}")

        with patch("core.obi_state_backup._obi_dir", return_value=str(source)), \
             patch("core.obi_state_backup.get_obi_state_backup_dir", return_value=str(backup)):
            assert backup_obi_state() is None

    def test_rotation_keeps_only_max_snapshots(self, tmp_path):
        source = tmp_path / ".obi"
        backup = tmp_path / "backups" / "obi-state"
        _write(source / "calibration.md", "v0")

        with patch("core.obi_state_backup._obi_dir", return_value=str(source)), \
             patch("core.obi_state_backup.get_obi_state_backup_dir", return_value=str(backup)):
            for i in range(MAX_SNAPSHOTS + 2):
                _write(source / "calibration.md", f"v{i}")
                new_mtime = time.time() + 10 * (i + 1)
                os.utime(source / "calibration.md", (new_mtime, new_mtime))
                backup_obi_state()
                time.sleep(1.1)  # ensure distinct snapshot timestamps

        snapshots = [d for d in os.listdir(backup) if d.startswith("snapshot-")]
        assert len(snapshots) == MAX_SNAPSHOTS

    def test_tracked_files_list(self):
        assert "calibration.md" in TRACKED_FILES
        assert "pending-learnings.json" in TRACKED_FILES

    def test_get_obi_state_backup_dir_default(self):
        path = get_obi_state_backup_dir()
        assert path.endswith(os.path.join("backups", "obi-state"))
        assert ".claude" in path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

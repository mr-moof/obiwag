"""Unit tests for settings.local.json cleanup in session_start hook."""

import json
import os

import pytest

# The cleanup logic is inline in session_start.py. Extract the testable
# behavior: given a source dir with projects containing settings.local.json,
# and a user settings.json with N permissions, files with fewer permissions
# should be cleaned.


def run_cleanup(source_dir: str, user_settings_path: str) -> list:
    """Reproduce the cleanup logic from session_start.py for testing.

    Returns list of cleaned project names.
    """
    cleaned = []
    if not os.path.isdir(source_dir) or not os.path.isfile(user_settings_path):
        return cleaned

    with open(user_settings_path, 'r', encoding='utf-8') as f:
        user_data = json.load(f)
    user_perms = len(user_data.get('permissions', {}).get('allow', []))

    if user_perms <= 50:
        return cleaned

    for entry in os.scandir(source_dir):
        if not entry.is_dir():
            continue
        local_settings = os.path.join(entry.path, '.claude', 'settings.local.json')
        if os.path.isfile(local_settings):
            try:
                with open(local_settings, 'r', encoding='utf-8') as f:
                    local_data = json.load(f)
                # Skip files managed by obi-deploy (Issue #74)
                if local_data.get('_managed_by') == 'obi-deploy':
                    continue
                local_perms = len(local_data.get('permissions', {}).get('allow', []))
                if 0 < local_perms < user_perms:
                    os.remove(local_settings)
                    cleaned.append(entry.name)
            except Exception:
                pass

    return cleaned


@pytest.fixture
def setup_dirs(tmp_path):
    """Create a temporary directory structure for testing."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()

    # User settings with 100 permissions (above threshold)
    user_settings = claude_dir / "settings.json"
    perms = [f"Bash(cmd_{i})" for i in range(100)]
    user_settings.write_text(json.dumps({
        "permissions": {"allow": perms}
    }))

    return source_dir, str(user_settings)


class TestCleanupSkipsWhenFewUserPermissions:
    def test_skip_when_under_threshold(self, tmp_path):
        """Cleanup should not run when user has fewer than 50 permissions."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        # Project with 10 local permissions
        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        (proj / "settings.local.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(a)" for _ in range(10)]}
        }))

        # User with only 30 permissions (below 50 threshold)
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        user_settings = claude_dir / "settings.json"
        user_settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(a)" for _ in range(30)]}
        }))

        cleaned = run_cleanup(str(source_dir), str(user_settings))
        assert cleaned == []
        assert (proj / "settings.local.json").exists()


class TestCleanupDeletesStaleFiles:
    def test_deletes_when_local_fewer_than_user(self, setup_dirs):
        """Should delete settings.local.json when it has fewer permissions."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash(a)" for _ in range(20)]}
        }))

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert "my-project" in cleaned
        assert not local_file.exists()

    def test_skips_when_local_same_as_user(self, setup_dirs):
        """Should not delete when local has same count as user."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash(a)" for _ in range(100)]}
        }))

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert cleaned == []
        assert local_file.exists()

    def test_skips_when_local_more_than_user(self, setup_dirs):
        """Should not delete when local has more permissions than user."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash(a)" for _ in range(200)]}
        }))

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert cleaned == []
        assert local_file.exists()

    def test_skips_empty_permissions(self, setup_dirs):
        """Should not delete when local has zero permissions."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text(json.dumps({
            "permissions": {"allow": []}
        }))

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert cleaned == []
        assert local_file.exists()


class TestCleanupSkipsManagedFiles:
    def test_skips_obi_deploy_managed_files(self, setup_dirs):
        """Should not delete settings.local.json with _managed_by: obi-deploy."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text(json.dumps({
            "_managed_by": "obi-deploy",
            "permissions": {"allow": ["Bash(a)" for _ in range(5)]}
        }))

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert cleaned == []
        assert local_file.exists()

    def test_deletes_unmanaged_with_few_perms(self, setup_dirs):
        """Should still delete unmanaged files with fewer permissions."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text(json.dumps({
            "permissions": {"allow": ["Bash(a)" for _ in range(10)]}
        }))

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert "my-project" in cleaned
        assert not local_file.exists()


class TestCleanupHandlesErrors:
    def test_invalid_json_skipped(self, setup_dirs):
        """Should skip files with invalid JSON."""
        source_dir, user_settings = setup_dirs

        proj = source_dir / "my-project" / ".claude"
        proj.mkdir(parents=True)
        local_file = proj / "settings.local.json"
        local_file.write_text("not valid json {{{")

        cleaned = run_cleanup(str(source_dir), user_settings)
        assert cleaned == []
        assert local_file.exists()  # Not deleted

    def test_missing_source_dir(self, tmp_path):
        """Should handle missing source directory."""
        user_settings = tmp_path / "settings.json"
        user_settings.write_text(json.dumps({
            "permissions": {"allow": ["a" for _ in range(100)]}
        }))

        cleaned = run_cleanup("/nonexistent/path", str(user_settings))
        assert cleaned == []

    def test_missing_user_settings(self, tmp_path):
        """Should handle missing user settings."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        cleaned = run_cleanup(str(source_dir), "/nonexistent/settings.json")
        assert cleaned == []


class TestGcOldFiles:
    """Tests for the session-start GC helper that prunes unbounded-growth dirs."""

    def _setup(self, tmp_path):
        import sys
        from pathlib import Path
        hooks_dir = Path(__file__).parent.parent
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        from session_start import _gc_old_files
        return _gc_old_files

    def test_prunes_files_older_than_cutoff(self, tmp_path):
        gc = self._setup(tmp_path)
        old = tmp_path / "old.md"
        new = tmp_path / "new.md"
        old.write_text("stale")
        new.write_text("fresh")
        # Backdate old to 60 days ago
        old_mtime = (60 * 86400)
        os.utime(str(old), (1, __import__("time").time() - old_mtime))

        removed = gc(str(tmp_path), ".md", max_age_days=30)
        assert removed == 1
        assert not old.exists()
        assert new.exists()

    def test_respects_suffix_filter(self, tmp_path):
        gc = self._setup(tmp_path)
        bad = tmp_path / "old.json"
        bad.write_text("ignored")
        os.utime(str(bad), (1, __import__("time").time() - 60 * 86400))

        removed = gc(str(tmp_path), ".md", max_age_days=30)
        assert removed == 0
        assert bad.exists()  # Wrong suffix — left alone

    def test_missing_directory_returns_zero(self, tmp_path):
        gc = self._setup(tmp_path)
        removed = gc(str(tmp_path / "does-not-exist"), ".md", max_age_days=30)
        assert removed == 0

    def test_keeps_recent_files(self, tmp_path):
        gc = self._setup(tmp_path)
        recent = tmp_path / "recent.md"
        recent.write_text("fresh")
        # Default mtime is now — well within retention

        removed = gc(str(tmp_path), ".md", max_age_days=30)
        assert removed == 0
        assert recent.exists()

"""Unit tests for the shared paths module."""

import os
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import core.paths as paths
from core.paths import (
    get_obi_platform,
    get_obiwag_repo_path,
    get_obi_source_repo,
    get_source_repo_path,
    get_obi_root,
    get_obi_data_dir,
    ensure_dir,
)


class TestEnsureDir:
    """Tests for ensure_dir helper."""

    def test_creates_directory(self, tmp_path):
        """ensure_dir creates a directory that does not exist."""
        target = str(tmp_path / 'sub' / 'nested')
        assert not os.path.isdir(target)
        ensure_dir(target)
        assert os.path.isdir(target)

    def test_noop_on_existing(self, tmp_path):
        """ensure_dir does not raise when directory already exists."""
        target = str(tmp_path / 'already')
        os.makedirs(target)
        ensure_dir(target)  # should not raise
        assert os.path.isdir(target)


class TestGetObiwagRepoPath:
    """Tests for the canonical get_obiwag_repo_path function."""

    def test_env_var_override(self, tmp_path, monkeypatch):
        """OBIWAG_SOURCE env var takes priority when .git exists."""
        git_dir = tmp_path / '.git'
        git_dir.mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        result = get_obiwag_repo_path()
        assert result == tmp_path

    def test_env_var_without_git_skipped(self, tmp_path, monkeypatch):
        """OBIWAG_SOURCE with no .git directory is skipped."""
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        # tmp_path has no .git, so env var path is rejected.
        # Result depends on whether a real candidate exists on disk,
        # so we only assert the env path was not blindly returned.
        result = get_obiwag_repo_path()
        if result is not None:
            assert (result / '.git').is_dir()

    def test_returns_path_type(self, tmp_path, monkeypatch):
        """Return value is a Path (not str) when found."""
        git_dir = tmp_path / '.git'
        git_dir.mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        result = get_obiwag_repo_path()
        assert isinstance(result, Path)


class TestGetObiSourceRepo:
    """get_obi_source_repo is an alias for get_obiwag_repo_path."""

    def test_is_same_function(self):
        assert get_obi_source_repo is get_obiwag_repo_path


class TestGetSourceRepoPath:
    """Tests for the stricter drift-detector variant."""

    def test_returns_str_when_hooks_present(self, tmp_path, monkeypatch):
        """Returns str when repo has both .git and hooks/."""
        (tmp_path / '.git').mkdir()
        (tmp_path / 'hooks').mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        result = get_source_repo_path()
        assert result == str(tmp_path)
        assert isinstance(result, str)

    def test_returns_none_when_hooks_missing(self, tmp_path, monkeypatch):
        """Returns None when env path lacks hooks/ and no candidate exists."""
        (tmp_path / '.git').mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        # Suppress hardcoded candidates so only the env var path is tried.
        real_isdir = os.path.isdir

        def _isdir(p):
            # Allow the env var check (tmp_path/hooks won't exist) but
            # block all hardcoded candidate checks.
            if 'src' in str(p) or 'source' in str(p):
                return False
            return real_isdir(p)

        monkeypatch.setattr('core.paths.os.path.isdir', _isdir)
        result = get_source_repo_path()
        assert result is None


class TestGetObiRoot:
    """Tests for get_obi_root."""

    def test_returns_claude_dir(self, monkeypatch):
        monkeypatch.delenv('OBI_PLATFORM', raising=False)
        monkeypatch.delenv('OBI_ROOT', raising=False)
        result = get_obi_root()
        assert result == Path.home() / '.claude'

    def test_returns_path_type(self):
        assert isinstance(get_obi_root(), Path)

    def test_returns_codex_dir(self, monkeypatch):
        monkeypatch.setenv('OBI_PLATFORM', 'codex')
        monkeypatch.delenv('OBI_ROOT', raising=False)
        assert get_obi_root() == Path.home() / '.codex'

    def test_root_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_PLATFORM', 'codex')
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        assert get_obi_root() == tmp_path

    def test_unknown_platform_defaults_to_claude(self, monkeypatch):
        monkeypatch.setenv('OBI_PLATFORM', 'unknown')
        monkeypatch.delenv('OBI_ROOT', raising=False)
        assert get_obi_platform() == 'claude'
        assert get_obi_root() == Path.home() / '.claude'


class TestGetObiDataDir:
    """Tests for get_obi_data_dir."""

    def test_env_var_override(self, tmp_path, monkeypatch):
        """OBIWAG_SOURCE env var is checked first."""
        obi_dir = tmp_path / '.obi'
        obi_dir.mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        result = get_obi_data_dir()
        assert result == str(obi_dir)

    def test_returns_none_when_no_obi(self, tmp_path, monkeypatch):
        """Returns None when .obi dir does not exist under env path."""
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        # No candidate has .obi either; result depends on real disk.
        # Just verify no crash and correct type.
        result = get_obi_data_dir()
        assert result is None or isinstance(result, str)


class TestFindRepoEngine:
    """Tests for the shared _find_repo engine and its three wrappers (#180).

    The three public discovery functions are now thin wrappers over one
    candidate-walk engine.  These tests pin the unified behavior: the
    env-var precedence, per-marker validation, return types, and the
    single-source-of-truth candidate list.
    """

    # (function, marker, expected wrapper return type for a found repo)
    MARKER_CASES = [
        (get_obiwag_repo_path, '.git', Path),
        (get_source_repo_path, 'hooks', str),
        (get_obi_data_dir, '.obi', str),
    ]

    @pytest.mark.parametrize(
        'func, marker, expected_type',
        MARKER_CASES,
        ids=['git->Path', 'hooks->str', 'obi->str'],
    )
    def test_env_var_found_per_marker(self, func, marker, expected_type,
                                      tmp_path, monkeypatch):
        """Each wrapper finds the repo via OBIWAG_SOURCE for its own marker."""
        (tmp_path / marker).mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        result = func()
        assert result is not None
        assert isinstance(result, expected_type)

    @pytest.mark.parametrize(
        'func, marker, expected_type',
        MARKER_CASES,
        ids=['git->Path', 'hooks->str', 'obi->str'],
    )
    def test_marker_absent_skips_env_path(self, func, marker, expected_type,
                                          tmp_path, monkeypatch):
        """An env path lacking the marker is not blindly returned."""
        # tmp_path has no marker subdir; suppress the hardcoded candidates
        # so only the env path is considered, yielding None.
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        monkeypatch.setattr(paths, '_REPO_CANDIDATES', [])
        assert func() is None

    def test_obiwag_search_roots_seam_preserved(self, tmp_path, monkeypatch):
        """get_obiwag_repo_path still honors the search_roots test seam."""
        repo = tmp_path / 'source' / 'obiwag-agents'
        (repo / '.git').mkdir(parents=True)
        monkeypatch.delenv('OBIWAG_SOURCE', raising=False)
        # search_roots replaces the defaults and returns the exact Path.
        assert get_obiwag_repo_path(search_roots=[repo]) == repo
        assert get_obiwag_repo_path(
            search_roots=[tmp_path / 'nonexistent']) is None

    def test_obi_data_dir_returns_marker_path(self, tmp_path, monkeypatch):
        """get_obi_data_dir returns the .obi subdir path, not the repo root."""
        (tmp_path / '.obi').mkdir()
        monkeypatch.setenv('OBIWAG_SOURCE', str(tmp_path))
        assert get_obi_data_dir() == os.path.join(str(tmp_path), '.obi')

    def test_candidate_list_is_single_shared_object(self, monkeypatch):
        """All three functions walk the SAME candidate-list object (#180).

        We replace the module's shared candidate list with a sentinel and
        spy on _find_repo to capture the list each wrapper actually walks.
        Proving every wrapper receives the identical object (by identity)
        confirms there is exactly one source of truth for the candidates.
        """
        sentinel = paths._build_repo_candidates()
        monkeypatch.setattr(paths, '_REPO_CANDIDATES', sentinel)
        monkeypatch.delenv('OBIWAG_SOURCE', raising=False)

        seen = []
        real_find = paths._find_repo

        def _spy(marker, candidates=None):
            # Capture the list the engine will actually walk for a default
            # (non-override) call — i.e. when no explicit candidates given.
            seen.append(candidates if candidates is not None
                        else paths._default_repo_candidates())
            return real_find(marker, candidates=candidates)

        monkeypatch.setattr(paths, '_find_repo', _spy)

        get_obiwag_repo_path()
        get_source_repo_path()
        get_obi_data_dir()

        assert len(seen) == 3
        # Literally the same object for all three (identity, not equality).
        assert seen[0] is sentinel
        assert seen[1] is sentinel
        assert seen[2] is sentinel
        assert seen[0] is seen[1] is seen[2]

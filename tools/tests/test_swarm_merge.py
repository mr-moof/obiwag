"""Unit tests for swarm_merge module."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Add tools directory to path for imports
TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLS_DIR))

from swarm_merge import get_changed_files, detect_overlaps, try_merge, merge_batch, run_git


def _git(repo_path: str, *args: str):
    """Helper to run git commands in test repos."""
    subprocess.run(
        ['git', '-C', repo_path] + list(args),
        capture_output=True, text=True, check=True,
    )


def _init_repo(tmp_path: Path) -> str:
    """Create a test git repo with an initial commit on master."""
    repo = str(tmp_path / 'test-repo')
    os.makedirs(repo)
    _git(repo, 'init', '-b', 'master')
    _git(repo, 'config', 'user.email', 'test@test.local')
    _git(repo, 'config', 'user.name', 'Test')

    # Initial commit with a file
    readme = Path(repo) / 'README.md'
    readme.write_text('# Test Repo\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', 'Initial commit')

    return repo


def _create_branch_with_changes(repo: str, branch: str, file_changes: dict):
    """Create a branch with file changes.

    Args:
        repo: Repo path.
        branch: Branch name.
        file_changes: Dict mapping filename to content.
    """
    _git(repo, 'checkout', '-b', branch, 'master')
    for filename, content in file_changes.items():
        filepath = Path(repo) / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-m', f'Changes on {branch}')
    _git(repo, 'checkout', 'master')


class TestGetChangedFiles:
    """Tests for get_changed_files."""

    def test_single_file_change(self, tmp_path):
        """Detect a single changed file."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'docs/CHANGELOG.md': 'v1.0\n'})

        changed = get_changed_files(repo, 'fix/issue-1')
        assert changed == {'docs/CHANGELOG.md'}

    def test_multiple_file_changes(self, tmp_path):
        """Detect multiple changed files."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-2', {
            'src/a.py': 'print("a")\n',
            'src/b.py': 'print("b")\n',
        })

        changed = get_changed_files(repo, 'fix/issue-2')
        assert changed == {'src/a.py', 'src/b.py'}

    def test_no_changes(self, tmp_path):
        """Branch with no changes returns empty set."""
        repo = _init_repo(tmp_path)
        _git(repo, 'checkout', '-b', 'empty-branch', 'master')
        _git(repo, 'checkout', 'master')

        changed = get_changed_files(repo, 'empty-branch')
        assert changed == set()

    def test_nonexistent_branch(self, tmp_path):
        """Nonexistent branch returns empty set."""
        repo = _init_repo(tmp_path)
        changed = get_changed_files(repo, 'does-not-exist')
        assert changed == set()


class TestDetectOverlaps:
    """Tests for detect_overlaps."""

    def test_no_overlaps(self, tmp_path):
        """Branches touching different files have no overlaps."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'file_a.txt': 'a\n'})
        _create_branch_with_changes(repo, 'fix/issue-2', {'file_b.txt': 'b\n'})

        overlaps = detect_overlaps(repo, ['fix/issue-1', 'fix/issue-2'])
        assert overlaps == {}

    def test_overlapping_file(self, tmp_path):
        """Branches touching the same file are detected."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'shared.txt': 'version A\n'})
        _create_branch_with_changes(repo, 'fix/issue-2', {'shared.txt': 'version B\n'})

        overlaps = detect_overlaps(repo, ['fix/issue-1', 'fix/issue-2'])
        assert 'shared.txt' in overlaps
        assert set(overlaps['shared.txt']) == {'fix/issue-1', 'fix/issue-2'}

    def test_partial_overlap(self, tmp_path):
        """Only shared files appear in overlap results."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {
            'shared.txt': 'a\n',
            'unique_a.txt': 'a\n',
        })
        _create_branch_with_changes(repo, 'fix/issue-2', {
            'shared.txt': 'b\n',
            'unique_b.txt': 'b\n',
        })

        overlaps = detect_overlaps(repo, ['fix/issue-1', 'fix/issue-2'])
        assert 'shared.txt' in overlaps
        assert 'unique_a.txt' not in overlaps
        assert 'unique_b.txt' not in overlaps


class TestTryMerge:
    """Tests for try_merge."""

    def test_clean_merge(self, tmp_path):
        """Non-conflicting branch merges successfully."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'new_file.txt': 'content\n'})

        success, message = try_merge(repo, 'fix/issue-1')
        assert success is True
        assert 'merged' in message.lower() or 'fix/issue-1' in message

    def test_conflict_merge(self, tmp_path):
        """Conflicting branches fail gracefully."""
        repo = _init_repo(tmp_path)

        # Modify README.md on master
        readme = Path(repo) / 'README.md'
        readme.write_text('# Modified on master\n')
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-m', 'Modify README on master')

        # Create branch that also modifies README.md (from before the master change)
        _git(repo, 'checkout', '-b', 'fix/issue-1', 'master~1')
        readme.write_text('# Modified on branch\n')
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-m', 'Modify README on branch')
        _git(repo, 'checkout', 'master')

        success, message = try_merge(repo, 'fix/issue-1')
        assert success is False

    def test_merge_leaves_clean_state_on_failure(self, tmp_path):
        """Failed merge aborts cleanly, leaving repo in good state."""
        repo = _init_repo(tmp_path)

        readme = Path(repo) / 'README.md'
        readme.write_text('# Master version\n')
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-m', 'Master change')

        _git(repo, 'checkout', '-b', 'fix/conflict', 'master~1')
        readme.write_text('# Branch version\n')
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-m', 'Branch change')
        _git(repo, 'checkout', 'master')

        try_merge(repo, 'fix/conflict')

        # Verify repo is in clean state (no merge in progress)
        rc, stdout, _ = run_git(repo, 'status', '--porcelain')
        assert rc == 0
        assert stdout == ''


class TestMergeBatch:
    """Tests for merge_batch."""

    def test_all_clean_merges(self, tmp_path):
        """All non-conflicting branches merge successfully."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'file_a.txt': 'a\n'})
        _create_branch_with_changes(repo, 'fix/issue-2', {'file_b.txt': 'b\n'})

        results = merge_batch(repo, ['fix/issue-1', 'fix/issue-2'])
        assert set(results['merged']) == {'fix/issue-1', 'fix/issue-2'}
        assert results['conflicted'] == []
        assert results['skipped'] == []

    def test_risk_order_config_first(self, tmp_path):
        """Config-only branches are merged before single-file."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'src/code.py': 'code\n'})
        _create_branch_with_changes(repo, 'fix/issue-2', {'docs/README.md': 'docs\n'})

        risk_map = {
            'fix/issue-1': 'single-file',
            'fix/issue-2': 'config-only',
        }

        results = merge_batch(repo, ['fix/issue-1', 'fix/issue-2'], risk_map)

        # Both should merge (no overlap)
        assert 'fix/issue-2' in results['merged']
        assert 'fix/issue-1' in results['merged']

    def test_overlapping_branch_skipped(self, tmp_path):
        """Branch overlapping with already-merged files is skipped."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'shared.txt': 'version A\n'})
        _create_branch_with_changes(repo, 'fix/issue-2', {'shared.txt': 'version B\n'})

        risk_map = {
            'fix/issue-1': 'config-only',
            'fix/issue-2': 'single-file',
        }

        results = merge_batch(repo, ['fix/issue-1', 'fix/issue-2'], risk_map)

        # First one merges (config-only has priority), second is skipped
        assert 'fix/issue-1' in results['merged']
        assert 'fix/issue-2' in results['skipped']

    def test_empty_branches(self, tmp_path):
        """Empty branch list returns empty results."""
        repo = _init_repo(tmp_path)
        results = merge_batch(repo, [])
        assert results == {'merged': [], 'conflicted': [], 'skipped': []}

    def test_default_risk_map(self, tmp_path):
        """Without risk_map, all branches default to single-file."""
        repo = _init_repo(tmp_path)
        _create_branch_with_changes(repo, 'fix/issue-1', {'file_a.txt': 'a\n'})

        results = merge_batch(repo, ['fix/issue-1'])
        assert results['merged'] == ['fix/issue-1']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

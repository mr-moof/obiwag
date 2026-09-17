#!/usr/bin/env python3
"""
Swarm Merge Helper - Orchestrates safe merging of parallel issue branches.

Detects file overlaps, merges non-conflicting branches in risk order
(config-only first), and reports results.

Usage:
    python tools/swarm_merge.py --repo <path> --branches fix/issue-5,fix/issue-7 \\
        --risk-map '{"fix/issue-5":"config-only","fix/issue-7":"single-file"}'

Returns JSON: {"merged": [...], "conflicted": [...], "skipped": [...]}
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


RISK_ORDER = ['config-only', 'single-file', 'multi-file']
GIT_TIMEOUT_SEC = 60
FALLBACK_DEFAULT_BRANCH = 'main'


class GitCommandError(RuntimeError):
    """A required git inspection failed, so merge safety cannot be established."""


def run_git(repo: str, *args: str) -> Tuple[int, str, str]:
    """Run a git command in the given repo directory.

    Returns:
        Tuple of (return_code, stdout, stderr).
    """
    cmd = ['git', '-C', repo] + list(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, '', f"git command timed out after {GIT_TIMEOUT_SEC}s: {' '.join(args)}"
    except OSError as exc:
        return 127, '', f"git command could not start: {exc}"


def get_default_branch(repo: str) -> str:
    """Resolve the remote default branch from local ``origin/HEAD`` metadata."""
    rc, stdout, _ = run_git(
        repo, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'
    )
    if rc == 0 and stdout.startswith('origin/'):
        branch = stdout[len('origin/'):]
        if branch and '..' not in branch and '//' not in branch:
            return branch
    return FALLBACK_DEFAULT_BRANCH


def get_changed_files(repo: str, branch: str, base: Optional[str] = None) -> Set[str]:
    """Get the set of files changed on a branch relative to base.

    Args:
        repo: Path to the git repository.
        branch: Branch name to check.
        base: Base branch to diff against.

    Returns:
        Set of file paths changed on the branch.
    """
    resolved_base = base or get_default_branch(repo)
    rc, stdout, stderr = run_git(repo, 'diff', '--name-only', f'{resolved_base}...{branch}')
    if rc != 0:
        raise GitCommandError(stderr or f"unable to inspect branch {branch}")
    return set(line.strip() for line in stdout.splitlines() if line.strip())


def detect_overlaps(
    repo: str,
    branches: List[str],
    base: Optional[str] = None,
) -> Dict[str, List[str]]:
    """Detect files touched by multiple branches.

    Args:
        repo: Path to the git repository.
        branches: List of branch names.
        base: Base branch to diff against.

    Returns:
        Dict mapping filenames to list of branches that touch them,
        only for files touched by 2+ branches.
    """
    file_to_branches: Dict[str, List[str]] = {}

    resolved_base = base or get_default_branch(repo)
    for branch in branches:
        changed = get_changed_files(repo, branch, resolved_base)
        for f in changed:
            if f not in file_to_branches:
                file_to_branches[f] = []
            file_to_branches[f].append(branch)

    return {f: bs for f, bs in file_to_branches.items() if len(bs) > 1}


def try_merge(repo: str, branch: str) -> Tuple[bool, str]:
    """Attempt to merge a branch using --no-commit --no-ff.

    On conflict, aborts the merge automatically.

    Args:
        repo: Path to the git repository.
        branch: Branch to merge.

    Returns:
        Tuple of (success, message).
    """
    rc, stdout, stderr = run_git(repo, 'merge', '--no-commit', '--no-ff', branch)

    if rc != 0:
        # Abort the failed merge
        abort_rc, _, abort_err = run_git(repo, 'merge', '--abort')
        detail = stderr or stdout or 'merge conflict'
        if abort_rc != 0:
            detail += f"; merge abort failed: {abort_err}"
        return False, detail

    # Commit the merge
    rc, stdout, stderr = run_git(
        repo, '-c', 'user.email=swarm@obiwag.local',
        '-c', 'user.name=Obi Swarm',
        'commit', '-m', f'Merge {branch} (swarm auto-merge)',
    )
    if rc != 0:
        abort_rc, _, abort_err = run_git(repo, 'merge', '--abort')
        detail = f'commit failed: {stderr or stdout}'
        if abort_rc != 0:
            detail += f'; merge abort failed: {abort_err}'
        return False, detail

    return True, f'merged {branch}'


def merge_batch(
    repo: str,
    branches: List[str],
    risk_map: Optional[Dict[str, str]] = None,
    base: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge branches in risk order, skipping those with overlaps against higher-priority merges.

    Args:
        repo: Path to the git repository.
        branches: List of branch names to merge.
        risk_map: Dict mapping branch name to risk tier.
        base: Base branch name.

    Returns:
        Dict with branch lists plus an `errors` map containing every failed branch's reason.
    """
    if risk_map is None:
        risk_map = {b: 'single-file' for b in branches}

    resolved_base = base or get_default_branch(repo)

    # Sort branches by risk tier (config-only first)
    def sort_key(branch: str) -> int:
        tier = risk_map.get(branch, 'single-file')
        return RISK_ORDER.index(tier) if tier in RISK_ORDER else 1

    sorted_branches = sorted(branches, key=sort_key)

    # Track which files have been merged already
    merged_files: Set[str] = set()
    results: Dict[str, Any] = {
        'merged': [],
        'conflicted': [],
        'skipped': [],
        'errors': {},
    }

    for branch in sorted_branches:
        try:
            changed = get_changed_files(repo, branch, resolved_base)
        except GitCommandError as exc:
            results['conflicted'].append(branch)
            results['errors'][branch] = str(exc)
            continue

        # Check for overlap with already-merged files
        overlap = changed & merged_files
        if overlap:
            results['skipped'].append(branch)
            continue

        success, message = try_merge(repo, branch)
        if success:
            results['merged'].append(branch)
            merged_files.update(changed)
        else:
            results['conflicted'].append(branch)
            results['errors'][branch] = message

    return results


def main():
    parser = argparse.ArgumentParser(
        description='Merge parallel issue branches safely in risk order.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('--repo', '-r', required=True, help='Path to git repository')
    parser.add_argument('--branches', '-b', required=True,
                        help='Comma-separated branch names')
    parser.add_argument('--risk-map', '-m', default='{}',
                        help='JSON mapping branch names to risk tiers')
    parser.add_argument('--base', default=None,
                        help='Base branch (default: resolved origin/HEAD, then main)')

    args = parser.parse_args()

    branches = [b.strip() for b in args.branches.split(',') if b.strip()]
    risk_map = json.loads(args.risk_map)

    results = merge_batch(args.repo, branches, risk_map, args.base)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()

"""Shared path resolution primitives for Obi Wag hooks.

Centralizes repo-root discovery, installation-root lookup, data-dir
resolution, and directory creation so that every hook module uses the
same candidate list and validation logic.

Moved here from git_sync, version, drift_detector, memory_reader, and
session_state as part of issues #121 and #124.
"""

import os
from pathlib import Path
from typing import List, Optional


def get_obi_platform() -> str:
    """Return the active Obi runtime platform.

    Defaults to ``claude`` for backward compatibility. Set
    ``OBI_PLATFORM=codex`` when running under Codex.
    """
    platform = os.environ.get('OBI_PLATFORM', 'claude').strip().lower()
    if platform in {'codex', 'claude'}:
        return platform
    return 'claude'


# ---------------------------------------------------------------------------
# Repo-root discovery
# ---------------------------------------------------------------------------
#
# Single source of truth for the obiwag-agents repo candidate list.  All
# three public discovery functions (``get_obiwag_repo_path``,
# ``get_source_repo_path``, ``get_obi_data_dir``) walk this exact list via
# the shared ``_find_repo`` engine, so the candidate list can no longer
# drift between them (issue #180 / OPT-07).


def _build_repo_candidates() -> List[Path]:
    """Build the unified repo candidate list."""
    home = Path.home()
    return [
        Path('C:/src/obiwag-agents'),
        home / 'source' / 'obiwag-agents',
        home / 'repos' / 'obiwag-agents',
        home / 'projects' / 'obiwag-agents',
    ]


# Single shared candidate-list object walked by every discovery function.
# Defining it once (rather than rebuilding per function) is the whole point
# of issue #180: there is now exactly one list for the candidates to drift
# within, and ``test_paths.py`` asserts the three functions reference it.
_REPO_CANDIDATES: List[Path] = _build_repo_candidates()


def _default_repo_candidates() -> List[Path]:
    """Return the single shared unified repo candidate list."""
    return _REPO_CANDIDATES


def _find_repo(marker: str, candidates: Optional[List[Path]] = None) -> Optional[Path]:
    """Locate the obiwag-agents repo root by walking candidate paths.

    Honors the ``OBIWAG_SOURCE`` environment variable first, then the
    supplied ``candidates`` (or the unified default candidate list).  A
    candidate is accepted when ``<candidate>/<marker>`` is a directory.

    The directory check uses ``os.path.isdir`` so existing test seams
    that patch ``core.paths.os.path.isdir`` continue to intercept
    candidate validation.

    Args:
        marker: Subdirectory whose presence validates a candidate
            (e.g. ``'.git'``, ``'hooks'``, ``'.obi'``).
        candidates: Optional explicit candidate list.  When provided, the
            default candidates are NOT checked — pass an explicit list to
            isolate tests from the real repo on disk.

    Returns:
        Path to the repo root, or None if not found.
    """
    env_path = os.environ.get('OBIWAG_SOURCE')
    if env_path and os.path.isdir(os.path.join(env_path, marker)):
        return Path(env_path)

    search = candidates if candidates is not None else _default_repo_candidates()

    for path in search:
        if os.path.isdir(os.path.join(str(path), marker)):
            return path

    return None


def get_obiwag_repo_path(search_roots: Optional[List[Path]] = None) -> Optional[Path]:
    """Find the obiwag-agents source repository.

    Checks the OBIWAG_SOURCE environment variable first, then falls
    back to ``search_roots`` (or the default hardcoded candidates).
    Validates each candidate by verifying that a ``.git`` subdirectory
    exists.

    Args:
        search_roots: Optional list of candidate paths to check instead
            of the default hardcoded candidates.  When provided, the
            default candidates are NOT checked — pass an explicit list
            to isolate tests from the real repo on disk.

    Returns:
        Path to the repo root, or None if not found.
    """
    return _find_repo('.git', candidates=search_roots)


# Alias kept for version.py backward compatibility.
get_obi_source_repo = get_obiwag_repo_path


def get_source_repo_path() -> Optional[str]:
    """Find the obiwag-agents repo by verifying it contains ``hooks/``.

    This is the drift_detector variant: it validates via the ``hooks/``
    subdirectory (not ``.git``).  Checks OBIWAG_SOURCE first, then
    hardcoded candidates.  Returns ``str`` for backward compatibility
    with drift_detector callers.

    Returns:
        String path to repo root, or None if not found or hooks/ missing.
    """
    root = _find_repo('hooks')
    return str(root) if root is not None else None


# ---------------------------------------------------------------------------
# Installation-root and data-dir helpers
# ---------------------------------------------------------------------------

def get_obi_root() -> Path:
    """Return the deployed Obi installation directory for the active platform."""
    override = os.environ.get('OBI_ROOT')
    if override:
        return Path(override).expanduser()

    if get_obi_platform() == 'codex':
        return Path.home() / '.codex'

    return Path.home() / '.claude'


def get_obi_data_dir() -> Optional[str]:
    """Find the ``.obi`` data directory inside the source repo.

    Checks OBIWAG_SOURCE first, then the unified candidate
    directories, returning the ``.obi`` subdirectory of the first repo
    root that contains one.

    Returns:
        String path to the ``.obi`` directory, or None if not found.
    """
    root = _find_repo('.obi')
    return os.path.join(str(root), '.obi') if root is not None else None


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def ensure_dir(path: str) -> None:
    """Create *path* and any missing parents (no-op if it exists)."""
    os.makedirs(path, exist_ok=True)

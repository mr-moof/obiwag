"""Version management for Obi Wag.

Provides version information and update checking capabilities.
"""

import functools
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

import yaml

# Re-exported from paths.py for backward compatibility with older callers.
from .paths import get_obi_root, get_obi_source_repo  # noqa: F401


_FALLBACK_VERSION = "unknown"


@functools.lru_cache(maxsize=1)
def get_current_version() -> str:
    """Resolve the current version from ``tools/version.yaml`` (lazily, cached).

    Prefers the source repo copy (most current), then the deployed copy
    under ``~/.claude``. Falls back to ``_FALLBACK_VERSION`` only when
    neither file is readable — the hardcoded literal that used to sit
    in this module is now the version.yaml source of truth (issue #127).

    Resolution is deferred until first call (issue #175): importing this
    module performs zero filesystem I/O. The result is cached for the
    process lifetime via ``functools.lru_cache``.
    """
    candidates = []
    source_repo = get_obi_source_repo()
    if source_repo is not None:
        candidates.append(source_repo / 'tools' / 'version.yaml')
    candidates.append(get_obi_root() / 'tools' / 'version.yaml')

    for path in candidates:
        if not path.is_file():
            continue
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            version = data.get('version')
            if isinstance(version, str) and version:
                return version

    return _FALLBACK_VERSION


def __getattr__(name: str) -> str:
    """Lazily resolve the deprecated module-level ``CURRENT_VERSION`` (PEP 562).

    Deprecated shim retained for one release (issue #175). ``CURRENT_VERSION``
    used to be a module-level constant computed at import time; it now resolves
    lazily by delegating to :func:`get_current_version`. Triggered by both
    ``version.CURRENT_VERSION`` and ``from .version import CURRENT_VERSION``.
    Prefer calling ``get_current_version()`` directly.
    """
    if name == 'CURRENT_VERSION':
        return get_current_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class VersionInfo:
    """Version information container."""
    version: str
    commit_hash: Optional[str] = None
    commit_date: Optional[str] = None
    is_dirty: bool = False

    def __str__(self) -> str:
        """Format version for display."""
        parts = [f"v{self.version}"]
        if self.commit_hash:
            parts.append(f"({self.commit_hash[:7]})")
        if self.is_dirty:
            parts.append("[modified]")
        return " ".join(parts)


def get_version_info() -> VersionInfo:
    """Get version information including git details if available.

    Uses the source repo (~/source/obiwag-agents) for git info since
    the deployed copy at ~/.claude is not a git repository.

    Returns:
        VersionInfo object with version, commit hash, date, and dirty status
    """
    info = VersionInfo(version=get_current_version())

    source_repo = get_obi_source_repo()
    if source_repo is None:
        return info

    try:
        # Get current commit hash
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            info.commit_hash = result.stdout.strip()

        # Get commit date
        result = subprocess.run(
            ['git', 'log', '-1', '--format=%ci'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            info.commit_date = result.stdout.strip()

        # Check if working directory is dirty
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            info.is_dirty = bool(result.stdout.strip())

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return info


def check_for_updates() -> Tuple[bool, str, List[str]]:
    """Check if updates are available from the remote repository.

    Uses the source repo (~/source/obiwag-agents) for git operations.

    Returns:
        Tuple of (has_updates, message, list of commit summaries)
    """
    source_repo = get_obi_source_repo()

    if source_repo is None:
        return False, "Source repository not found at ~/source/obiwag-agents", []

    try:
        # Fetch from origin
        result = subprocess.run(
            ['git', 'fetch', 'origin', 'master'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            return False, f"Failed to fetch updates: {result.stderr.strip()}", []

        # Check if there are new commits
        result = subprocess.run(
            ['git', 'rev-list', '--count', 'HEAD..origin/master'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            return False, "Failed to check for updates", []

        count = int(result.stdout.strip())
        if count == 0:
            return False, "Already up to date", []

        # Get commit summaries
        result = subprocess.run(
            ['git', 'log', '--oneline', 'HEAD..origin/master'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=5
        )
        commits = []
        if result.returncode == 0:
            commits = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]

        return True, f"{count} update(s) available", commits

    except subprocess.TimeoutExpired:
        return False, "Timeout checking for updates", []
    except (FileNotFoundError, OSError) as e:
        return False, f"Error checking for updates: {str(e)}", []
    except ValueError:
        return False, "Error parsing update count", []


def apply_updates() -> Tuple[bool, str]:
    """Apply updates from the remote repository.

    This is a two-stage process:
    1. Pull updates in the source repo (~/source/obiwag-agents)
    2. Run deploy script to copy to ~/.claude

    Returns:
        Tuple of (success, message)
    """
    source_repo = get_obi_source_repo()

    if source_repo is None:
        return False, "Source repository not found at ~/source/obiwag-agents"

    try:
        # Stage 1: Pull updates in source repo
        result = subprocess.run(
            ['git', 'pull', 'origin', 'master'],
            cwd=str(source_repo),
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            return False, f"Failed to pull updates: {result.stderr.strip()}"

        # Stage 2: Run deploy script
        deploy_script = source_repo / 'tools' / 'deploy.ps1'
        if deploy_script.exists():
            result = subprocess.run(
                ['powershell.exe', '-ExecutionPolicy', 'Bypass', '-File', str(deploy_script)],
                cwd=str(source_repo),
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                return False, f"Pull succeeded but deploy failed: {result.stderr.strip()}"

            return True, "Updates pulled and deployed successfully"
        else:
            return True, "Updates pulled (no deploy script found - manual deploy may be needed)"

    except subprocess.TimeoutExpired:
        return False, "Timeout applying updates"
    except (FileNotFoundError, OSError) as e:
        return False, f"Error applying updates: {str(e)}"


def get_version_display() -> str:
    """Get a formatted version string for display in welcome messages."""
    info = get_version_info()
    return str(info)

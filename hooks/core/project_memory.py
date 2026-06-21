"""Project memory path resolution and backup for Obi Memory System.

Resolves the Claude Code project memory directory dynamically and provides
backup/restore functionality for session-end snapshots.
"""

import json
import os
import shutil
import time
from typing import Dict, Optional

from core.paths import get_obi_root


# Maximum number of timestamped snapshots to retain
MAX_SNAPSHOTS = 3


def _encode_path_for_claude(path: str) -> str:
    """Encode an absolute path to Claude Code's project directory name format.

    Claude Code encodes project paths by replacing ':\\' with '--' and
    remaining separators with '-'. For example:
        C:\\Users\\user -> C--Users-user
    """
    # Normalize to forward slashes first, then apply encoding
    normalized = path.replace("\\", "/")
    # Replace :/ with -- (drive letter)
    encoded = normalized.replace(":/", "--")
    # Replace remaining / with -
    encoded = encoded.replace("/", "-")
    # Remove trailing separator if present
    return encoded.rstrip("-")


def get_project_memory_dir(project_path: Optional[str] = None) -> Optional[str]:
    """Resolve the Claude Code project memory directory dynamically.

    Args:
        project_path: The project root path to encode. When None, searches for
            a populated memory dir among cwd, home, and (as a last resort) any
            populated ``~/.claude/projects/*/memory`` directory. Claude Code
            keys project dirs by the cwd at session start, not by user home,
            so defaulting to home alone misses the active project memory and
            silently breaks the Stop-hook backup.

    Returns:
        Full path to the project memory directory, or None if not found.
    """
    home = os.path.expanduser("~")
    projects_dir = str(get_obi_root() / "projects")
    if not os.path.isdir(projects_dir):
        return None

    if project_path is not None:
        return _resolve_encoded_memory_dir(projects_dir, project_path)

    # No explicit path — try cwd first, then home, then any populated dir.
    seen = set()
    for candidate_path in (os.getcwd(), home):
        if candidate_path in seen:
            continue
        seen.add(candidate_path)
        resolved = _resolve_encoded_memory_dir(projects_dir, candidate_path)
        if resolved and _has_files(resolved):
            return resolved

    # Last resort: any populated memory dir, most recently modified.
    try:
        best = None
        best_mtime = -1.0
        for entry in os.listdir(projects_dir):
            mem = os.path.join(projects_dir, entry, "memory")
            if not os.path.isdir(mem) or not _has_files(mem):
                continue
            try:
                mtime = os.path.getmtime(mem)
            except OSError:
                continue
            if mtime > best_mtime:
                best_mtime = mtime
                best = mem
        if best:
            return best
    except OSError:
        pass

    return None


def _resolve_encoded_memory_dir(projects_dir: str, project_path: str) -> Optional[str]:
    """Return memory dir for `project_path` if it exists, else None."""
    encoded = _encode_path_for_claude(project_path)
    candidate = os.path.join(projects_dir, encoded, "memory")
    if os.path.isdir(candidate):
        return candidate
    try:
        for entry in os.listdir(projects_dir):
            if entry.lower() == encoded.lower():
                candidate = os.path.join(projects_dir, entry, "memory")
                if os.path.isdir(candidate):
                    return candidate
    except OSError:
        pass
    return None


def _has_files(directory: str) -> bool:
    """True if directory exists and contains at least one regular file."""
    try:
        for name in os.listdir(directory):
            if os.path.isfile(os.path.join(directory, name)):
                return True
    except OSError:
        pass
    return False


def get_backup_dir() -> str:
    """Get the backup directory for project memory snapshots."""
    return str(get_obi_root() / ".obi" / "backups" / "project-memory")


def _fingerprint_file(filepath: str) -> Dict:
    """Compute a cheap fingerprint for a file (path + size + mtime).

    mtime is truncated to integer seconds to avoid Windows filesystem
    precision issues across directories.
    """
    stat = os.stat(filepath)
    return {
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
    }


def _fingerprint_directory(dirpath: str) -> Dict[str, Dict]:
    """Compute fingerprints for all files in a directory."""
    fingerprints = {}
    for fname in os.listdir(dirpath):
        fpath = os.path.join(dirpath, fname)
        if os.path.isfile(fpath):
            try:
                fingerprints[fname] = _fingerprint_file(fpath)
            except OSError:
                pass
    return fingerprints


def _load_latest_index(backup_dir: str) -> Optional[Dict]:
    """Load the backup-index.json from the most recent snapshot."""
    if not os.path.isdir(backup_dir):
        return None

    snapshots = sorted(
        [d for d in os.listdir(backup_dir) if d.startswith("snapshot-")],
        reverse=True,
    )
    if not snapshots:
        return None

    index_path = os.path.join(backup_dir, snapshots[0], "backup-index.json")
    if not os.path.isfile(index_path):
        return None

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _has_changes(memory_dir: str, backup_dir: str) -> bool:
    """Check if memory directory has changed since last backup.

    Compares file fingerprints (relative_path + size + mtime) against
    the most recent backup index. Uses integer-second mtime with a
    2-second tolerance to handle Windows filesystem precision issues.
    """
    current = _fingerprint_directory(memory_dir)
    last_index = _load_latest_index(backup_dir)

    if last_index is None:
        # No previous backup exists
        return bool(current)

    previous = last_index.get("fingerprints", {})

    # Check for added or removed files
    if set(current.keys()) != set(previous.keys()):
        return True

    # Check for modified files (size or mtime with 2s tolerance)
    for fname, fp in current.items():
        prev_fp = previous.get(fname)
        if prev_fp is None:
            return True
        if fp["size"] != prev_fp["size"]:
            return True
        if abs(fp["mtime"] - prev_fp["mtime"]) > 2:
            return True

    return False


def _rotate_snapshots(backup_dir: str) -> None:
    """Keep only the most recent MAX_SNAPSHOTS snapshots."""
    snapshots = sorted(
        [d for d in os.listdir(backup_dir) if d.startswith("snapshot-")],
        reverse=True,
    )
    for old_snapshot in snapshots[MAX_SNAPSHOTS:]:
        old_path = os.path.join(backup_dir, old_snapshot)
        shutil.rmtree(old_path, ignore_errors=True)


def backup_project_memory() -> Optional[str]:
    """Backup project memory directory to .obi/backups/project-memory/.

    Uses fingerprint-based change detection to avoid unnecessary copies.
    Keeps the last 3 timestamped snapshots with rotation.

    Returns:
        Path to the new snapshot directory, or None if no backup was needed/possible.
    """
    memory_dir = get_project_memory_dir()
    if memory_dir is None or not os.path.isdir(memory_dir):
        return None

    # Check if there are any files to back up
    files = [f for f in os.listdir(memory_dir) if os.path.isfile(os.path.join(memory_dir, f))]
    if not files:
        return None

    backup_dir = get_backup_dir()

    # Check if anything has changed
    if not _has_changes(memory_dir, backup_dir):
        return None

    # Create timestamped snapshot
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    snapshot_dir = os.path.join(backup_dir, f"snapshot-{timestamp}")
    os.makedirs(snapshot_dir, exist_ok=True)

    # Copy files (flat copy, no subdirectories)
    fingerprints = {}
    copied = 0
    for fname in files:
        src = os.path.join(memory_dir, fname)
        dst = os.path.join(snapshot_dir, fname)
        try:
            shutil.copy2(src, dst)
            fingerprints[fname] = _fingerprint_file(src)
            copied += 1
        except OSError:
            pass

    if copied == 0:
        # Nothing was actually copied, clean up empty snapshot
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return None

    # Write sentinel file (clean up snapshot if this fails)
    index = {
        "timestamp": timestamp,
        "source": memory_dir,
        "file_count": copied,
        "fingerprints": fingerprints,
    }
    index_path = os.path.join(snapshot_dir, "backup-index.json")
    try:
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
    except OSError:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return None

    # Rotate old snapshots
    _rotate_snapshots(backup_dir)

    return snapshot_dir

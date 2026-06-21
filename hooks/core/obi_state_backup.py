"""Obi state backup for authoritative non-derivable .obi/ files.

Handles backup of `calibration.md` and `pending-learnings.json` — the
two top-level authoritative files under `~/.claude/.obi/` that are not
covered by project_memory.py (which handles `~/.claude/projects/<project>/memory/`)
or config-guardian (which handles `settings.json` and `settings.local.json`).

See `policies/data-safety.md` for the authoritative-vs-derivable
inventory and recovery procedure.
"""

import json
import os
import shutil
import time
from typing import Dict, List, Optional, Tuple

from core.paths import get_obi_root


# Keep more snapshots than project_memory (3) because these files change
# less frequently and recovery from a stale snapshot is costlier.
MAX_SNAPSHOTS = 5

# Relative paths under ~/.claude/.obi/ that are authoritative.
# Order matters only for readability in the snapshot index.
TRACKED_FILES: Tuple[str, ...] = (
    "calibration.md",
    "pending-learnings.json",
)


def _obi_dir() -> str:
    return str(get_obi_root() / ".obi")


def get_obi_state_backup_dir() -> str:
    """Return the obi-state snapshot root."""
    return os.path.join(_obi_dir(), "backups", "obi-state")


def _fingerprint(filepath: str) -> Dict:
    stat = os.stat(filepath)
    return {"size": stat.st_size, "mtime": int(stat.st_mtime)}


def _current_fingerprints(source_dir: str) -> Dict[str, Dict]:
    fingerprints = {}
    for name in TRACKED_FILES:
        full = os.path.join(source_dir, name)
        if os.path.isfile(full):
            try:
                fingerprints[name] = _fingerprint(full)
            except OSError:
                pass
    return fingerprints


def _latest_index(backup_dir: str) -> Optional[Dict]:
    if not os.path.isdir(backup_dir):
        return None
    snapshots = sorted(
        (d for d in os.listdir(backup_dir) if d.startswith("snapshot-")),
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


def _has_changes(current: Dict[str, Dict], backup_dir: str) -> bool:
    last = _latest_index(backup_dir)
    if last is None:
        return bool(current)
    previous = last.get("fingerprints", {})
    if set(current.keys()) != set(previous.keys()):
        return True
    for name, fp in current.items():
        prev = previous.get(name)
        if prev is None:
            return True
        if fp["size"] != prev["size"]:
            return True
        if abs(fp["mtime"] - prev["mtime"]) > 2:
            return True
    return False


def _rotate(backup_dir: str) -> None:
    snapshots = sorted(
        (d for d in os.listdir(backup_dir) if d.startswith("snapshot-")),
        reverse=True,
    )
    for old in snapshots[MAX_SNAPSHOTS:]:
        shutil.rmtree(os.path.join(backup_dir, old), ignore_errors=True)


def backup_obi_state() -> Optional[str]:
    """Snapshot authoritative .obi/ files if anything has changed.

    Returns path to the new snapshot directory, or None if nothing needed
    backing up / nothing could be written.
    """
    source_dir = _obi_dir()
    if not os.path.isdir(source_dir):
        return None

    current = _current_fingerprints(source_dir)
    if not current:
        return None

    backup_dir = get_obi_state_backup_dir()
    if not _has_changes(current, backup_dir):
        return None

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    snapshot_dir = os.path.join(backup_dir, f"snapshot-{timestamp}")
    try:
        os.makedirs(snapshot_dir, exist_ok=True)
    except OSError:
        return None

    copied: List[str] = []
    for name in current:
        src = os.path.join(source_dir, name)
        dst = os.path.join(snapshot_dir, name)
        try:
            shutil.copy2(src, dst)
            copied.append(name)
        except OSError:
            pass

    if not copied:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return None

    index = {
        "timestamp": timestamp,
        "source": source_dir,
        "file_count": len(copied),
        "files": copied,
        "fingerprints": {k: v for k, v in current.items() if k in copied},
    }
    try:
        with open(os.path.join(snapshot_dir, "backup-index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
    except OSError:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return None

    _rotate(backup_dir)
    return snapshot_dir

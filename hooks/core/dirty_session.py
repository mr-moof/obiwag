"""Dirty-session detection for the Stop hook (WI-1a).

Detects uncommitted changes (tracked modifications, staged changes, and
untracked files) via ``git status --porcelain`` and formats an advisory
nag message for session end.
"""

import subprocess
from typing import List, Dict, Optional


def _run_git_status(repo_path: str) -> Optional[subprocess.CompletedProcess]:
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result if result.returncode == 0 else None
    except Exception:
        return None


def is_repo_dirty(repo_path: str) -> bool:
    """Return True if the working tree has any uncommitted or untracked changes."""
    result = _run_git_status(repo_path)
    return result is not None and bool(result.stdout.strip())


def get_dirty_file_list(repo_path: str) -> List[Dict[str, str]]:
    """Parse ``git status --porcelain`` into a list of {status, path} dicts."""
    result = _run_git_status(repo_path)
    if result is None or not result.stdout.strip():
        return []

    files = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        path_part = line[3:]
        # Renames: "R  old.py -> new.py" — store the new path
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        files.append({"status": status, "path": path_part})
    return files


def format_dirty_session_nag(file_list: List[Dict[str, str]]) -> str:
    if not file_list:
        return ""

    count = len(file_list)
    msg = (
        f"[Git] Session ended with uncommitted changes in {count} file(s). "
        "Review and commit before next session."
    )

    if count <= 5:
        lines = [f"  {f['status']} {f['path']}" for f in file_list]
        msg += "\n" + "\n".join(lines)

    return msg

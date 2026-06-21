"""Persistent session state for Obi Memory System.

Provides session state that survives across hook invocations.
Each hook runs as a separate Python process, so in-memory state doesn't persist.
This module stores state in files to maintain continuity within a session.
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, Optional

from .paths import get_obi_root


def get_active_sessions_path() -> str:
    """Get path to active sessions directory."""
    return str(get_obi_root() / ".obi" / "active-sessions")


# Re-exported from paths.py (dedup of #124).
from .paths import ensure_dir  # noqa: F401


def get_state_dir() -> str:
    """Runtime state directory (~/.claude/.obi/state).

    Holds singleton run-state files (the current-session sentinel,
    last-maintenance marker) — distinct from active-sessions/, which holds one
    file per session.
    """
    return str(get_obi_root() / ".obi" / "state")


def get_current_session_path() -> str:
    """Path to the current-session sentinel written at SessionStart."""
    return os.path.join(get_state_dir(), "current-session.json")


def write_current_session(session_id: str) -> None:
    """Persist the active session id at SessionStart (OPT-04 #177).

    PostToolUse / PreCompact read this when their own input lacks a session_id,
    so a session that crosses an hour boundary no longer forks its state into a
    second hour-bucket-seeded file. Best-effort — never blocks startup.
    """
    try:
        ensure_dir(get_state_dir())
        payload = {
            "session_id": session_id,
            "started_at": datetime.now().isoformat(),
            "pid": os.getpid(),
        }
        with open(get_current_session_path(), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass  # Sentinel is an optimization; never block startup.


def read_current_session(max_age_hours: int = 24) -> Optional[str]:
    """Return the sentinel session id if present and fresher than max_age_hours.

    Returns None when the file is missing, unreadable, malformed, or stale —
    callers then fall back to their hour-bucket seed.
    """
    path = get_current_session_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        session_id = data.get("session_id")
        started_at = data.get("started_at")
        if not session_id or not started_at:
            return None
        age = datetime.now() - datetime.fromisoformat(started_at)
        if age.total_seconds() > max_age_hours * 3600:
            return None
        return session_id
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def clear_current_session() -> None:
    """Remove the current-session sentinel (called at Stop). Best-effort."""
    path = get_current_session_path()
    try:
        if os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


class SessionState:
    """Persistent session state that survives across hook invocations.

    State is stored per-session in a JSON file that persists between
    hook invocations within the same session.
    """

    def __init__(self, session_id: str):
        """Initialize session state.

        Args:
            session_id: Unique identifier for this session
        """
        self.session_id = session_id
        self.state_dir = get_active_sessions_path()
        self.state_file = os.path.join(self.state_dir, f"{session_id}.json")
        ensure_dir(self.state_dir)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from session state.

        Args:
            key: The key to retrieve
            default: Default value if key doesn't exist

        Returns:
            The value or default
        """
        state = self._load()
        return state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in session state.

        Args:
            key: The key to set
            value: The value to store
        """
        state = self._load()
        state[key] = value
        state['_last_updated'] = datetime.now().isoformat()
        self._save(state)

    def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter, returning new value.

        Args:
            key: The counter key
            amount: Amount to increment by

        Returns:
            The new value
        """
        current = self.get(key, 0)
        new_value = current + amount
        self.set(key, new_value)
        return new_value

    def append_to_list(self, key: str, value: Any) -> None:
        """Append a value to a list in state.

        Args:
            key: The list key
            value: The value to append
        """
        current = self.get(key, [])
        if not isinstance(current, list):
            current = []
        current.append(value)
        self.set(key, current)

    def get_all(self) -> Dict[str, Any]:
        """Get all session state.

        Returns:
            Complete state dictionary
        """
        return self._load()

    def _load(self) -> Dict[str, Any]:
        """Load state from file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return self._default_state()
        return self._default_state()

    def _default_state(self) -> Dict[str, Any]:
        """Return default state for new sessions."""
        return {
            'session_id': self.session_id,
            'task_type': 'unknown',
            'tool_count': 0,
            'tools_used': [],
            '_created': datetime.now().isoformat(),
            '_last_updated': datetime.now().isoformat(),
        }

    def _save(self, state: Dict[str, Any]) -> None:
        """Save state to file."""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except IOError:
            pass  # Fail silently to not block Claude Code

    def cleanup(self) -> None:
        """Remove state file when session ends."""
        if os.path.exists(self.state_file):
            try:
                os.unlink(self.state_file)
            except IOError:
                pass


def get_session_state(session_id: Optional[str] = None) -> Optional[SessionState]:
    """Get session state, creating if needed.

    Args:
        session_id: Session ID. If None, tries to find most recent active session.

    Returns:
        SessionState instance or None if no session found
    """
    if session_id:
        return SessionState(session_id)

    # Try to find most recent active session
    active_dir = get_active_sessions_path()
    if os.path.isdir(active_dir):
        sessions = []
        for f in os.listdir(active_dir):
            if f.endswith('.json'):
                filepath = os.path.join(active_dir, f)
                try:
                    mtime = os.path.getmtime(filepath)
                    sessions.append((filepath, mtime))
                except OSError:
                    continue

        if sessions:
            # Most recently modified
            latest = max(sessions, key=lambda x: x[1])
            session_id = os.path.basename(latest[0]).replace('.json', '')
            return SessionState(session_id)

    return None


def cleanup_old_sessions(max_age_hours: int = 24) -> int:
    """Clean up session state files older than max_age_hours.

    Args:
        max_age_hours: Maximum age in hours before cleanup

    Returns:
        Number of files cleaned up
    """
    active_dir = get_active_sessions_path()
    if not os.path.isdir(active_dir):
        return 0

    cleaned = 0
    cutoff = datetime.now().timestamp() - (max_age_hours * 3600)

    for f in os.listdir(active_dir):
        if f.endswith('.json'):
            filepath = os.path.join(active_dir, f)
            try:
                if os.path.getmtime(filepath) < cutoff:
                    os.unlink(filepath)
                    cleaned += 1
            except OSError:
                continue

    return cleaned

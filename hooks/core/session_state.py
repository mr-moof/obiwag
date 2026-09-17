"""Persistent session state for Obi Memory System.

Provides session state that survives across hook invocations.
Each hook runs as a separate Python process, so in-memory state doesn't persist.
This module stores state in files to maintain continuity within a session.

Concurrency (issue #202)
------------------------
Several hooks write this file: PostToolUse on every tool call, PreCompact,
UserPromptSubmit, and Stop.  Two guarantees are needed and neither is free on
Windows:

* **Atomic publish** -- writes go to a temp file and are ``os.replace``d, so a
  reader never sees a half-written file.
* **Serialized read-modify-write** -- ``os.replace`` alone does NOT prevent lost
  updates, and on Windows it does not even make reads safe: CPython's ``open()``
  grants no ``FILE_SHARE_DELETE``, so a concurrent reader makes the replace fail
  with ``PermissionError: [WinError 5]``.  Because ``PermissionError`` subclasses
  ``OSError``, a naive ``except OSError: return default`` turns that into
  "no state" -- which is exactly the ``task_type: unknown`` bug of issue #202.

So *every* access, reads included, goes through a sidecar lock file which is
never replaced and therefore never hits the sharing conflict.  The lock is an OS
lock (``msvcrt``/``fcntl``) released by the kernel on process death, so a crashed
holder needs no age-based reclamation and a slow holder is never displaced.

The locks are NOT reentrant.  ``transaction()`` acquires exactly once and uses the
``*_unlocked`` helpers internally; public accessors acquire only when called
outside a transaction.  Acquisition is bounded (hooks must never hang Claude
Code) and callers fail closed rather than proceeding unlocked.
"""

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .paths import get_obi_root

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


# Bounded lock acquisition: a hook must never block Claude Code, so give up and
# let the caller fail closed rather than wait indefinitely. 1s sits well inside
# the 3-5s hook timeouts in settings.json while being long enough that ordinary
# contention does not drop work -- 250ms was measurably too tight.
LOCK_TIMEOUT_SEC = 1.0
LOCK_POLL_SEC = 0.005

# Windows file-sharing violations (endpoint security scanning a file, a racing
# replace) are transient. Retry the open itself briefly before giving up.
IO_RETRIES = 5
IO_RETRY_SEC = 0.02


class StateUnavailable(Exception):
    """Session state could not be read or written this time.

    Raised for BOTH causes, because callers react identically to both:

    * the per-session lock was still held after ``LOCK_TIMEOUT_SEC``;
    * the file itself could not be read or published after ``IO_RETRIES``
      (on Windows a concurrent reader or a scanner can hold it).

    Callers must fail closed -- skip the optional work. Never proceed unlocked,
    and never propagate this to Claude Code.
    """


@contextmanager
def _file_lock(lock_path: str, timeout: float = LOCK_TIMEOUT_SEC):
    """Hold an OS lock on ``lock_path`` for the duration of the block.

    Uses ``msvcrt.locking`` (Windows) or ``fcntl.flock`` (POSIX) on a dedicated
    sidecar file.  Both are released by the kernel if the holder dies, so no
    stale-lock heuristic is needed -- and neither may be acquired twice by the
    same process (see the module docstring on reentrancy).

    Raises:
        StateUnavailable: if the lock is still held after ``timeout``.
    """
    deadline = time.monotonic() + timeout
    handle = None
    acquired = False
    try:
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        except OSError:
            pass

        while True:
            if handle is None:
                try:
                    handle = open(lock_path, 'a+b')
                except OSError:
                    # Cannot even create the lock file -- treat as unavailable
                    # rather than silently running unserialized.
                    raise StateUnavailable(lock_path)
                # msvcrt.locking locks nbytes from the CURRENT file position, and
                # 'a+b' can position at EOF. The lock file is only ever a
                # rendezvous object and stays empty, but seek to 0 explicitly so
                # every process locks the SAME byte range regardless -- otherwise
                # anything that ever wrote to this file would silently give each
                # process its own range and no mutual exclusion at all.
                try:
                    handle.seek(0)
                except OSError:
                    raise StateUnavailable(lock_path)
            try:
                if msvcrt is not None:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StateUnavailable(lock_path)
                time.sleep(LOCK_POLL_SEC)

        yield
    finally:
        if handle is not None:
            # Only unlock what we actually locked; unlocking an unheld range
            # raises, and swallowing that would hide a genuine release failure.
            if acquired:
                try:
                    if msvcrt is not None:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    elif fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            # Closing the handle releases the OS lock even if the explicit
            # unlock above failed (verified: close alone releases it).
            try:
                handle.close()
            except OSError:
                pass


@contextmanager
def bounded_file_lock(lock_path: str, timeout: float = LOCK_TIMEOUT_SEC):
    """Public bounded lock for small hook-owned state files outside SessionState."""
    with _file_lock(lock_path, timeout=timeout):
        yield


def _safe_session_id(session_id: str) -> str:
    """Reduce a session id to a single safe filename component.

    Strips directory separators, drive letters and traversal so a payload-supplied
    id cannot place the state file outside ``active-sessions/``.

    A real session id (UUID) passes through untouched. Anything that had to be
    rewritten -- or that was long enough to truncate -- gets a short digest of the
    ORIGINAL appended, because substitution alone is lossy: ``a/b`` and ``a:b``
    would otherwise both become ``a_b`` and share one state file.
    """
    text = str(session_id) if session_id else 'unknown'
    cleaned = re.sub(r'[^A-Za-z0-9._-]', '_', text).lstrip('.')

    if cleaned == text and len(cleaned) <= 128:
        return cleaned

    digest = hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:8]
    return f"{(cleaned or 'unknown')[:119]}-{digest}"


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
        # Sanitize before it reaches the filesystem. session_id arrives from a
        # hook payload; a value like "../../escaped" otherwise writes the state
        # file AND its lock outside active-sessions/ (verified). Claude Code sends
        # a UUID, so this is defence-in-depth, but a state writer that can be
        # steered out of its own directory should not exist.
        self.state_file = os.path.join(self.state_dir, f"{_safe_session_id(session_id)}.json")
        self.lock_file = self.state_file + '.lock'
        # Instance-local reentrancy guard plus the in-flight transaction buffer.
        # Safe because each hook is its own short-lived process and instances are
        # never shared across processes -- cross-process serialization is the OS
        # lock's job.
        self._in_transaction = False
        self._txn_state: Optional[Dict[str, Any]] = None
        ensure_dir(self.state_dir)

    # -- transaction primitive ------------------------------------------------

    @contextmanager
    def transaction(self):
        """Serialize a read-modify-write against other processes.

        Yields the mutable state dict; whatever it contains on clean exit is
        persisted atomically.  The lock is taken exactly once -- nested public
        accessors detect ``_in_transaction`` and use the unlocked helpers, since
        the OS locks are not reentrant and would otherwise self-deadlock until
        the acquisition budget expired.

        Raises:
            StateUnavailable: lock busy past the budget, or the write failed.
                Callers fail closed.
        """
        if self._in_transaction:
            # Reuse the OUTER transaction's buffer. Re-loading from disk here
            # would give the nested block a separate dict, and the outer save
            # would then overwrite whatever it wrote -- a lost update inside a
            # single process. The outer block performs the one commit.
            yield self._txn_state
            return

        with _file_lock(self.lock_file):
            self._in_transaction = True
            try:
                state = self._load_unlocked()
                self._txn_state = state
                yield state
                state['_last_updated'] = datetime.now().isoformat()
                self._save_unlocked(state)
            finally:
                self._in_transaction = False
                self._txn_state = None

    # -- reads ----------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from session state.

        Best-effort by design: existing callers treat state as advisory, so a
        busy lock yields ``default`` rather than raising.  Use ``get_all()`` when
        you must distinguish "absent" from "could not read".
        """
        try:
            return self.get_all().get(key, default)
        except StateUnavailable:
            return default

    def get_all(self) -> Dict[str, Any]:
        """Return the complete state dict.

        Raises:
            StateUnavailable: if the lock is busy.  Callers that must not
                silently substitute defaults (Stop resolving ``task_type``)
                should catch this explicitly.
        """
        if self._in_transaction:
            # Return the live buffer so a nested read sees the enclosing
            # transaction's uncommitted mutations.
            return self._txn_state if self._txn_state is not None else {}
        with _file_lock(self.lock_file):
            return self._load_unlocked()

    # -- mutations ------------------------------------------------------------

    def set(self, key: str, value: Any) -> bool:
        """Set a value. Returns True if persisted, False if the lock was busy."""
        return self.update(**{key: value})

    def update(self, **fields: Any) -> bool:
        """Apply several fields in ONE transaction.

        Two ``set()`` calls would be two read-modify-write cycles and twice the
        window for a concurrent writer to clobber one of them.
        """
        try:
            with self.transaction() as state:
                state.update(fields)
            return True
        except StateUnavailable:
            return False

    def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter inside one transaction, returning the new value.

        The read and the write must both happen under the lock, otherwise the
        increment is a classic lost-update race.  Returns ``0`` if the lock was
        busy and the increment was therefore dropped -- deliberately NOT a second
        read, which would wait out the acquisition budget all over again and could
        cost a 3 s hook two full seconds for an advisory counter.
        """
        try:
            with self.transaction() as state:
                new_value = state.get(key, 0) + amount
                state[key] = new_value
                return new_value
        except StateUnavailable:
            return 0

    def append_to_list(self, key: str, value: Any) -> bool:
        """Append to a list in state, inside one transaction."""
        try:
            with self.transaction() as state:
                current = state.get(key)
                if not isinstance(current, list):
                    current = []
                current.append(value)
                state[key] = current
            return True
        except StateUnavailable:
            return False

    def claim_topics(self, candidates: Iterable[str], limit: int) -> List[str]:
        """Atomically claim up to ``limit`` not-yet-claimed topics.

        Filters ``candidates`` against ``grounded_topics``, takes at most
        ``limit`` of what survives, records them, and returns exactly what was
        claimed.  Filtering *before* capping matters: capping first would let
        already-claimed topics consume the slots and starve a fresh topic
        forever (issue #202).

        Returns an empty list if nothing is claimable or the lock was busy -- the
        caller must then inject nothing, since injecting text whose topic was not
        recorded would repeat it on every later prompt.
        """
        try:
            with self.transaction() as state:
                claimed = state.get('grounded_topics')
                if not isinstance(claimed, list):
                    claimed = []
                fresh = [t for t in candidates if t not in claimed][:max(0, limit)]
                if not fresh:
                    return []
                state['grounded_topics'] = claimed + fresh
                return fresh
        except StateUnavailable:
            return []

    # -- persistence ----------------------------------------------------------

    def _load_unlocked(self) -> Dict[str, Any]:
        """Read state without touching the lock. Caller must hold it."""
        if not os.path.exists(self.state_file):
            return self._default_state()
        last_exc = None
        for attempt in range(IO_RETRIES):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                # Genuinely corrupt content (not contention) -- start clean.
                return self._default_state()
            except OSError as exc:
                # Sharing violation: a scanner or a racing replace holds it.
                last_exc = exc
                if attempt < IO_RETRIES - 1:
                    time.sleep(IO_RETRY_SEC)
        # Never masquerade an unreadable file as "no state" -- that is how a
        # persisted task_type silently became 'unknown' (issue #202).
        raise StateUnavailable(str(last_exc))

    def _save_unlocked(self, state: Dict[str, Any]) -> None:
        """Atomically publish state. Caller must hold the lock.

        Raises:
            StateUnavailable: if the write could not be published.  Callers
                surface this as "not persisted" instead of swallowing it.
        """
        tmp = f"{self.state_file}.{os.getpid()}.tmp"
        last_exc = None
        for attempt in range(IO_RETRIES):
            try:
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=2)
                os.replace(tmp, self.state_file)
                return
            except OSError as exc:
                last_exc = exc
                if attempt < IO_RETRIES - 1:
                    time.sleep(IO_RETRY_SEC)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise StateUnavailable(str(last_exc))

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

    def cleanup(self) -> bool:
        """Remove the state file when the session ends.

        Takes the lock so a concurrent transaction is not deleted out from under
        itself.  Returns False if the lock was busy -- the caller should leave the
        file for ``cleanup_old_sessions()`` rather than force the delete.

        Read everything you need BEFORE calling this: deleting first and reading
        after is what made ``task_type`` unrecoverable in issue #202.
        """
        try:
            with _file_lock(self.lock_file):
                if os.path.exists(self.state_file):
                    os.unlink(self.state_file)
            # The sidecar lock file is deliberately NOT unlinked. Deleting it
            # splits the lock into two domains: a waiter can still hold and lock
            # the unlinked inode while another process creates a fresh file at the
            # same path and locks that, and both then proceed into the critical
            # section. It is an empty rendezvous file; leaving it costs nothing and
            # cleanup_old_sessions sweeps it with the state file.
            return True
        except StateUnavailable:
            return False
        except OSError:
            return False


def resolve_session_id(input_data: Dict[str, Any]) -> str:
    """Resolve the session id for a hook payload, one way for every hook.

    Precedence: the payload's ``session_id``, then the SessionStart sentinel
    (keeps a session that crosses an hour boundary on one state file, OPT-04
    #177), then a stable per-hour seed.

    Extracted from post_tool_use so Stop stops picking state by modification
    time -- which could consume and delete a *different* concurrent session's
    file (issue #202).
    """
    session_id = input_data.get('session_id')
    if session_id:
        return session_id

    session_id = read_current_session()
    if session_id:
        return session_id

    from .memory_reader import generate_session_id
    return generate_session_id(datetime.now().strftime("%Y-%m-%d-%H"))


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
        if not f.endswith('.json'):
            continue
        filepath = os.path.join(active_dir, f)
        try:
            if os.path.getmtime(filepath) >= cutoff:
                continue
            # Take the file's own lock before deleting. Checking mtime and then
            # unlinking unlocked lets a writer commit fresh state in between, and
            # the sweep then deletes a successfully committed update. Re-check the
            # mtime under the lock for exactly that reason. A busy lock means skip
            # it -- the file is stale, so the next sweep gets it.
            with _file_lock(filepath + '.lock'):
                if os.path.getmtime(filepath) < cutoff:
                    os.unlink(filepath)
                    cleaned += 1
        except (OSError, StateUnavailable):
            continue

    # Sidecars are swept separately and only when provably unused: deleting a
    # lock file that someone might still be waiting on splits it into two
    # domains (a waiter holds the unlinked inode while a new process locks a
    # fresh file at the same path, and both proceed) -- the same hazard cleanup()
    # avoids by never unlinking. Acquisition is bounded by LOCK_TIMEOUT_SEC, so a
    # sidecar untouched for a day has no possible waiter. Enumerated separately
    # because the loop above only walks '.json'.
    lock_cutoff = datetime.now().timestamp() - (24 * 3600)
    for f in os.listdir(active_dir):
        if not f.endswith('.json.lock'):
            continue
        lockpath = os.path.join(active_dir, f)
        try:
            if os.path.exists(lockpath[:-len('.lock')]):
                continue  # its session is still live
            if os.path.getmtime(lockpath) < lock_cutoff:
                os.unlink(lockpath)
        except OSError:
            continue

    return cleaned

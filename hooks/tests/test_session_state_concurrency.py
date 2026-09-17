"""Concurrency contract for SessionState (synthetic fixtures).

Several hooks write one per-session JSON: PostToolUse on every tool call,
PreCompact, UserPromptSubmit, and Stop.  These tests pin the guarantees the
grounding dedupe depends on:

  * cross-process read-modify-write is serialized (no lost updates);
  * a topic can be claimed exactly once, even under a race;
  * a busy lock makes callers fail closed, never hang and never proceed unlocked;
  * a killed holder's lock is released by the kernel (no age heuristic);
  * nested reads inside a transaction do not self-deadlock;
  * an unreadable/unwritable file is never silently reported as "no state".

Every test isolates OBI_ROOT to tmp_path -- reading the developer's real
~/.claude/.obi makes these non-hermetic.
"""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.session_state import (  # noqa: E402
    SessionState,
    StateUnavailable,
    _file_lock,
    resolve_session_id,
)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv('OBI_ROOT', str(tmp_path))
    return SessionState('sess-1')


def _run_workers(tmp_path, body, count, timeout=60, extra_bodies=()):
    """Run ``body`` in ``count`` real subprocesses; return their stdout lines.

    Real processes, not threads: the point is to exercise the OS lock rather than
    the GIL. ``body`` and each of ``extra_bodies`` are top-level source, already
    unindented; the extras run concurrently with the main workers (used to sample
    the file WHILE it is being written).
    """
    header = (
        'import sys\n'
        f'sys.path.insert(0, r"{HOOKS_DIR}")\n'
        'from core.session_state import SessionState, StateUnavailable\n'
        "state = SessionState('sess-1')\n"
    )
    env = dict(os.environ, OBI_ROOT=str(tmp_path), PYTHONIOENCODING='utf-8')

    specs = [(f'worker{i}.py', body) for i in range(count)]
    specs += [(f'extra{i}.py', b) for i, b in enumerate(extra_bodies)]

    procs = []
    for name, src in specs:
        script = tmp_path / name
        script.write_text(header + src, encoding='utf-8')
        procs.append(subprocess.Popen(
            [sys.executable, str(script)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))

    out = []
    for p in procs:
        stdout, stderr = p.communicate(timeout=timeout)
        assert p.returncode == 0, f'worker failed: {stderr}'
        out.extend(line for line in stdout.splitlines() if line.strip())
    return out


class TestCrossProcessSerialization:
    """The lock has to hold across processes, not just threads."""

    def test_concurrent_increments_are_not_lost(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        _run_workers(tmp_path, body=(
            "for _ in range(25):\n"
            "    state.increment('tool_count')\n"
        ), count=4)

        assert SessionState('sess-1').get('tool_count') == 100

    def test_concurrent_writers_preserve_each_others_fields(self, tmp_path, monkeypatch):
        """A writer must not clobber a field it never touched.

        This is the failure Codex identified: PostToolUse loading state, another
        hook persisting grounded_topics, then PostToolUse writing back its stale
        copy and dropping the dedupe record.
        """
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        _run_workers(tmp_path, body=(
            "import os\n"
            "state.update(**{'field_%s' % os.getpid(): 'set'})\n"
            "for _ in range(10):\n"
            "    state.increment('tool_count')\n"
        ), count=4)

        final = SessionState('sess-1').get_all()
        assert final['tool_count'] == 40
        assert len([k for k in final if k.startswith('field_')]) == 4

    def test_state_file_is_always_valid_json(self, tmp_path, monkeypatch):
        """A reader sampling DURING the writes must never see a torn file.

        Reading only after every writer exits would pass even with non-atomic
        writes, so one worker hammers reads while the others write and reports any
        read that failed to parse.
        """
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        writers = (
            "for i in range(15):\n"
            "    state.append_to_list('tools_used', {'tool': 'T%d' % i})\n"
        )
        reader = (
            "import json, os, time\n"
            "bad = 0\n"
            "deadline = time.time() + 3\n"
            "while time.time() < deadline:\n"
            "    try:\n"
            "        raw = open(state.state_file, encoding='utf-8').read()\n"
            "    except OSError:\n"
            "        continue\n"
            "    if not raw.strip():\n"
            "        continue\n"
            "    try:\n"
            "        json.loads(raw)\n"
            "    except json.JSONDecodeError:\n"
            "        bad += 1\n"
            "print('TORN=%d' % bad)\n"
        )
        # Seed the file so the reader has something to read from the start.
        SessionState('sess-1').update(seeded=True)
        lines = _run_workers(tmp_path, body=writers, count=3, extra_bodies=[reader])

        torn = [l for l in lines if l.startswith('TORN=')]
        assert torn, 'reader worker did not report'
        assert torn[0] == 'TORN=0', f'reader saw a partially-written file: {torn[0]}'

        path = tmp_path / '.obi' / 'active-sessions' / 'sess-1.json'
        json.loads(path.read_text(encoding='utf-8'))  # and it parses at rest


class TestClaimTopics:
    """Dedupe must be atomic, filter before capping, and never starve a topic."""

    def test_claims_once_then_returns_empty(self, state):
        assert state.claim_topics(['widgetapi'], limit=3) == ['widgetapi']
        assert state.claim_topics(['widgetapi'], limit=3) == []

    def test_filters_before_capping_so_fresh_topics_are_not_starved(self, state):
        """With A/B/C claimed and limit=3, a match on A/B/C/D must yield D.

        Capping the candidate list before excluding claimed topics would return
        A/B/C, claim nothing, and starve D for the rest of the session.
        """
        state.claim_topics(['a', 'b', 'c'], limit=3)
        assert state.claim_topics(['a', 'b', 'c', 'd'], limit=3) == ['d']

    def test_respects_limit(self, state):
        assert state.claim_topics(['a', 'b', 'c', 'd'], limit=2) == ['a', 'b']
        assert state.get('grounded_topics') == ['a', 'b']

    def test_exactly_one_process_wins_a_contested_topic(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        lines = _run_workers(tmp_path, body=(
            "claimed = state.claim_topics(['widgetapi', 'github', 'storageapi'], limit=3)\n"
            "print(','.join(claimed))\n"
        ), count=4)

        won = [t for line in lines for t in line.split(',') if t]
        assert sorted(won) == ['github', 'storageapi', 'widgetapi'], (
            f'each topic must be claimed exactly once, got {won}'
        )


class TestFailClosed:
    """A busy lock degrades the optional work; it never hangs or runs unlocked."""

    def test_mutations_report_failure_when_lock_is_held(self, state):
        with _file_lock(state.lock_file):
            assert state.update(task_type='widgetapi') is False
            assert state.claim_topics(['widgetapi'], limit=3) == []
            assert state.append_to_list('tools_used', {'tool': 'x'}) is False

    def test_get_all_raises_so_callers_can_distinguish_absent_from_unreadable(self, state):
        with _file_lock(state.lock_file):
            with pytest.raises(StateUnavailable):
                state.get_all()

    def test_get_stays_best_effort(self, state):
        """Existing advisory callers must keep working, so get() returns default."""
        with _file_lock(state.lock_file):
            assert state.get('task_type', 'unknown') == 'unknown'

    def test_acquisition_is_bounded(self, state):
        """A hook must never hang Claude Code waiting on a peer."""
        with _file_lock(state.lock_file):
            started = time.monotonic()
            state.update(task_type='widgetapi')
            assert time.monotonic() - started < 2.0

    def test_killed_holder_releases_the_lock(self, tmp_path, monkeypatch):
        """Kernel-managed release -- no age-based breaking, which could displace
        a live-but-slow holder and create two writers."""
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        script = tmp_path / 'holder.py'
        script.write_text(
            'import sys, time\n'
            f'sys.path.insert(0, r"{HOOKS_DIR}")\n'
            'from core.session_state import SessionState, _file_lock\n'
            "state = SessionState('sess-1')\n"
            'with _file_lock(state.lock_file):\n'
            "    print('held', flush=True)\n"
            '    time.sleep(60)\n',
            encoding='utf-8')

        env = dict(os.environ, OBI_ROOT=str(tmp_path))
        proc = subprocess.Popen([sys.executable, str(script)], env=env,
                                stdout=subprocess.PIPE, text=True)
        try:
            assert proc.stdout.readline().strip() == 'held'
            state = SessionState('sess-1')
            assert state.update(task_type='blocked') is False
        finally:
            proc.kill()
            proc.wait(timeout=30)

        assert SessionState('sess-1').update(task_type='widgetapi') is True


class TestReentrancy:
    """msvcrt/fcntl locks are not reentrant -- verified: a second handle in the
    same process gets PermissionError. Nested reads must reuse the held lock."""

    def test_nested_get_inside_transaction_does_not_deadlock(self, state):
        """A nested read must return promptly AND see the uncommitted mutation.

        The sentinel below is deliberately not a real task type: it is a value
        only this test could have written, so the assertions cannot be satisfied
        by a default or by production behaviour.

        Asserting merely "not None" would pass on the FALLBACK value that a
        blocked read returns, hiding the very deadlock this test exists to catch.
        """
        sentinel = 'nested-read-sentinel'
        started = time.monotonic()
        with state.transaction() as txn:
            txn['task_type'] = sentinel
            # 'unknown' here is the fallback ARGUMENT -- the point is that the
            # read must NOT fall back to it.
            assert state.get('task_type', 'unknown') == sentinel
            assert state.get_all()['task_type'] == sentinel
        assert time.monotonic() - started < 2.0, 'nested read blocked on the lock'
        assert state.get('task_type') == sentinel

    def test_nested_transaction_still_persists(self, state):
        with state.transaction() as txn:
            txn['outer'] = 1
            state.update(inner=2)
        final = state.get_all()
        assert final['inner'] == 2


class TestNeverSilentlyDefault:
    """An unreadable or unpublishable file must not look like "no state" --
    that is how a persisted task_type became 'unknown' (synthetic fixtures)."""

    def test_write_failure_is_reported_not_swallowed(self, state):
        """On Windows an open reader handle makes os.replace fail with WinError 5.

        The write must report failure and leave the previous content intact,
        rather than being swallowed by a bare `except OSError: pass`.
        """
        assert state.update(task_type='widgetapi') is True

        reader = open(state.state_file, 'r', encoding='utf-8')
        try:
            persisted = state.update(task_type='overwritten')
        finally:
            reader.close()

        if persisted:
            # POSIX (or a Windows build that permits the replace): the write
            # went through, which is also correct behaviour.
            assert state.get('task_type') == 'overwritten'
        else:
            # Windows: reported as not-persisted, old value intact, not corrupt.
            assert state.get('task_type') == 'widgetapi'

    def test_corrupt_content_falls_back_but_readable_state_does_not(self, state):
        state.update(task_type='widgetapi')
        Path(state.state_file).write_text('{not json', encoding='utf-8')
        # Genuinely corrupt -> clean defaults (not an exception).
        assert state.get('task_type', 'unknown') == 'unknown'


class TestStopConsumerOrdering:
    """Stop must snapshot state BEFORE deleting it, and delete only its own.

    Both halves of issue #202's second root cause: stop.py read ``task_type``
    four lines after ``cleanup()``, and picked state by modification time.
    """

    def test_snapshot_before_cleanup_preserves_task_type(self, state):
        from core.stop_pipeline import resolve_task_type

        state.update(task_type='widgetapi')
        snapshot = state.get_all()          # what stop.py now does first
        state.cleanup()

        assert resolve_task_type({}, snapshot) == 'widgetapi'

    def test_reading_after_cleanup_is_the_old_bug(self, state):
        """Regression guard: the previous order could only ever say 'unknown'."""
        from core.stop_pipeline import resolve_task_type

        state.update(task_type='widgetapi')
        state.cleanup()                     # the old stop.py order
        assert resolve_task_type({}, state) == 'unknown'

    def test_cleanup_does_not_touch_another_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        mine = SessionState('mine')
        theirs = SessionState('theirs')
        mine.update(task_type='widgetapi')
        theirs.update(task_type='canvasapi')

        mine.cleanup()

        assert not os.path.exists(mine.state_file)
        assert theirs.get('task_type') == 'canvasapi'

    def test_cleanup_reports_failure_when_lock_is_held(self, state):
        """A busy lock must leave the file for cleanup_old_sessions(), not force
        the delete out from under a live transaction."""
        state.update(task_type='widgetapi')
        with _file_lock(state.lock_file):
            assert state.cleanup() is False
        assert os.path.exists(state.state_file)

    def test_stop_does_not_delete_state_it_failed_to_read(self, state):
        """Stop must not clean up after a FAILED snapshot.

        The lock can free between the read attempt and the delete, so the delete
        would succeed where the read did not -- discarding state nobody consumed.
        Mirrors the guard in stop.py (`snapshot is not None`).
        """
        state.update(task_type='widgetapi')

        snapshot = None
        try:
            with _file_lock(state.lock_file):
                snapshot = state.get_all()
        except StateUnavailable:
            snapshot = None
        assert snapshot is None, 'expected the read to fail while the lock was held'

        # Lock is free again here -- cleanup WOULD succeed, which is the trap.
        if snapshot is not None:
            state.cleanup()
        assert os.path.exists(state.state_file), 'state was deleted unread'
        assert SessionState(state.session_id).get('task_type') == 'widgetapi'

    def test_sweeper_rechecks_mtime_under_the_lock(self, tmp_path, monkeypatch):
        """The re-check must actually run, on a file that looked stale.

        Simply committing before calling the sweeper proves nothing: the FIRST
        mtime check would then skip the file and the locked re-check would never
        execute, so the test would pass even with the re-check deleted. The write
        has to land BETWEEN the two checks, which is exactly the race. Injected by
        wrapping _file_lock so a fresh commit happens at acquisition time.
        """
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        import core.session_state as ss

        s = SessionState('sweep-me')
        s.update(task_type='widgetapi')
        old = time.time() - (48 * 3600)
        os.utime(s.state_file, (old, old))  # looks stale to the first check

        original_lock = ss._file_lock
        fired = []

        @contextmanager
        def racing_lock(lock_path, timeout=ss.LOCK_TIMEOUT_SEC):
            with original_lock(lock_path, timeout):
                if lock_path == s.lock_file and not fired:
                    fired.append(True)
                    # Another process commits here. Written directly rather than
                    # via update(), which would deadlock on the held lock.
                    with open(s.state_file, 'w', encoding='utf-8') as f:
                        json.dump({'session_id': 'sweep-me',
                                   'task_type': 'canvasapi'}, f)
                yield

        monkeypatch.setattr(ss, '_file_lock', racing_lock)
        ss.cleanup_old_sessions(max_age_hours=24)

        assert fired, 'the sweeper never took the lock -- re-check not exercised'
        assert os.path.exists(s.state_file), 'sweeper deleted freshly committed state'
        assert SessionState('sweep-me').get('task_type') == 'canvasapi'

    def test_sweeper_still_removes_genuinely_stale_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        from core.session_state import cleanup_old_sessions

        s = SessionState('really-stale')
        s.update(task_type='widgetapi')
        old = time.time() - (48 * 3600)
        os.utime(s.state_file, (old, old))

        assert cleanup_old_sessions(max_age_hours=24) >= 1
        assert not os.path.exists(s.state_file)

    def test_sweeper_keeps_a_recent_sidecar_but_reaps_an_old_orphan(
            self, tmp_path, monkeypatch):
        """Sidecars are only removed once no waiter can possibly hold them.

        Deleting a lock file someone is still waiting on splits it into two
        domains, so the sweep requires BOTH that its session file is gone and
        that the sidecar itself is a day stale -- far beyond the 1s acquisition
        budget, so a waiter cannot exist.
        """
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        from core.session_state import cleanup_old_sessions

        fresh = SessionState('fresh-orphan')
        fresh.update(task_type='widgetapi')
        old = time.time() - (48 * 3600)
        os.utime(fresh.state_file, (old, old))
        cleanup_old_sessions(max_age_hours=24)
        assert not os.path.exists(fresh.state_file)
        assert os.path.exists(fresh.lock_file), (
            'a just-touched sidecar may still have a waiter and must be kept'
        )

        # Age the orphaned sidecar past the safety window; now it is reapable.
        os.utime(fresh.lock_file, (old, old))
        cleanup_old_sessions(max_age_hours=24)
        assert not os.path.exists(fresh.lock_file)

    def test_sweeper_never_reaps_a_live_sessions_sidecar(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        from core.session_state import cleanup_old_sessions

        live = SessionState('live')
        live.update(task_type='widgetapi')
        old = time.time() - (48 * 3600)
        os.utime(live.lock_file, (old, old))  # stale sidecar, but session is live

        cleanup_old_sessions(max_age_hours=24)
        assert os.path.exists(live.state_file)
        assert os.path.exists(live.lock_file)


class TestSessionIdSanitization:
    """A payload-supplied session id must not steer writes out of the directory."""

    @pytest.mark.parametrize('raw', [
        '../../escaped',
        '..\\..\\escaped',
        'C:/Windows/Temp/escaped',
        'sub/dir/id',
    ])
    def test_state_stays_inside_active_sessions(self, tmp_path, monkeypatch, raw):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        s = SessionState(raw)
        s.update(task_type='widgetapi')

        active = os.path.realpath(str(tmp_path / '.obi' / 'active-sessions'))
        for path in (s.state_file, s.lock_file):
            assert os.path.realpath(os.path.dirname(path)) == active, (
                f'{raw!r} placed {path} outside active-sessions/'
            )
        assert os.path.exists(s.state_file)

    def test_distinct_ids_stay_distinct(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        a = SessionState('abc-123')
        b = SessionState('abc_123')
        assert a.state_file != b.state_file

    def test_normal_uuid_is_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        sid = '96960cd9-548d-418c-83da-86268f36a944'
        assert os.path.basename(SessionState(sid).state_file) == f'{sid}.json'


class TestResolveSessionId:
    """One resolution order for every hook, so Stop stops picking state by mtime."""

    def test_prefers_payload_session_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        assert resolve_session_id({'session_id': 'abc'}) == 'abc'

    def test_falls_back_to_sentinel(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        from core.session_state import write_current_session
        write_current_session('sentinel-id')
        assert resolve_session_id({}) == 'sentinel-id'

    def test_falls_back_to_hour_seed(self, tmp_path, monkeypatch):
        monkeypatch.setenv('OBI_ROOT', str(tmp_path))
        resolved = resolve_session_id({})
        assert resolved and isinstance(resolved, str)

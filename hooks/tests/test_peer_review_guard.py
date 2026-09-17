from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path


HOOKS_DIR = Path(__file__).parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from core.detector_registry import DetectorContext  # noqa: E402
from core.detectors.peer_review_ownership_detector import (  # noqa: E402
    PeerReviewOwnershipDetector,
)
from core.peer_review_guard import active_peer_reviews  # noqa: E402
from core.session_state import StateUnavailable  # noqa: E402
import core.peer_review_guard as peer_review_guard  # noqa: E402


def _status(repo: Path, run_id: str, *, terminal: bool, state: str) -> None:
    path = repo / '.obi' / 'review' / 'runs' / run_id / 'status.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        'run_id': run_id,
        'provider': 'claude',
        'terminal': terminal,
        'transport_status': state,
        'updated_at': '2026-08-06T00:00:00+00:00',
    }), encoding='utf-8')


def test_guard_only_reports_nonterminal_runs(tmp_path: Path) -> None:
    _status(tmp_path, 'active-1', terminal=False, state='running')
    _status(tmp_path, 'done-1', terminal=True, state='completed')
    assert [item['run_id'] for item in active_peer_reviews(str(tmp_path))] == ['active-1']


def test_detector_keeps_run_id_visible_to_orchestrator(tmp_path: Path) -> None:
    _status(tmp_path, 'active-2', terminal=False, state='preparing')
    context = DetectorContext('stop', str(tmp_path), time.time())
    message = PeerReviewOwnershipDetector().run(context)
    assert 'active-2' in message
    assert 'status/wait/result/cancel' in message


def test_guard_surfaces_contended_status_instead_of_dropping_obligation(
    tmp_path: Path, monkeypatch
) -> None:
    _status(tmp_path, 'busy-1', terminal=False, state='running')

    @contextmanager
    def unavailable_lock(*_args, **_kwargs):
        raise StateUnavailable('busy')
        yield

    monkeypatch.setattr(peer_review_guard, 'bounded_file_lock', unavailable_lock)
    items = active_peer_reviews(str(tmp_path))
    assert items == [{
        'run_id': 'busy-1',
        'provider': 'unknown',
        'transport_status': 'status_unavailable',
        'heartbeat_at': None,
    }]


def test_guard_surfaces_corrupt_status_instead_of_dropping_obligation(
    tmp_path: Path,
) -> None:
    _status(tmp_path, 'corrupt-1', terminal=False, state='running')
    status_path = (
        tmp_path / '.obi' / 'review' / 'runs' / 'corrupt-1' / 'status.json'
    )
    status_path.write_text('{not-json', encoding='utf-8')
    items = active_peer_reviews(str(tmp_path))
    assert items[0]['run_id'] == 'corrupt-1'
    assert items[0]['transport_status'] == 'status_unavailable'


def test_guard_stops_at_deadline_and_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    for index in range(40):
        _status(tmp_path, f'busy-{index:02d}', terminal=False, state='running')

    @contextmanager
    def slow_unavailable_lock(*_args, **_kwargs):
        time.sleep(0.02)
        raise StateUnavailable('busy')
        yield

    monkeypatch.setattr(
        peer_review_guard,
        'bounded_file_lock',
        slow_unavailable_lock,
    )
    started = time.monotonic()
    items = active_peer_reviews(
        str(tmp_path),
        max_runs=40,
        deadline_monotonic=started + 0.08,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert any(item['run_id'] == 'peer-review-scan' for item in items)


def test_guard_caps_directory_enumeration_and_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    for index in range(3):
        _status(tmp_path, f'run-{index}', terminal=True, state='completed')
    monkeypatch.setattr(peer_review_guard, '_MAX_SCAN_ENTRIES', 2)
    items = active_peer_reviews(str(tmp_path))
    assert any(item['run_id'] == 'peer-review-scan' for item in items)

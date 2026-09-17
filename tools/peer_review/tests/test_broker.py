from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.peer_review import broker
from tools.peer_review.io_utils import atomic_write_json
from tools.peer_review.runner import StatusStore, artifact_paths
from tools.peer_review.validation import unavailable_result

from .test_packet import make_repo, request as request_value


def _request(repo: Path) -> Path:
    path = repo / 'request.json'
    path.write_text(json.dumps(request_value()), encoding='utf-8')
    return path


def test_start_returns_durable_receipt_before_detached_work(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)

    class FakeProcess:
        pid = os.getpid()

    monkeypatch.setattr(broker.subprocess, 'Popen', lambda *_a, **_k: FakeProcess())
    output = broker.start_review(
        repo_root=repo,
        request_file=_request(repo),
        provider='codex',
        timeout_sec=600,
        run_id='broker-start-test',
    )
    assert output['accepted'] is True
    assert output['run_id'] == 'broker-start-test'
    status = json.loads(Path(output['status_file']).read_text(encoding='utf-8'))
    assert status['run_mode'] == 'broker'
    assert status['worker_pid'] == os.getpid()
    assert (repo / '.obi' / 'review' / 'runs' / 'broker-start-test' / '.launch-ready').is_file()


def test_start_auto_uses_explicit_primary_platform(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)

    class FakeProcess:
        pid = os.getpid()

    monkeypatch.setattr(broker.subprocess, 'Popen', lambda *_a, **_k: FakeProcess())
    output = broker.start_review(
        repo_root=repo,
        request_file=_request(repo),
        provider='auto',
        platform='codex',
        timeout_sec=600,
        run_id='broker-platform-test',
    )

    assert output['accepted'] is True
    assert output['provider'] == 'claude'


def test_owner_loss_becomes_terminal_instead_of_silent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-owner-lost')
    store = StatusStore(
        Path(artifacts['status']),
        run_id='broker-owner-lost',
        provider='claude',
        timeout_sec=600,
        artifacts=artifacts,
        run_mode='broker',
        worker_pid=999_999_999,
        worker_started_at_epoch=1.0,
    )
    store.value['started_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    store.write()
    status = broker.recover_status(repo, 'broker-owner-lost')
    assert status['terminal'] is True
    assert status['transport_status'] == 'owner_lost'
    assert Path(artifacts['result']).is_file()
    assert Path(artifacts['summary']).is_file()


def test_result_marks_terminal_obligation_consumed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-result')
    store = StatusStore(
        Path(artifacts['status']),
        run_id='broker-result',
        provider='codex',
        timeout_sec=600,
        artifacts=artifacts,
        run_mode='broker',
    )
    result = unavailable_result('codex', 'test terminal')
    atomic_write_json(Path(artifacts['result']), result)
    store.terminal('unavailable', validation_status='not_run')
    output = broker.result_review(repo_root=repo, run_id='broker-result')
    assert output['ready'] is True
    assert output['result']['validation_status'] == 'not_run'
    status = json.loads(Path(artifacts['status']).read_text(encoding='utf-8'))
    assert status['result_consumed_at'] is not None


def test_wait_is_bounded_and_reports_pending(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-wait')
    StatusStore(
        Path(artifacts['status']),
        run_id='broker-wait',
        provider='codex',
        timeout_sec=600,
        artifacts=artifacts,
        run_mode='broker',
        worker_pid=os.getpid(),
        worker_started_at_epoch=broker._process_start_epoch(os.getpid()),
    )
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(
        broker,
        'time',
        SimpleNamespace(
            monotonic=lambda: next(clock, 2.0),
            sleep=lambda _seconds: None,
        ),
    )
    output = broker.wait_review(repo_root=repo, run_id='broker-wait', wait_sec=1)
    assert output['wait_status'] == 'pending'


def test_cancel_marker_does_not_race_worker_status_writer(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-cancel-marker')
    StatusStore(
        Path(artifacts['status']),
        run_id='broker-cancel-marker',
        provider='codex',
        timeout_sec=600,
        artifacts=artifacts,
        run_mode='broker',
        worker_pid=os.getpid(),
        worker_started_at_epoch=broker._process_start_epoch(os.getpid()),
    )
    _, before = broker._load_status(repo, 'broker-cancel-marker')
    broker._request_cancellation(before, 'test')
    _, durable = broker._load_status(repo, 'broker-cancel-marker')
    assert durable['transport_status'] == 'queued'
    visible = broker.recover_status(repo, 'broker-cancel-marker')
    assert visible['transport_status'] == 'cancel_requested'
    assert visible['terminal'] is False


def test_detached_start_wait_result_end_to_end(tmp_path: Path, monkeypatch) -> None:
    repo = make_repo(tmp_path)
    git = shutil.which('git')
    assert git is not None
    isolated_path = str(Path(git).parent)
    if os.name == 'nt':
        isolated_path += os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
    monkeypatch.setenv('PATH', isolated_path)
    receipt = broker.start_review(
        repo_root=repo,
        request_file=_request(repo),
        provider='codex',
        timeout_sec=30,
        run_id='broker-e2e',
    )
    assert receipt['accepted'] is True
    waited = broker.wait_review(
        repo_root=repo,
        run_id='broker-e2e',
        wait_sec=20,
        until='terminal',
    )
    assert waited['wait_status'] == 'terminal'
    assert waited['transport_status'] == 'unavailable'
    consumed = broker.result_review(repo_root=repo, run_id='broker-e2e')
    assert consumed['ready'] is True
    assert consumed['result']['validation_status'] == 'not_run'


def _validated_consumer_result(provider: str = 'codex') -> dict:
    return {
        'schema_version': 1,
        'provider': provider,
        'peer_verdict': 'fail',
        'validation_status': 'valid',
        'scope_complete': True,
        'findings': [{
            'id': 'PR-001',
            'severity': 'medium',
            'category': 'regression-risk',
            'summary': 'preserve me',
            'rationale': 'validated before worker exit',
            'recommendation': 'keep the result',
            'evidence': [{'path': 'a.py', 'line_start': 1, 'line_end': 1}],
        }],
        'rejected_findings': [],
        'limitations': [],
    }


def test_owner_loss_preserves_result_committed_before_terminal_status(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-precommitted-result')
    store = StatusStore(
        Path(artifacts['status']),
        run_id='broker-precommitted-result',
        provider='codex',
        timeout_sec=60,
        artifacts=artifacts,
        run_mode='broker',
    )
    expected = _validated_consumer_result()
    atomic_write_json(Path(artifacts['result']), expected, compact=True)
    recovered = broker._finalize_abandoned(
        Path(artifacts['status']),
        dict(store.value),
        'owner_lost',
        'worker exited before status commit',
    )
    assert recovered['transport_status'] == 'completed'
    assert recovered['validation_status'] == 'valid'
    assert json.loads(Path(artifacts['result']).read_text(encoding='utf-8')) == expected


def test_abandonment_rechecks_terminal_status_before_writing(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-stale-status')
    store = StatusStore(
        Path(artifacts['status']),
        run_id='broker-stale-status',
        provider='codex',
        timeout_sec=60,
        artifacts=artifacts,
        run_mode='broker',
    )
    stale = dict(store.value)
    expected = _validated_consumer_result()
    atomic_write_json(Path(artifacts['result']), expected, compact=True)
    store.terminal('completed', validation_status='valid', review_verdict='fail')
    recovered = broker._finalize_abandoned(
        Path(artifacts['status']), stale, 'owner_lost', 'stale poller'
    )
    assert recovered['transport_status'] == 'completed'
    assert json.loads(Path(artifacts['result']).read_text(encoding='utf-8')) == expected


def test_forced_fallback_cancel_never_claims_tree_verification(
    tmp_path: Path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, 'broker-unverified-fallback')
    StatusStore(
        Path(artifacts['status']),
        run_id='broker-unverified-fallback',
        provider='codex',
        timeout_sec=60,
        artifacts=artifacts,
        run_mode='broker',
        worker_pid=os.getpid(),
        worker_started_at_epoch=broker._process_start_epoch(os.getpid()),
    ).update(ownership_mode='process_group_fallback', pid=4242)
    monkeypatch.setattr(broker, '_terminate_worker', lambda *_args: (True, False))
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(
        broker,
        'time',
        SimpleNamespace(
            monotonic=lambda: next(clock, 2.0),
            sleep=lambda _seconds: None,
        ),
    )
    output = broker.cancel_review(
        repo_root=repo,
        run_id='broker-unverified-fallback',
        grace_sec=1,
    )
    assert output['transport_status'] == 'cancelled'
    status = json.loads(Path(artifacts['status']).read_text(encoding='utf-8'))
    assert status['killed'] is True
    assert status['kill_verified'] is False
    assert 'not verified' in status['detail']


@pytest.mark.skipif(os.name != 'nt', reason='Windows taskkill tree acceptance test')
def test_forced_fallback_termination_kills_the_worker_process_tree(
    tmp_path: Path,
) -> None:
    marker = tmp_path / 'provider.pid'
    provider = tmp_path / 'provider.py'
    provider.write_text(
        f"import os,time,pathlib; marker=pathlib.Path(r'{marker}'); pending=marker.with_suffix('.tmp'); "
        "pending.write_text(str(os.getpid())); pending.replace(marker); "
        "time.sleep(60)\n",
        encoding='utf-8',
    )
    worker = tmp_path / 'worker.py'
    worker.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,r'{provider}'])\n"
        "time.sleep(60)\n",
        encoding='utf-8',
    )
    process = subprocess.Popen(
        [sys.executable, str(worker)],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), 'fallback provider child never started'
    provider_pid = int(marker.read_text(encoding='utf-8'))
    provider_started = broker._process_start_epoch(provider_pid)
    worker_started = broker._process_start_epoch(process.pid)
    worker_gone, tree_verified = broker._terminate_worker(
        process.pid,
        worker_started,
        'process_group_fallback',
    )
    process.wait(timeout=10)
    assert worker_gone is True
    assert tree_verified is True
    assert broker._identity_matches(provider_pid, provider_started) is False

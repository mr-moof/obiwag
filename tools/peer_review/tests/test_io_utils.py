from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time

from tools.peer_review.io_utils import (
    _fsync_directory,
    atomic_write_json,
    atomic_write_text,
    read_json_file,
)


def test_same_process_concurrent_atomic_writes_use_distinct_temporaries(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "status.json"
    workers = 8
    state_lock = threading.Lock()
    active_replaces = 0
    max_active_replaces = 0
    original_replace = os.replace

    def guarded_replace(source: Path, destination: Path) -> None:
        nonlocal active_replaces, max_active_replaces
        with state_lock:
            active_replaces += 1
            max_active_replaces = max(max_active_replaces, active_replaces)
        try:
            time.sleep(0.01)
            original_replace(source, destination)
        finally:
            with state_lock:
                active_replaces -= 1

    monkeypatch.setattr(os, "replace", guarded_replace)
    values = [f'{{"writer":{index}}}\n' for index in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda value: atomic_write_text(target, value), values))

    assert target.read_text(encoding="utf-8") in values
    assert max_active_replaces == 1
    assert list(tmp_path.glob(f".{target.name}.tmp.*")) == []


def test_locked_reader_waits_for_atomic_status_publication(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "status.json"
    atomic_write_json(target, {"generation": 1})
    replace_entered = threading.Event()
    release_replace = threading.Event()
    original_replace = os.replace

    def paused_replace(source: Path, destination: Path) -> None:
        replace_entered.set()
        assert release_replace.wait(timeout=2)
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", paused_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(atomic_write_json, target, {"generation": 2})
        assert replace_entered.wait(timeout=2)
        reader = pool.submit(
            read_json_file,
            target,
            max_bytes=1024,
            use_lock=True,
        )
        time.sleep(0.05)
        assert reader.done() is False
        release_replace.set()
        writer.result(timeout=2)
        assert reader.result(timeout=2) == {"generation": 2}


def test_atomic_writer_retries_transient_windows_replace_denial(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "status.json"
    original_replace = os.replace
    calls = 0

    def flaky_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(5, "destination is temporarily open")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", flaky_replace)
    atomic_write_text(target, '{"ready":true}\n')
    assert calls == 3
    assert read_json_file(target, max_bytes=1024, use_lock=True) == {"ready": True}


def test_atomic_writers_emit_utf8_with_lf_bytes_on_windows_too(tmp_path: Path) -> None:
    text_path = tmp_path / "summary.md"
    json_path = tmp_path / "status.json"
    atomic_write_text(text_path, "first\nsecond\n")
    atomic_write_json(json_path, {"line": "first\nsecond"})
    assert text_path.read_bytes() == b"first\nsecond\n"
    assert b"\r\n" not in json_path.read_bytes()


def test_atomic_writer_flushes_reserved_descriptor_without_path_reopen(
    tmp_path: Path, monkeypatch
) -> None:
    fsync_calls = 0
    original_fsync = os.fsync

    def recording_fsync(descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        original_fsync(descriptor)

    def forbidden_write_text(*args, **kwargs):
        raise AssertionError("reserved temporary was reopened through Path.write_text")

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(Path, "write_text", forbidden_write_text)
    target = tmp_path / "result.json"
    atomic_write_text(target, "{}\n")
    assert target.read_bytes() == b"{}\n"
    assert fsync_calls >= 1


def test_best_effort_directory_fsync_never_fails_after_replace(
    tmp_path: Path, monkeypatch
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: 123)
    monkeypatch.setattr(
        os,
        "fsync",
        lambda descriptor: (_ for _ in ()).throw(OSError("unsupported")),
    )
    monkeypatch.setattr(os, "close", closed.append)
    _fsync_directory(tmp_path)
    assert closed == [123]


def test_json_reader_rejects_oversized_state_before_parsing(tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    target.write_text('{"padding":"' + ('x' * 200) + '"}', encoding="utf-8")

    try:
        read_json_file(target, max_bytes=64)
    except ValueError as exc:
        assert "exceeds 64 bytes" in str(exc)
    else:
        raise AssertionError("oversized JSON was accepted")

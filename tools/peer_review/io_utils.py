"""Small, dependency-free durability and hashing helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

try:  # pragma: no cover - platform-specific import
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

try:  # pragma: no cover - platform-specific import
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


_ATOMIC_WRITE_LOCK = threading.Lock()
_LOCK_TIMEOUT_SEC = 2.0
_LOCK_POLL_SEC = 0.005
_REPLACE_RETRIES = 10
_REPLACE_RETRY_SEC = 0.02


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def artifact_lock(path: Path, *, timeout_sec: float = _LOCK_TIMEOUT_SEC):
    """Hold the cross-process sidecar lock for one mutable artifact.

    Windows does not permit ``os.replace`` while another process has the
    destination open without delete sharing.  Atomic replacement alone is
    therefore insufficient for status files that are polled by the broker,
    health check, and Stop hook.  A persistent ``<name>.lock`` rendezvous file
    serializes those cooperating readers with writers; the kernel releases the
    lock if a process dies.
    """
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    lock_path = path.with_name(f"{path.name}.lock")
    deadline = time.monotonic() + timeout_sec
    handle = lock_path.open("a+b")
    acquired = False
    try:
        handle.seek(0)
        while True:
            try:
                if msvcrt is not None:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:  # pragma: no cover - every supported platform has one
                    raise OSError("OS file locking is unavailable")
                acquired = True
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out locking peer artifact: {path}") from exc
                time.sleep(_LOCK_POLL_SEC)
        yield
    finally:
        if acquired:
            try:
                if msvcrt is not None:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            handle.close()
        except OSError:
            pass


def read_json_file(path: Path, *, max_bytes: int, use_lock: bool = False) -> Any:
    """Parse one UTF-8 JSON file without allowing corrupt state to grow unbounded."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    lock = artifact_lock(path) if use_lock else nullcontext()
    with lock:
        with path.open("rb") as stream:
            payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"JSON file exceeds {max_bytes} bytes: {path}")
    return json.loads(payload.decode("utf-8-sig"))


def _fsync_directory(path: Path) -> None:
    """Best-effort metadata flush where directory descriptors are supported."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Publish a temporary despite brief non-cooperating Windows readers."""
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(source, destination)
            return
        except OSError:
            if attempt + 1 >= _REPLACE_RETRIES:
                raise
            time.sleep(_REPLACE_RETRY_SEC)


def _atomic_write_bytes(path: Path, value: bytes, *, use_lock: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ATOMIC_WRITE_LOCK:
        lock = artifact_lock(path) if use_lock else nullcontext()
        with lock:
            descriptor, name = tempfile.mkstemp(
                dir=str(path.parent),
                prefix=f".{path.name}.tmp.",
            )
            temporary = Path(name)
            descriptor_open = True
            try:
                stream = os.fdopen(descriptor, "wb")
                descriptor_open = False
                with stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                _replace_with_retry(temporary, path)
                _fsync_directory(path.parent)
            finally:
                if descriptor_open:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)


def json_file_bytes(value: Any, *, compact: bool = False) -> bytes:
    options = {"ensure_ascii": False}
    if compact:
        options["separators"] = (",", ":")
    else:
        options["indent"] = 2
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def atomic_write_json(
    path: Path,
    value: Any,
    *,
    use_lock: bool = True,
    compact: bool = False,
) -> None:
    payload = json_file_bytes(value, compact=compact)
    _atomic_write_bytes(path, payload, use_lock=use_lock)


def atomic_write_text(path: Path, value: str, *, use_lock: bool = True) -> None:
    _atomic_write_bytes(path, value.encode("utf-8"), use_lock=use_lock)

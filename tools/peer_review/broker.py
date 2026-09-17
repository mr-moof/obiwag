"""Durable detached peer-review lifecycle operations."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import (
    artifact_lock,
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    read_json_file,
    sha256_bytes,
)
from .packet import load_request
from .runner import (
    MAX_BROKER_TIMEOUT_SEC,
    RUN_ID_PATTERN,
    StatusStore,
    artifact_paths,
    bounded_summary,
    make_run_id,
    resolve_provider,
    run_review,
    utc_now,
)
from .validation import unavailable_result

HEARTBEAT_STALE_SEC = 30.0
LAUNCH_GRACE_SEC = 10.0
MAX_WAIT_SEC = 240
STATUS_MAX_BYTES = 1024 * 1024
CANCEL_MAX_BYTES = 64 * 1024
RESULT_MAX_BYTES = 256 * 1024


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_seconds(value: Any) -> float:
    parsed = _parse_time(value)
    if parsed is None:
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def _run_dir(repo_root: Path, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters or is too long")
    root = repo_root.resolve()
    path = (root / ".obi" / "review" / "runs" / run_id).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("run_id escapes repository") from exc
    return path


def _load_status(repo_root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    path = _run_dir(repo_root, run_id) / "status.json"
    try:
        value = read_json_file(path, max_bytes=STATUS_MAX_BYTES, use_lock=True)
    except FileNotFoundError as exc:
        raise ValueError(f"unknown peer-review run: {run_id}") from exc
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"peer-review status is unreadable: {exc}") from exc
    if not isinstance(value, dict) or value.get("run_id") != run_id:
        raise ValueError("peer-review status does not match the requested run")
    return path, value


def _process_start_epoch(pid: int) -> float | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            # Creation time remains queryable for an exited process while any
            # handle is still open. Require an unsignaled process handle before
            # treating that creation time as a live identity.
            handle = ctypes.windll.kernel32.OpenProcess(
                0x00100000 | 0x1000,  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
                False,
                pid,
            )
            if not handle:
                return None
            try:
                if ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != 0x102:
                    return None
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return ticks / 10_000_000.0 - 11_644_473_600.0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return None
    try:
        return Path(f"/proc/{pid}").stat().st_ctime
    except OSError:
        return None


def _identity_matches(pid: Any, expected_start: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    actual = _process_start_epoch(pid)
    if actual is None:
        return False
    if not isinstance(expected_start, (int, float)):
        return True
    return abs(actual - float(expected_start)) <= 2.0


def _terminate_worker(
    pid: int,
    expected_start: Any,
    ownership_mode: Any = None,
) -> tuple[bool, bool]:
    """Terminate the matched worker and report worker/tree verification separately."""
    if not _identity_matches(pid, expected_start):
        return False, False
    tree_command_succeeded = False
    if os.name == "nt":
        taskkill = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "taskkill.exe"
        )
        try:
            result = subprocess.run(
                [str(taskkill), "/T", "/F", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
            tree_command_succeeded = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            tree_command_succeeded = False
        if not tree_command_succeeded and _identity_matches(pid, expected_start):
            try:
                import ctypes

                handle = ctypes.windll.kernel32.OpenProcess(
                    0x0001 | 0x00100000, False, pid
                )
                if handle:
                    try:
                        ctypes.windll.kernel32.TerminateProcess(handle, 125)
                    finally:
                        ctypes.windll.kernel32.CloseHandle(handle)
            except (AttributeError, OSError, ValueError):
                pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True, False
        except OSError:
            return False, False
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if not _identity_matches(pid, expected_start):
            return True, bool(
                tree_command_succeeded or ownership_mode == "windows_job"
            )
        time.sleep(0.1)
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True, False
        except OSError:
            return False, False
    worker_gone = not _identity_matches(pid, expected_start)
    tree_verified = worker_gone and (
        tree_command_succeeded or ownership_mode == "windows_job"
    )
    return worker_gone, tree_verified


def _committed_consumer_result(path: Path, provider: str) -> dict[str, Any] | None:
    try:
        value = read_json_file(path, max_bytes=RESULT_MAX_BYTES, use_lock=True)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != 1 or value.get("provider") != provider:
        return None
    if value.get("validation_status") not in {"valid", "partial", "invalid", "not_run"}:
        return None
    if not isinstance(value.get("findings"), list):
        return None
    if not isinstance(value.get("rejected_findings"), list):
        return None
    if not isinstance(value.get("limitations"), list):
        return None
    return value


def _finalize_abandoned(
    status_path: Path,
    status: dict[str, Any],
    state: str,
    detail: str,
    *,
    killed: bool = False,
    kill_verified: bool = False,
) -> dict[str, Any]:
    with artifact_lock(status_path):
        current = read_json_file(
            status_path,
            max_bytes=STATUS_MAX_BYTES,
            use_lock=False,
        )
        if not isinstance(current, dict) or current.get("run_id") != status.get("run_id"):
            raise ValueError("peer-review status changed identity during recovery")
        if current.get("terminal") is True:
            return current

        status = current
        provider = str(status.get("provider") or "unknown")
        result_path = Path(status["artifacts"]["result"])
        summary_path = Path(status["artifacts"]["summary"])
        result = _committed_consumer_result(result_path, provider)
        effective_state = state
        effective_detail = detail
        if result is None:
            result = unavailable_result(provider, detail)
            atomic_write_json(result_path, result, compact=True)
        else:
            effective_detail = (
                f"{detail}; preserved a consumer result committed before terminal status"
            )
            if result.get("validation_status") != "not_run":
                effective_state = "completed"
                effective_detail = (
                    "recovered a validated provider result committed before the worker "
                    "could publish terminal status"
                )

        now = utc_now()
        status.update(
            transport_status=effective_state,
            terminal=True,
            terminal_at=now,
            updated_at=now,
            heartbeat_at=now,
            review_verdict=result.get("peer_verdict"),
            validation_status=result.get("validation_status"),
            activity_kind="terminal",
            killed=killed,
            kill_verified=kill_verified,
            result_bytes=result_path.stat().st_size,
            detail=effective_detail,
        )
        atomic_write_text(summary_path, bounded_summary(status, result))
        # The compare-and-finalize section already owns status.json.lock.
        atomic_write_json(status_path, status, use_lock=False)
        return status


def _cancel_marker(status: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(status["artifacts"]["cancel_request"])
    try:
        value = read_json_file(path, max_bytes=CANCEL_MAX_BYTES, use_lock=True)
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None


def _request_cancellation(status: dict[str, Any], reason: str) -> dict[str, Any]:
    existing = _cancel_marker(status)
    if existing is not None:
        return existing
    value = {
        "run_id": status["run_id"],
        "requested_at": utc_now(),
        "reason": reason,
    }
    atomic_write_json(Path(status["artifacts"]["cancel_request"]), value)
    return value


def _cancel_overlay(status: dict[str, Any], marker: dict[str, Any], detail: str) -> dict[str, Any]:
    visible = dict(status)
    visible.update(
        transport_status="cancel_requested",
        cancel_requested_at=marker.get("requested_at"),
        detail=detail,
    )
    return visible


def recover_status(repo_root: Path, run_id: str) -> dict[str, Any]:
    """Turn owner loss or a dead heartbeat into an explicit terminal state."""
    status_path, status = _load_status(repo_root, run_id)
    if status.get("terminal") is True or status.get("run_mode") != "broker":
        return status
    worker_pid = status.get("worker_pid")
    worker_start = status.get("worker_started_at_epoch")
    alive = _identity_matches(worker_pid, worker_start)
    if not alive and _age_seconds(status.get("started_at")) >= LAUNCH_GRACE_SEC:
        return _finalize_abandoned(
            status_path,
            status,
            "owner_lost",
            "broker worker exited before committing a terminal result",
        )
    if alive and _age_seconds(status.get("heartbeat_at")) >= HEARTBEAT_STALE_SEC:
        marker = _request_cancellation(status, "broker heartbeat stalled")
        if _age_seconds(marker.get("requested_at")) < 5:
            return _cancel_overlay(
                status,
                marker,
                "broker heartbeat stalled; cancellation requested before forced recovery",
            )
        worker_gone, tree_verified = _terminate_worker(
            int(worker_pid), worker_start, status.get("ownership_mode")
        )
        if worker_gone:
            provider_pid = status.get("pid")
            detail = (
                "broker heartbeat stalled; the owned worker tree was terminated"
                if tree_verified
                else "broker heartbeat stalled; worker termination succeeded but provider "
                f"tree termination was not verified (provider PID {provider_pid})"
            )
            return _finalize_abandoned(
                status_path,
                status,
                "stalled_killed",
                detail,
                killed=True,
                kill_verified=tree_verified,
            )
        if not _identity_matches(worker_pid, worker_start):
            return _finalize_abandoned(
                status_path,
                status,
                "owner_lost",
                "broker heartbeat stalled and its worker exited without a terminal result",
            )
        return _cancel_overlay(
            status,
            marker,
            "broker heartbeat stalled; worker termination could not be verified",
        )
    marker = _cancel_marker(status)
    if marker is not None:
        return _cancel_overlay(status, marker, "cancellation requested; waiting for owned worker")
    return status


def _compact(status: dict[str, Any], operation: str) -> dict[str, Any]:
    return {
        "operation": operation,
        "run_id": status["run_id"],
        "provider": status["provider"],
        "transport_status": status["transport_status"],
        "terminal": status["terminal"],
        "heartbeat_at": status.get("heartbeat_at"),
        "last_activity_at": status.get("last_activity_at"),
        "activity_kind": status.get("activity_kind"),
        "cpu_time_sec": status.get("cpu_time_sec", 0.0),
        "detail": status.get("detail"),
        "status_file": status["artifacts"]["status"],
        "result_file": status["artifacts"]["result"],
        "summary_file": status["artifacts"]["summary"],
    }


def start_review(
    *,
    repo_root: Path,
    request_file: Path,
    provider: str,
    timeout_sec: int = 1800,
    run_id: str | None = None,
    platform: str | None = None,
    expected_request_sha256: str | None = None,
    expected_payload_scope_sha256: str | None = None,
) -> dict[str, Any]:
    if timeout_sec < 1 or timeout_sec > MAX_BROKER_TIMEOUT_SEC:
        raise ValueError(f"timeout_sec must be between 1 and {MAX_BROKER_TIMEOUT_SEC}")
    repo_root = repo_root.resolve()
    selected = resolve_provider(provider, platform)
    request = load_request(request_file.resolve())
    request_sha256 = sha256_bytes(canonical_json_bytes(request))
    if expected_request_sha256 and request_sha256 != expected_request_sha256:
        raise ValueError("request changed after authorization")
    expected_request_sha256 = request_sha256
    run_id = run_id or make_run_id()
    run_dir, artifacts = artifact_paths(repo_root, run_id)
    atomic_write_json(Path(artifacts["request"]), request)
    status = StatusStore(
        Path(artifacts["status"]),
        run_id=run_id,
        provider=selected,
        timeout_sec=timeout_sec,
        artifacts=artifacts,
        run_mode="broker",
    )
    gate = run_dir / ".launch-ready"
    launcher = Path(__file__).resolve().parents[1] / "peer-review.py"
    command = [
        sys.executable,
        str(launcher),
        "_worker",
        "--provider",
        selected,
        "--repo-root",
        str(repo_root),
        "--request-file",
        artifacts["request"],
        "--timeout-sec",
        str(timeout_sec),
        "--run-id",
        run_id,
        "--launch-gate",
        str(gate),
        "--expected-request-sha256",
        expected_request_sha256,
        "--expected-payload-scope-sha256",
        expected_payload_scope_sha256 or "",
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(repo_root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": dict(os.environ),
    }
    try:
        if os.name == "nt":
            base_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            kwargs["creationflags"] = base_flags | breakaway
            try:
                process = subprocess.Popen(command, **kwargs)
            except OSError:
                kwargs["creationflags"] = base_flags
                process = subprocess.Popen(command, **kwargs)
        else:
            kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)
    except OSError as exc:
        terminal = _finalize_abandoned(
            Path(artifacts["status"]),
            status.value,
            "launch_error",
            f"unable to launch broker worker: {exc}",
        )
        receipt = _compact(terminal, "start")
        receipt.update(accepted=True, timeout_sec=timeout_sec, worker_pid=None)
        return receipt
    worker_started = _process_start_epoch(process.pid) or time.time()
    status.update(
        worker_pid=process.pid,
        worker_started_at_epoch=worker_started,
        heartbeat_at=utc_now(),
        detail="accepted; broker worker launching",
    )
    atomic_write_text(gate, "ready\n")
    receipt = _compact(status.value, "start")
    receipt.update(accepted=True, timeout_sec=timeout_sec, worker_pid=process.pid)
    return receipt


def worker_review(
    *,
    repo_root: Path,
    request_file: Path,
    provider: str,
    timeout_sec: int,
    run_id: str,
    launch_gate: Path,
    expected_request_sha256: str,
    expected_payload_scope_sha256: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + LAUNCH_GRACE_SEC
    while not launch_gate.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not launch_gate.is_file():
        status_path, status = _load_status(repo_root, run_id)
        finalized = _finalize_abandoned(
            status_path,
            status,
            "launch_error",
            "broker parent did not release the worker launch gate",
        )
        return _compact(finalized, "_worker")
    launch_gate.unlink(missing_ok=True)
    return run_review(
        repo_root=repo_root,
        request_file=request_file,
        provider=provider,
        timeout_sec=timeout_sec,
        run_id=run_id,
        run_mode="broker",
        allow_existing_run=True,
        worker_pid=os.getpid(),
        worker_started_at_epoch=_process_start_epoch(os.getpid()) or time.time(),
        expected_request_sha256=expected_request_sha256,
        expected_payload_scope_sha256=expected_payload_scope_sha256 or None,
    )


def status_review(*, repo_root: Path, run_id: str) -> dict[str, Any]:
    return _compact(recover_status(repo_root.resolve(), run_id), "status")


def wait_review(
    *,
    repo_root: Path,
    run_id: str,
    wait_sec: int = 120,
    until: str = "terminal",
) -> dict[str, Any]:
    if wait_sec < 1 or wait_sec > MAX_WAIT_SEC:
        raise ValueError(f"wait_sec must be between 1 and {MAX_WAIT_SEC}")
    if until not in {"terminal", "activity"}:
        raise ValueError("until must be terminal or activity")
    initial = recover_status(repo_root.resolve(), run_id)
    boundary = (initial.get("last_activity_at"), initial.get("transport_status"))
    deadline = time.monotonic() + wait_sec
    current = initial
    while time.monotonic() < deadline:
        if current.get("terminal") is True:
            break
        if until == "activity" and (
            current.get("last_activity_at"), current.get("transport_status")
        ) != boundary:
            break
        time.sleep(0.5)
        current = recover_status(repo_root.resolve(), run_id)
    result = _compact(current, "wait")
    result["wait_status"] = (
        "terminal"
        if current.get("terminal") is True
        else "activity"
        if until == "activity" and (
            current.get("last_activity_at"), current.get("transport_status")
        ) != boundary
        else "pending"
    )
    return result


def result_review(*, repo_root: Path, run_id: str) -> dict[str, Any]:
    status_path, _ = _load_status(repo_root.resolve(), run_id)
    status = recover_status(repo_root.resolve(), run_id)
    if status.get("terminal") is not True:
        result = _compact(status, "result")
        result["ready"] = False
        return result
    result_path = Path(status["artifacts"]["result"])
    try:
        peer_result = read_json_file(
            result_path, max_bytes=RESULT_MAX_BYTES, use_lock=True
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"terminal peer result is unreadable: {exc}") from exc
    status["result_consumed_at"] = utc_now()
    status["updated_at"] = utc_now()
    atomic_write_json(status_path, status)
    output = _compact(status, "result")
    output.update(ready=True, result=peer_result)
    return output


def cancel_review(
    *,
    repo_root: Path,
    run_id: str,
    grace_sec: int = 10,
) -> dict[str, Any]:
    if grace_sec < 1 or grace_sec > 30:
        raise ValueError("grace_sec must be between 1 and 30")
    repo_root = repo_root.resolve()
    _, status = _load_status(repo_root, run_id)
    status = recover_status(repo_root, run_id)
    if status.get("terminal") is True:
        return _compact(status, "cancel")
    marker = _request_cancellation(status, "primary orchestrator requested cancellation")
    deadline = time.monotonic() + grace_sec
    while time.monotonic() < deadline:
        time.sleep(0.25)
        current = recover_status(repo_root, run_id)
        if current.get("terminal") is True:
            return _compact(current, "cancel")
    worker_pid = status.get("worker_pid")
    worker_gone, tree_verified = (
        _terminate_worker(
            int(worker_pid),
            status.get("worker_started_at_epoch"),
            status.get("ownership_mode"),
        )
        if isinstance(worker_pid, int)
        else (False, False)
    )
    current_path, current = _load_status(repo_root, run_id)
    if worker_gone:
        provider_pid = current.get("pid")
        detail = (
            "cancellation grace expired; owned worker tree termination was verified"
            if tree_verified
            else "cancellation grace expired; worker termination succeeded but provider "
            f"tree termination was not verified (provider PID {provider_pid})"
        )
        current = _finalize_abandoned(
            current_path,
            current,
            "cancelled",
            detail,
            killed=True,
            kill_verified=tree_verified,
        )
        return _compact(current, "cancel")
    current = recover_status(repo_root, run_id)
    if current.get("terminal") is True:
        return _compact(current, "cancel")
    return _compact(
        _cancel_overlay(
            current,
            marker,
            "cancellation remains pending; worker termination could not be verified",
        ),
        "cancel",
    )

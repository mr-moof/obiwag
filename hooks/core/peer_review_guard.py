"""Cheap detection of accepted peer reviews that still need an outcome."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .session_state import StateUnavailable, bounded_file_lock


_TERMINAL_STATES = {
    "completed",
    "unavailable",
    "timed_out",
    "idle_killed",
    "output_limit",
    "error",
    "launch_error",
    "cancelled",
    "owner_lost",
    "stalled_killed",
}
_STATUS_MAX_BYTES = 1024 * 1024
_MAX_SCAN_ENTRIES = 256
_MAX_SCAN_SECONDS = 0.75
_MAX_LOCK_WAIT_SECONDS = 0.05


def _unavailable_status(status_path: Path) -> dict[str, Any]:
    return {
        "run_id": status_path.parent.name,
        "provider": "unknown",
        "transport_status": "status_unavailable",
        "heartbeat_at": None,
    }


def _scan_unavailable() -> dict[str, Any]:
    return {
        "run_id": "peer-review-scan",
        "provider": "unknown",
        "transport_status": "status_unavailable",
        "heartbeat_at": None,
    }


def active_peer_reviews(
    repo_root: str,
    max_runs: int = 100,
    deadline_monotonic: float | None = None,
) -> list[dict[str, Any]]:
    """Return bounded metadata for non-terminal durable peer-review runs.

    This function is used by the Stop hook, so it deliberately performs no
    process inspection, recovery, or writes.  The broker owns those actions;
    the hook only prevents an accepted review obligation from disappearing.
    """
    runs_root = Path(repo_root) / ".obi" / "review" / "runs"
    if not runs_root.exists():
        return []
    started = time.monotonic()
    scan_deadline = started + _MAX_SCAN_SECONDS
    if deadline_monotonic is not None:
        scan_deadline = min(scan_deadline, deadline_monotonic)
    incomplete = False
    try:
        candidates_with_age = []
        scanned = 0
        with os.scandir(runs_root) as entries:
            for entry in entries:
                if scanned >= _MAX_SCAN_ENTRIES or time.monotonic() >= scan_deadline:
                    incomplete = True
                    break
                scanned += 1
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    incomplete = True
                    continue
                item = Path(entry.path) / "status.json"
                try:
                    if not item.is_file():
                        continue
                except OSError:
                    incomplete = True
                    continue
                try:
                    modified = item.stat().st_mtime
                except OSError:
                    modified = float("inf")
                candidates_with_age.append((modified, item))
        candidate_limit = max(1, min(int(max_runs), 100))
        if len(candidates_with_age) > candidate_limit:
            incomplete = True
        candidates = [
            item
            for _, item in sorted(
                candidates_with_age,
                key=lambda candidate: candidate[0],
                reverse=True,
            )
        ][:candidate_limit]
    except OSError:
        return [_scan_unavailable()]

    active: list[dict[str, Any]] = [_scan_unavailable()] if incomplete else []
    for status_path in candidates:
        remaining = scan_deadline - time.monotonic()
        if remaining <= 0:
            if not any(item["run_id"] == "peer-review-scan" for item in active):
                active.append(_scan_unavailable())
            break
        try:
            lock_path = status_path.with_name(f"{status_path.name}.lock")
            lock_timeout = max(
                0.001,
                min(_MAX_LOCK_WAIT_SECONDS, remaining),
            )
            with bounded_file_lock(str(lock_path), timeout=lock_timeout):
                with status_path.open("rb") as stream:
                    payload = stream.read(_STATUS_MAX_BYTES + 1)
            if len(payload) > _STATUS_MAX_BYTES:
                raise ValueError("peer-review status exceeds the Stop-hook limit")
            value = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("peer-review status is not an object")
        except (StateUnavailable, OSError, UnicodeError, ValueError, json.JSONDecodeError):
            active.append(_unavailable_status(status_path))
            continue
        state = str(value.get("transport_status") or "unknown")
        terminal = value.get("terminal") is True or state in _TERMINAL_STATES
        # `start` creates an explicit obligation. A terminal broker run remains
        # outstanding until the primary calls `result`; explicit cancellation
        # is the only terminal state that needs no result consumption.
        if terminal:
            if value.get("run_mode") != "broker":
                continue
            if state == "cancelled" or value.get("result_consumed_at"):
                continue
        active.append(
            {
                "run_id": str(value.get("run_id") or status_path.parent.name),
                "provider": str(value.get("provider") or "unknown"),
                "transport_status": f"{state}/unconsumed" if terminal else state,
                "heartbeat_at": value.get("heartbeat_at") or value.get("updated_at"),
            }
        )
    return active

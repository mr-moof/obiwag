"""Peer-review execution and its durable artifact/status contract."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .adapters import (
    ADAPTER_VERSION,
    AdapterSpec,
    ProviderResultError,
    ProviderUnavailable,
    build_prompt,
    claude_adapter,
    codex_adapter,
    materialize_result,
)
from .io_utils import (
    atomic_write_json,
    atomic_write_text,
    canonical_json_bytes,
    json_file_bytes,
    read_json_file,
    sha256_bytes,
)
from .packet import Capsule, build_capsule, load_request, remove_capsule
from .process_control import ProcessOutcome, run_managed
from .validation import (
    capsule_changes,
    capsule_inventory,
    invalid_result,
    unavailable_result,
    unusable_execution_reason,
    validate_result,
)

MAX_TIMEOUT_SEC = 240
MAX_BROKER_TIMEOUT_SEC = 3600
SUMMARY_MAX_BYTES = 64 * 1024
SUMMARY_MAX_LINES = 200
RESULT_MAX_BYTES = 256 * 1024
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

AdapterBuilder = Callable[[Capsule, Path, Path], AdapterSpec]


class ReviewDeadlineExceeded(RuntimeError):
    """The accepted run spent its complete deadline before provider launch."""


def _join_detail(primary: str | None, secondary: str | None) -> str | None:
    parts = [part.strip() for part in (primary, secondary) if part and part.strip()]
    return "; ".join(parts)[:2000] if parts else None


def _bounded_text(value: Any, max_chars: int = 2048) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15] + "...[truncated]"


def bounded_result(
    result: dict[str, Any], max_bytes: int = RESULT_MAX_BYTES
) -> dict[str, Any]:
    """Return a consumer result guaranteed to fit its documented read cap.

    Validation can expand compact provider JSON by resolving shared citation
    IDs into per-finding evidence and retaining rejected bodies.  Preserve as
    many validated findings as possible (high severity first), drop diagnostic
    rejection bodies before accepted findings, and make every omission explicit.
    """
    if max_bytes < 1024:
        raise ValueError("result max_bytes is too small for the v1 contract")
    if len(json_file_bytes(result, compact=True)) <= max_bytes:
        return result

    original_findings = list(result.get("findings") or [])
    original_rejected = list(result.get("rejected_findings") or [])
    original_limitations = list(result.get("limitations") or [])
    candidate = dict(result)
    candidate["scope_complete"] = False
    candidate["findings"] = []
    candidate["rejected_findings"] = []
    candidate["limitations"] = ["Result storage cap applied."]

    severity_order = {"high": 0, "medium": 1, "low": 2}
    prioritized = sorted(
        enumerate(original_findings[:128]),
        key=lambda item: (
            severity_order.get(str(item[1].get("severity")), 3)
            if isinstance(item[1], dict)
            else 3,
            item[0],
        ),
    )
    for _, finding in prioritized:
        candidate["findings"].append(finding)
        if len(json_file_bytes(candidate, compact=True)) > max_bytes:
            candidate["findings"].pop()

    omitted_findings = len(original_findings) - len(candidate["findings"])
    status = str(result.get("validation_status") or "invalid")
    if status != "not_run" and (omitted_findings or status == "valid"):
        candidate["validation_status"] = "partial"

    def storage_note() -> str:
        return (
            "Result exceeded the durable storage cap; retained "
            f"{len(candidate['findings'])}/{len(original_findings)} validated findings, "
            f"omitted {len(original_rejected)} rejected finding bodies, and bounded "
            "diagnostic limitations. Rerun with narrower scope if omitted material is needed."
        )

    candidate["limitations"] = [storage_note()]
    kept_limitations = 0
    for limitation in original_limitations[:64]:
        candidate["limitations"].append(_bounded_text(limitation))
        if len(json_file_bytes(candidate, compact=True)) > max_bytes:
            candidate["limitations"].pop()
            break
        kept_limitations += 1

    while len(json_file_bytes(candidate, compact=True)) > max_bytes:
        if len(candidate["limitations"]) > 1:
            candidate["limitations"].pop()
        elif candidate["findings"]:
            candidate["findings"].pop()
            omitted_findings += 1
            candidate["limitations"][0] = storage_note()
        else:  # Fixed v1 fields plus the storage note are well below 1 KiB.
            raise ValueError("bounded peer result cannot fit the storage contract")

    if kept_limitations < len(original_limitations):
        candidate["scope_complete"] = False
    return candidate


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def resolve_provider(provider: str, platform: str | None = None) -> str:
    provider = provider.lower()
    if provider in {"codex", "claude"}:
        return provider
    if provider != "auto":
        raise ValueError("provider must be auto, codex, or claude")
    primary = (platform or os.environ.get("OBI_PLATFORM", "")).lower()
    if primary in {"claude", "claude-code"}:
        return "codex"
    if primary == "codex":
        return "claude"
    raise ValueError(
        "Provider auto requires --platform codex|claude or "
        "OBI_PLATFORM=codex|claude"
    )


def artifact_paths(
    repo_root: Path,
    run_id: str,
    *,
    allow_existing: bool = False,
) -> tuple[Path, dict[str, str]]:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id contains unsafe characters or is too long")
    run_dir = repo_root / ".obi" / "review" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=allow_existing)
    names = {
        "request": "request.json",
        "manifest": "scope-manifest.json",
        "status": "status.json",
        "events": "events.jsonl",
        "stderr": "stderr.log",
        "raw_result": "result.raw.json",
        "result": "result.json",
        "summary": "summary.md",
        "cancel_request": "cancel.request.json",
    }
    return run_dir, {key: str((run_dir / name).resolve()) for key, name in names.items()}


class StatusStore:
    """Thread-safe durable status with heartbeat and distinct activity clocks."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        provider: str,
        timeout_sec: int,
        artifacts: dict[str, str],
        run_mode: str = "foreground",
        resume: bool = False,
        worker_pid: int | None = None,
        worker_started_at_epoch: float | None = None,
    ) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._observability_failure_count = 0
        self._observability_detail: str | None = None
        existing: dict[str, Any] | None = None
        if resume and path.is_file():
            try:
                loaded = read_json_file(
                    path, max_bytes=1024 * 1024, use_lock=True
                )
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                existing = None
        if existing is not None:
            if existing.get("run_id") != run_id or existing.get("provider") != provider:
                raise ValueError("pre-created peer-review status does not match this worker")
            self.value = existing
            self.value.update(
                schema_version=2,
                run_mode=run_mode,
                worker_pid=worker_pid or existing.get("worker_pid"),
                worker_started_at_epoch=(
                    worker_started_at_epoch
                    if worker_started_at_epoch is not None
                    else existing.get("worker_started_at_epoch")
                ),
                heartbeat_at=utc_now(),
                transport_status="preparing",
                terminal=False,
                detail=None,
            )
            self.value.setdefault("cancel_requested_at", None)
            self.value.setdefault("result_consumed_at", None)
            self.value.setdefault("last_activity_at", None)
            self.value.setdefault("activity_kind", "launch")
            self.value.setdefault("cpu_time_sec", 0.0)
            self.value.setdefault("last_activity_age_sec", 0.0)
            self.value["artifacts"] = artifacts
        else:
            started = datetime.now(timezone.utc)
            self.value: dict[str, Any] = {
                "schema_version": 2,
                "run_id": run_id,
                "run_mode": run_mode,
                "provider": provider,
                "adapter_version": ADAPTER_VERSION,
                "cli_version": None,
                "request_sha256": None,
                "capsule_sha256": None,
                "transport_status": "queued" if run_mode == "broker" else "launching",
                "terminal": False,
                "review_verdict": None,
                "validation_status": None,
                "worker_pid": worker_pid,
                "worker_started_at_epoch": worker_started_at_epoch,
                "pid": None,
                "ownership_mode": None,
                "started_at": started.isoformat(),
                "deadline_at": (started + timedelta(seconds=timeout_sec)).isoformat(),
                "updated_at": started.isoformat(),
                "heartbeat_at": started.isoformat(),
                "last_activity_at": started.isoformat(),
                "last_event_at": None,
                "terminal_at": None,
                "cancel_requested_at": None,
                "result_consumed_at": None,
                "exit_code": None,
                "killed": False,
                "kill_verified": False,
                "activity_kind": "launch",
                "cpu_time_sec": 0.0,
                "last_activity_age_sec": 0.0,
                "events_bytes": 0,
                "stderr_bytes": 0,
                "result_bytes": 0,
                "detail": None,
                "artifacts": artifacts,
            }
        self.write()

    def write(self) -> None:
        with self._lock:
            self.value["updated_at"] = utc_now()
            atomic_write_json(self.path, self.value)

    def update(self, **values: Any) -> None:
        with self._lock:
            self.value.update(values)
            self.write()

    @property
    def observability_detail(self) -> str | None:
        return self._observability_detail

    def _resilient_write(self, phase: str) -> bool:
        try:
            self.write()
            return True
        except OSError as exc:
            self._observability_failure_count += 1
            self._observability_detail = (
                "status publication degraded "
                f"({self._observability_failure_count} transient failure(s)); "
                f"latest {phase}: {type(exc).__name__}: {exc}"
            )[:1000]
            # Keep the failure in memory. The next successful heartbeat,
            # progress update, or terminal commit makes it durable.
            self.value["detail"] = self._observability_detail
            return False

    def start_heartbeat(self, interval_sec: float = 2.0) -> None:
        if self._heartbeat_thread is not None:
            return

        def pulse() -> None:
            while not self._heartbeat_stop.wait(interval_sec):
                with self._lock:
                    if self.value.get("terminal"):
                        return
                    self.value["heartbeat_at"] = utc_now()
                    self._resilient_write("heartbeat")

        self._heartbeat_thread = threading.Thread(
            target=pulse,
            name=f"peer-review-heartbeat-{self.value['run_id']}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)

    def progress(
        self,
        pid: int,
        ownership: str,
        events: int,
        stderr: int,
        result: int,
        _: float,
        activity_kind: str,
        cpu_time_sec: float,
    ) -> None:
        now = utc_now()
        values: dict[str, Any] = {
            "transport_status": "running",
            "pid": pid,
            "ownership_mode": ownership,
            "events_bytes": events,
            "stderr_bytes": stderr,
            "result_bytes": result,
            "heartbeat_at": now,
            "activity_kind": activity_kind,
            "cpu_time_sec": round(cpu_time_sec, 3),
        }
        if activity_kind in {"output", "diagnostic", "cpu"}:
            values["last_activity_at"] = now
        if activity_kind == "output":
            values["last_event_at"] = now
        with self._lock:
            self.value.update(values)
            self._resilient_write("progress")

    def terminal(self, status: str, *, persist: bool = True, **values: Any) -> None:
        self.stop_heartbeat()
        with self._lock:
            self.value.update(values)
            self.value.update(
                transport_status=status,
                terminal=True,
                terminal_at=utc_now(),
                heartbeat_at=utc_now(),
            )
            if persist:
                self.write()


def bounded_summary(status: dict[str, Any], result: dict[str, Any]) -> str:
    lines = [
        f"# Peer review {status['run_id']}",
        "",
        f"- Provider: {status['provider']}",
        f"- Transport: {status['transport_status']}",
        f"- Peer verdict: {result.get('peer_verdict') or 'none'}",
        f"- Validation: {result.get('validation_status')}",
        f"- Accepted findings: {len(result.get('findings', []))}",
        f"- Rejected findings: {len(result.get('rejected_findings', []))}",
        "",
    ]
    for finding in result.get("findings", []):
        lines.extend(
            [
                f"## {finding['id']} [{finding['severity']}] {finding['summary']}",
                "",
                *(f"- `{e['path']}:{e['line_start']}-{e['line_end']}`" for e in finding["evidence"]),
                "",
            ]
        )
    if result.get("limitations"):
        lines.extend(["## Limitations", ""])
        lines.extend(f"- {item}" for item in result["limitations"])
    text = "\n".join(lines[:SUMMARY_MAX_LINES]).rstrip() + "\n"
    encoded = text.encode("utf-8")
    if len(encoded) > SUMMARY_MAX_BYTES:
        encoded = encoded[: SUMMARY_MAX_BYTES - 32]
        text = encoded.decode("utf-8", errors="ignore") + "\n[summary truncated]\n"
    return text


_TRANSCRIPT_TAIL_BYTES = 256 * 1024


def _transcript_tail(*paths: Path) -> str:
    """Read the tail of the provider event/stderr logs for refusal markers.

    Bounded: these logs can reach tens of MB and only the refusal text matters.
    """
    chunks: list[str] = []
    for path in paths:
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - _TRANSCRIPT_TAIL_BYTES))
                chunks.append(handle.read().decode("utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


_EVENTS_SCAN_MAX_BYTES = 32 * 1024 * 1024


def _count_successful_commands(events_path: Path, max_bytes: int = _EVENTS_SCAN_MAX_BYTES) -> int:
    """Count `command_execution` items that completed with exit code 0.

    Structural evidence that the peer actually ran tools. Read line by line
    (the stream is JSON Lines) and stop at `max_bytes`; a malformed line is
    skipped, never fatal, because this only feeds the usability classifier.
    """
    count = 0
    consumed = 0
    try:
        with events_path.open("rb") as handle:
            for raw in handle:
                consumed += len(raw)
                if consumed > max_bytes:
                    break
                try:
                    event = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "item.completed":
                    continue
                item = event.get("item")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "command_execution"
                    and item.get("status") == "completed"
                    and item.get("exit_code") == 0
                ):
                    count += 1
    except OSError:
        return 0
    return count


def run_review(
    *,
    repo_root: Path,
    request_file: Path,
    provider: str,
    timeout_sec: int = MAX_TIMEOUT_SEC,
    run_id: str | None = None,
    runtime_root: Path | None = None,
    platform: str | None = None,
    adapter_builder: AdapterBuilder | None = None,
    run_mode: str = "foreground",
    allow_existing_run: bool = False,
    worker_pid: int | None = None,
    worker_started_at_epoch: float | None = None,
    expected_request_sha256: str | None = None,
    expected_payload_scope_sha256: str | None = None,
) -> dict[str, Any]:
    maximum = MAX_BROKER_TIMEOUT_SEC if run_mode == "broker" else MAX_TIMEOUT_SEC
    if timeout_sec < 1 or timeout_sec > maximum:
        raise ValueError(f"timeout_sec must be between 1 and {maximum}")
    if run_mode not in {"foreground", "broker"}:
        raise ValueError("run_mode must be foreground or broker")
    repo_root = repo_root.resolve()
    selected = resolve_provider(provider, platform)
    run_id = run_id or make_run_id()
    request = load_request(request_file.resolve())
    request_sha256 = sha256_bytes(canonical_json_bytes(request))
    if expected_request_sha256 and request_sha256 != expected_request_sha256:
        raise ValueError("request changed after authorization")
    _, artifacts = artifact_paths(repo_root, run_id, allow_existing=allow_existing_run)
    status = StatusStore(
        Path(artifacts["status"]),
        run_id=run_id,
        provider=selected,
        timeout_sec=timeout_sec,
        artifacts=artifacts,
        run_mode=run_mode,
        resume=allow_existing_run,
        worker_pid=worker_pid or os.getpid(),
        worker_started_at_epoch=worker_started_at_epoch,
    )
    status.start_heartbeat()
    capsule: Capsule | None = None
    inventory_before: dict[str, str] | None = None
    result = unavailable_result(selected, "peer review did not reach provider execution")
    transport_status = "launch_error"
    terminal_values: dict[str, Any] = {}
    try:
        atomic_write_json(Path(artifacts["request"]), request)
        status.update(transport_status="preparing", activity_kind="capsule")
        capsule = build_capsule(
            repo_root,
            request,
            run_id,
            runtime_root=runtime_root,
            provider=selected,
            expected_payload_scope_sha256=expected_payload_scope_sha256,
        )
        shutil.copy2(capsule.manifest_path, artifacts["manifest"])
        status.update(
            request_sha256=capsule.request_hash,
            capsule_sha256=capsule.capsule_hash,
            last_activity_at=utc_now(),
        )
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "peer-review-result.schema.json"
        raw_path = Path(artifacts["raw_result"])
        if adapter_builder is not None:
            spec = adapter_builder(capsule, schema_path, raw_path)
        elif selected == "codex":
            spec = codex_adapter(capsule, schema_path=schema_path, raw_result_path=raw_path)
        else:
            spec = claude_adapter(capsule, schema_path=schema_path, raw_result_path=raw_path)
        if spec.provider != selected:
            raise ValueError(f"adapter provider mismatch: selected {selected}, got {spec.provider}")
        status.update(cli_version=spec.cli_version, transport_status="running")
        prompt_path = capsule.root / "peer-prompt.txt"
        prompt_path.write_text(build_prompt(capsule, selected), encoding="utf-8")
        deadline = datetime.fromisoformat(
            str(status.value["deadline_at"]).replace("Z", "+00:00")
        )
        remaining_timeout = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining_timeout <= 0:
            raise ReviewDeadlineExceeded(
                "peer-review preparation exhausted the accepted hard deadline"
            )
        # Integrity baseline lives in this process, not in the capsule the peer can
        # write to: every file (manifest and support files included) is hashed
        # before launch and compared after exit (peer finding PR-001).
        inventory_before = capsule_inventory(capsule.root)
        outcome: ProcessOutcome = run_managed(
            spec.command,
            cwd=capsule.root,
            stdin_path=prompt_path,
            events_path=Path(artifacts["events"]),
            stderr_path=Path(artifacts["stderr"]),
            result_path=raw_path,
            timeout_sec=remaining_timeout,
            env=spec.environment,
            on_progress=status.progress,
            first_output_timeout_sec=min(120, remaining_timeout),
            idle_timeout_sec=min(300, remaining_timeout),
            events_limit=(32 if run_mode == "broker" else 4) * 1024 * 1024,
            stderr_limit=(1024 if run_mode == "broker" else 256) * 1024,
            cancel_requested=lambda: Path(artifacts["cancel_request"]).is_file(),
        )
        # Compare the capsule exactly once, on EVERY path reached after the baseline
        # (peer finding COD-002): a peer that writes and then times out or overflows
        # must still be recorded as capsule_modified.
        capsule_modified = capsule_changes(inventory_before, capsule_inventory(capsule.root))
        transport_status = outcome.status
        terminal_detail = outcome.detail
        materialization_error: str | None = None
        if outcome.status == "completed":
            try:
                materialize_result(spec, Path(artifacts["events"]), raw_path)
            except ProviderResultError as exc:
                materialization_error = str(exc)
                atomic_write_text(raw_path, "{}\n")
            result_bytes = raw_path.stat().st_size if raw_path.exists() else 0
        else:
            result_bytes = outcome.result_bytes

        if outcome.status == "completed" and result_bytes <= RESULT_MAX_BYTES:
            result = validate_result(raw_path, capsule.manifest_path, capsule.root, selected)
            if materialization_error:
                result["limitations"].insert(0, materialization_error)
                terminal_detail = materialization_error
            if capsule_modified:
                # The peer wrote inside the capsule (a snapshot, the manifest, or a new
                # file), so its evidence base no longer matches what was authorized.
                # Nothing it claims can be trusted.
                shown = ", ".join(capsule_modified[:5])
                terminal_detail = f"capsule_modified: {shown}"
                result = invalid_result(selected, terminal_detail)
            else:
                # Refusal markers are read from STDERR only; the events stream
                # carries command OUTPUT, which legitimately contains those strings
                # whenever the peer reads this harness's own source. A single
                # successful command execution proves the peer reviewed something.
                unusable = unusable_execution_reason(
                    result,
                    _transcript_tail(Path(artifacts["stderr"])),
                    successful_commands=_count_successful_commands(
                        Path(artifacts["events"])
                    ),
                )
                if unusable:
                    # Transport completed, but the peer reviewed nothing. Never let
                    # that reach the orchestrator as a clean pass.
                    transport_status = "unavailable"
                    terminal_detail = unusable
                    result = unavailable_result(selected, unusable)
        elif outcome.status == "completed":
            transport_status = "output_limit"
            terminal_detail = "provider result exceeded the hard byte limit"
            result = unavailable_result(selected, terminal_detail)
        else:
            result = unavailable_result(
                selected,
                outcome.detail or f"provider transport ended as {outcome.status}",
            )
        if capsule_modified and result.get("validation_status") != "invalid":
            # Non-completed transports (timeout, output limit, idle kill) still carry
            # the integrity verdict: the transport status stays truthful, the result
            # becomes invalid, and the detail names what changed.
            shown = ", ".join(capsule_modified[:5])
            terminal_detail = f"capsule_modified: {shown}" + (
                f" (after {outcome.status})" if outcome.status != "completed" else ""
            )
            result = invalid_result(selected, terminal_detail)
        terminal_values = {
            "review_verdict": result.get("peer_verdict"),
            "validation_status": result.get("validation_status"),
            "pid": outcome.pid,
            "ownership_mode": outcome.ownership_mode,
            "exit_code": outcome.exit_code,
            "killed": outcome.killed,
            "kill_verified": outcome.kill_verified,
            "activity_kind": "terminal",
            "cpu_time_sec": outcome.cpu_time_sec,
            "last_activity_age_sec": outcome.last_activity_age_sec,
            "events_bytes": outcome.events_bytes,
            "stderr_bytes": outcome.stderr_bytes,
            "result_bytes": result_bytes,
            "detail": terminal_detail,
        }
        if Path(artifacts["cancel_request"]).is_file():
            try:
                cancel_value = read_json_file(
                    Path(artifacts["cancel_request"]),
                    max_bytes=64 * 1024,
                    use_lock=True,
                )
                terminal_values["cancel_requested_at"] = cancel_value.get("requested_at")
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError, AttributeError):
                terminal_values["cancel_requested_at"] = utc_now()
    except Exception as exc:
        if isinstance(exc, ProviderUnavailable):
            transport_status = "unavailable"
        elif isinstance(exc, ReviewDeadlineExceeded):
            transport_status = "timed_out"
        else:
            transport_status = "launch_error"
        result = unavailable_result(selected, str(exc))
        detail = str(exc)
        if capsule is not None and inventory_before is not None:
            # Even an exceptional exit must not lose the integrity verdict (COD-002).
            try:
                changed = capsule_changes(inventory_before, capsule_inventory(capsule.root))
            except OSError:
                changed = []
            if changed:
                detail = f"capsule_modified: {', '.join(changed[:5])} (after {detail})"
                result = invalid_result(selected, detail)
        terminal_values = {
            "review_verdict": None,
            "validation_status": result["validation_status"],
            "activity_kind": "terminal",
            "detail": detail,
        }
    finally:
        status.stop_heartbeat()
        result = bounded_result(result)
        atomic_write_json(Path(artifacts["result"]), result, compact=True)
        terminal_values["review_verdict"] = result.get("peer_verdict")
        terminal_values["validation_status"] = result.get("validation_status")
        terminal_values["result_bytes"] = Path(artifacts["result"]).stat().st_size
        terminal_values["detail"] = _join_detail(
            terminal_values.get("detail"), status.observability_detail
        )
        status.terminal(transport_status, persist=False, **terminal_values)
        atomic_write_text(Path(artifacts["summary"]), bounded_summary(status.value, result))
        # terminal=true is committed only after both consumer artifacts exist.
        status.write()
        if capsule is not None:
            remove_capsule(capsule.root)
    return {
        "run_id": run_id,
        "run_mode": run_mode,
        "provider": selected,
        "transport_status": status.value["transport_status"],
        "peer_verdict": result.get("peer_verdict"),
        "validation_status": result.get("validation_status"),
        "status_file": artifacts["status"],
        "result_file": artifacts["result"],
        "summary_file": artifacts["summary"],
    }

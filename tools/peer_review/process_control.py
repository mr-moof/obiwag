"""Bounded provider execution with Windows kill-on-parent containment."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class ProcessOutcome:
    status: str
    exit_code: int | None
    pid: int | None
    ownership_mode: str
    killed: bool
    kill_verified: bool
    elapsed_sec: float
    events_bytes: int
    stderr_bytes: int
    result_bytes: int
    detail: str | None = None
    cpu_time_sec: float = 0.0
    last_activity_age_sec: float = 0.0


class _WindowsJob:
    """A Job Object whose close kills every assigned descendant."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class BasicAccounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        limits = ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            kernel32.CloseHandle(handle)
            return
        self.handle = handle
        self._kernel32 = kernel32
        self._accounting_type = BasicAccounting

    def assign(self, process: subprocess.Popen[bytes]) -> bool:
        if self.handle is None:
            return False
        import ctypes
        from ctypes import wintypes

        process_handle = wintypes.HANDLE(int(getattr(process, "_handle")))
        if self._kernel32.AssignProcessToJobObject(self.handle, process_handle):
            return True
        self.close()
        return False

    def terminate(self) -> bool:
        return bool(self.handle and self._kernel32.TerminateJobObject(self.handle, 124))

    def cpu_time_sec(self) -> float | None:
        """Return aggregate user+kernel CPU for every process in the job."""
        if not self.handle:
            return None
        import ctypes
        from ctypes import wintypes

        accounting = self._accounting_type()
        returned = wintypes.DWORD()
        ok = self._kernel32.QueryInformationJobObject(
            self.handle,
            1,  # JobObjectBasicAccountingInformation
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            ctypes.byref(returned),
        )
        if not ok:
            return None
        return (accounting.TotalUserTime + accounting.TotalKernelTime) / 10_000_000.0

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _kill_tree(process: subprocess.Popen[bytes], job: _WindowsJob) -> bool:
    if process.poll() is not None:
        return True
    if os.name == "nt":
        job_terminated = job.terminate()
        taskkill_succeeded = False
        if job_terminated:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            try:
                result = subprocess.run(
                    [str(taskkill), "/T", "/F", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
                taskkill_succeeded = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                taskkill_succeeded = False
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return False
    root_gone = process.poll() is not None
    if os.name == "nt":
        return root_gone and (job_terminated or taskkill_succeeded)
    return root_gone


def _cpu_time_sec(process: subprocess.Popen[bytes], job: _WindowsJob) -> float | None:
    aggregate = job.cpu_time_sec()
    if aggregate is not None:
        return aggregate
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            ok = ctypes.windll.kernel32.GetProcessTimes(
                wintypes.HANDLE(int(getattr(process, "_handle"))),
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return None
            kernel_ticks = (kernel.dwHighDateTime << 32) | kernel.dwLowDateTime
            user_ticks = (user.dwHighDateTime << 32) | user.dwLowDateTime
            return (kernel_ticks + user_ticks) / 10_000_000.0
        except (AttributeError, OSError, ValueError):
            return None
    try:
        fields = Path(f"/proc/{process.pid}/stat").read_text(encoding="ascii").split()
        ticks = os.sysconf("SC_CLK_TCK")
        return (int(fields[13]) + int(fields[14])) / float(ticks)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def run_managed(
    command: Sequence[str],
    *,
    cwd: Path,
    stdin_path: Path,
    events_path: Path,
    stderr_path: Path,
    result_path: Path,
    timeout_sec: float,
    env: Mapping[str, str],
    events_limit: int = 4 * 1024 * 1024,
    stderr_limit: int = 256 * 1024,
    result_limit: int = 256 * 1024,
    first_output_timeout_sec: float = 180,
    idle_timeout_sec: float = 300,
    heartbeat_interval_sec: float = 2.0,
    on_progress: Callable[[int, str, int, int, int, float, str, float], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> ProcessOutcome:
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    job = _WindowsJob()
    ownership = "windows_job" if job.handle else "process_group_fallback"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            stdin_path.open("rb") as stdin_stream,
            events_path.open("wb") as events_stream,
            stderr_path.open("wb") as stderr_stream,
        ):
            kwargs: dict[str, object] = {
                "cwd": str(cwd),
                "stdin": stdin_stream,
                "stdout": events_stream,
                "stderr": stderr_stream,
                "env": dict(env),
            }
            if os.name == "nt":
                kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                )
            else:
                kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(list(command), **kwargs)  # type: ignore[arg-type]
            except OSError as exc:
                job.close()
                return ProcessOutcome(
                    "launch_error", None, None, ownership, False, False,
                    round(time.monotonic() - started, 3), 0, 0, 0, str(exc),
                )
            if os.name == "nt" and not job.assign(process):
                ownership = "process_group_fallback"

            last_sizes = (0, 0, 0)
            last_activity = started
            last_callback = 0.0
            last_cpu = _cpu_time_sec(process, job) or 0.0
            saw_review_output = False
            progress_failure_count = 0
            last_progress_error: str | None = None
            terminal_status: str | None = None
            detail: str | None = None
            while process.poll() is None:
                time.sleep(0.25)
                observed_at = time.monotonic()
                elapsed = observed_at - started
                sizes = (_size(events_path), _size(stderr_path), _size(result_path))
                activity_kind = "heartbeat"
                if sizes != last_sizes:
                    last_activity = observed_at
                    review_output_changed = (
                        sizes[0] != last_sizes[0] or sizes[2] != last_sizes[2]
                    )
                    diagnostic_changed = sizes[1] != last_sizes[1]
                    if review_output_changed:
                        saw_review_output = saw_review_output or bool(
                            sizes[0] or sizes[2]
                        )
                        activity_kind = "output"
                    elif diagnostic_changed:
                        activity_kind = "diagnostic"
                    last_sizes = sizes
                cpu_time = _cpu_time_sec(process, job)
                if cpu_time is not None and cpu_time > last_cpu + 0.01:
                    last_activity = observed_at
                    last_cpu = cpu_time
                    if activity_kind == "heartbeat":
                        activity_kind = "cpu"
                if on_progress and (
                    last_callback == 0.0
                    or observed_at - last_callback >= heartbeat_interval_sec
                ):
                    try:
                        on_progress(
                            process.pid,
                            ownership,
                            *sizes,
                            elapsed,
                            activity_kind,
                            last_cpu,
                        )
                    except Exception as exc:
                        progress_failure_count += 1
                        last_progress_error = f"{type(exc).__name__}: {exc}"[:500]
                    last_callback = observed_at
                if sizes[0] > events_limit or sizes[1] > stderr_limit or sizes[2] > result_limit:
                    terminal_status = "output_limit"
                    detail = "provider output exceeded a hard byte limit"
                    break
                try:
                    should_cancel = bool(cancel_requested and cancel_requested())
                except Exception as exc:
                    terminal_status = "error"
                    detail = f"cancellation monitor failed: {exc}"
                    break
                if should_cancel:
                    terminal_status = "cancelled"
                    detail = "cancellation requested by the primary orchestrator"
                    break
                if elapsed >= timeout_sec:
                    terminal_status = "timed_out"
                    detail = "provider exceeded the hard deadline"
                    break
                if (
                    not saw_review_output
                    and elapsed >= min(first_output_timeout_sec, timeout_sec)
                ):
                    terminal_status = "timed_out"
                    detail = (
                        "provider produced no review output before the first-output deadline"
                    )
                    break
                if saw_review_output and observed_at - last_activity >= idle_timeout_sec:
                    terminal_status = "idle_killed"
                    detail = "provider showed no output or CPU activity after starting"
                    break

            sizes = (_size(events_path), _size(stderr_path), _size(result_path))
            if terminal_status is None and (
                sizes[0] > events_limit
                or sizes[1] > stderr_limit
                or sizes[2] > result_limit
            ):
                terminal_status = "output_limit"
                detail = "provider output exceeded a hard byte limit"
            killed = terminal_status is not None and process.poll() is None
            kill_verified = _kill_tree(process, job) if killed else False
            exit_code = process.poll()
            final_cpu = _cpu_time_sec(process, job)
            if final_cpu is not None:
                last_cpu = max(last_cpu, final_cpu)
            if terminal_status is None:
                terminal_status = "completed" if exit_code == 0 else "error"
                if exit_code != 0:
                    detail = f"provider exited with code {exit_code}"
            if progress_failure_count:
                degraded = (
                    "progress publication degraded "
                    f"({progress_failure_count} callback failure(s)); latest: "
                    f"{last_progress_error}"
                )
                detail = f"{detail}; {degraded}" if detail else degraded
                detail = detail[:2000]
            return ProcessOutcome(
                terminal_status,
                exit_code,
                process.pid,
                ownership,
                killed,
                kill_verified,
                round(time.monotonic() - started, 3),
                *sizes,
                detail,
                round(last_cpu, 3),
                round(max(0.0, time.monotonic() - last_activity), 3),
            )
    finally:
        if process is not None and process.poll() is None:
            _kill_tree(process, job)
        job.close()

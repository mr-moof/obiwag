from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.peer_review.process_control import run_managed


def paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    stdin = tmp_path / "stdin.txt"
    stdin.write_text("prompt", encoding="utf-8")
    return stdin, tmp_path / "events", tmp_path / "stderr", tmp_path / "result"


def test_clean_process_is_observable(tmp_path: Path) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    script = tmp_path / "clean.py"
    script.write_text(
        "import pathlib; print('event', flush=True); pathlib.Path(r'%s').write_text('{}')\n"
        % result,
        encoding="utf-8",
    )
    outcome = run_managed(
        [sys.executable, str(script)],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=10,
        env=os.environ,
    )
    assert outcome.status == "completed"
    assert outcome.exit_code == 0
    assert outcome.events_bytes > 0
    if os.name == "nt":
        assert outcome.ownership_mode == "windows_job"


def test_nonzero_exit_has_bounded_status_detail(tmp_path: Path) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    outcome = run_managed(
        [sys.executable, "-c", "raise SystemExit(7)"],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=10,
        env=os.environ,
    )
    assert outcome.status == "error"
    assert outcome.exit_code == 7
    assert outcome.detail == "provider exited with code 7"


def test_heartbeat_and_cancel_are_observable_without_provider_output(tmp_path: Path) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    observations = []
    started = time.monotonic()
    outcome = run_managed(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=10,
        env=os.environ,
        heartbeat_interval_sec=0.1,
        on_progress=lambda *values: observations.append(values),
        cancel_requested=lambda: time.monotonic() - started > 0.5,
    )
    assert outcome.status == 'cancelled'
    assert outcome.kill_verified is True
    assert observations
    assert any(item[6] == 'heartbeat' for item in observations)


def test_output_limit_kills_only_managed_process(tmp_path: Path) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys,time; sys.stdout.write('x'*20000); sys.stdout.flush(); time.sleep(30)\n",
        encoding="utf-8",
    )
    outcome = run_managed(
        [sys.executable, str(script)],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=10,
        env=os.environ,
        events_limit=1024,
    )
    assert outcome.status == "output_limit"
    assert outcome.killed is True
    assert outcome.kill_verified is True


def test_output_limit_is_enforced_when_noisy_process_exits_before_first_poll(tmp_path: Path, monkeypatch) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    # Guarantee the named condition instead of racing Python startup against the first poll.
    import tools.peer_review.process_control as control
    original_popen = subprocess.Popen

    def already_finished(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        process.wait(timeout=10)
        return process

    monkeypatch.setattr(control.subprocess, 'Popen', already_finished)
    outcome = run_managed(
        [sys.executable, '-c', "import sys; sys.stdout.write('x'*20000)"],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=10,
        env=os.environ,
        events_limit=1024,
    )
    assert outcome.status == 'output_limit'
    assert outcome.killed is False
    assert outcome.events_bytes > 1024


def test_cancel_monitor_failure_is_terminal_and_visible(tmp_path: Path) -> None:
    stdin, events, stderr, result = paths(tmp_path)

    def broken_monitor() -> bool:
        raise OSError('marker read failed')

    outcome = run_managed(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=10,
        env=os.environ,
        cancel_requested=broken_monitor,
    )
    assert outcome.status == 'error'
    assert outcome.killed is True
    assert outcome.kill_verified is True
    assert outcome.detail == 'cancellation monitor failed: marker read failed'


def test_progress_callback_failure_does_not_abort_healthy_provider(tmp_path: Path) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    calls = 0

    def flaky_progress(*_values) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError(5, 'status temporarily unavailable')

    outcome = run_managed(
        [
            sys.executable,
            '-c',
            "import time; time.sleep(.8); print('review output',flush=True)",
        ],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=5,
        env=os.environ,
        heartbeat_interval_sec=0.1,
        on_progress=flaky_progress,
    )
    assert outcome.status == 'completed'
    assert calls >= 2
    assert 'progress publication degraded' in str(outcome.detail)


def test_stderr_noise_does_not_satisfy_first_review_output_deadline(
    tmp_path: Path,
) -> None:
    stdin, events, stderr, result = paths(tmp_path)
    outcome = run_managed(
        [
            sys.executable,
            '-c',
            "import sys,time; sys.stderr.write('startup noise\\n'); "
            "sys.stderr.flush(); time.sleep(30)",
        ],
        cwd=tmp_path,
        stdin_path=stdin,
        events_path=events,
        stderr_path=stderr,
        result_path=result,
        timeout_sec=5,
        env=os.environ,
        first_output_timeout_sec=0.75,
        idle_timeout_sec=4,
    )
    assert outcome.status == 'timed_out'
    assert outcome.elapsed_sec < 2
    assert 'no review output' in str(outcome.detail)
    assert outcome.stderr_bytes > 0


def _windows_pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object acceptance test")
def test_job_object_kills_child_when_owner_is_force_stopped(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text(
        f"import os,time,pathlib; pathlib.Path(r'{marker}').write_text(str(os.getpid())); time.sleep(60)\n",
        encoding="utf-8",
    )
    controller = tmp_path / "controller.py"
    controller.write_text(
        "import os,sys,pathlib\n"
        "from tools.peer_review.process_control import run_managed\n"
        f"root=pathlib.Path(r'{tmp_path}')\n"
        "(root/'stdin').write_text('x')\n"
        f"run_managed([sys.executable,r'{child}'],cwd=root,stdin_path=root/'stdin',events_path=root/'events',stderr_path=root/'err',result_path=root/'raw',timeout_sec=60,env=os.environ)\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    repo_root = Path(__file__).resolve().parents[3]
    environment["PYTHONPATH"] = str(repo_root)
    owner = subprocess.Popen(
        [sys.executable, str(controller)],
        cwd=repo_root,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.time() + 10
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.1)
    assert marker.exists(), "managed child never started"
    child_pid = int(marker.read_text())
    taskkill = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
    subprocess.run([str(taskkill), "/F", "/PID", str(owner.pid)], check=False, capture_output=True)
    owner.wait(timeout=10)
    deadline = time.time() + 10
    while _windows_pid_alive(child_pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not _windows_pid_alive(child_pid), "Job Object did not kill child on owner loss"

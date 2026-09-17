"""Tests for the PreToolUse duration guard (pre_tool_use.check_duration_guard).

The guard forces likely-long foreground shell calls to either background or
carry an explicit bounded timeout, so a dropped tool result can't park the
turn unbounded.
"""
import os
import sys

HOOK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)

import pre_tool_use as ptu  # noqa: E402


# --- allowed cases ---------------------------------------------------------

def test_child_owning_supervisors_must_run_foreground():
    # Their terminal state and child ownership die with the wrapper process;
    # background task cancellation is therefore unsafe.
    reason = ptu.check_duration_guard(
        {"command": "powershell -NoProfile -File $OBI_HOME/tools/dispatch-worker.ps1 "
                    "-Persona obi-discovery -TimeoutSec 900", "run_in_background": True}
    )
    assert reason and "FOREGROUND" in reason
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/peer-review.ps1 run -RepoRoot . -RequestFile request.json",
         "run_in_background": True}
    )
    assert reason and "FOREGROUND" in reason
    assert "peer-review.ps1 start" in reason


def test_child_owning_supervisors_allowed_with_explicit_bounded_timeout():
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -TimeoutSec 270",
         "timeout": 300000}
    ) is None
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/peer-review.ps1 run -TimeoutSec 240 -RepoRoot . -RequestFile request.json",
         "timeout": 570000}
    ) is None
    # A supervisor may use the whole 600 s host window; the plain foreground cap
    # (300 s) and the Pester-only allowance do not apply to it.
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/peer-review.ps1 start -RepoRoot . -RequestFile request.json",
         "timeout": 600000}
    ) is None


def test_supervisor_without_explicit_timeout_is_blocked():
    # Issue #200 T1: an unstated outer bound lets the harness kill the call
    # before the supervisor writes a terminal status.
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -TimeoutSec 270"}
    )
    assert reason and "EXPLICIT timeout" in reason


def test_supervisor_timeout_above_host_ceiling_is_blocked():
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -TimeoutSec 780",
         "timeout": 810000}
    )
    assert reason and "host ceiling" in reason


def test_supervisor_timeout_below_its_own_deadline_is_blocked():
    # Peer finding PR-003 (run 20260901T221915Z): 120 s wrapping a 540 s dispatch
    # is killed by the harness before the terminal status lands.
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -Phase 2 -TimeoutSec 540",
         "timeout": 120000}
    )
    assert reason and "below the supervisor's own deadline" in reason
    assert "570000" in reason
    # Exactly the deadline plus margin is allowed.
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -Phase 2 -TimeoutSec 540",
         "timeout": 570000}
    ) is None
    # peer-review run defaults to 240 s: 200 s is too short, 270 s is fine.
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/peer-review.ps1 run -RepoRoot . -RequestFile r.json",
         "timeout": 200000}
    )
    assert reason and "below the supervisor's own deadline" in reason
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/peer-review.ps1 run -RepoRoot . -RequestFile r.json",
         "timeout": 270000}
    ) is None


def test_durable_start_and_control_plane_calls_have_no_floor():
    # Peer finding COD-001: the documented durable review command is
    # `peer-review.ps1 start ... -TimeoutSec 1800`; that -TimeoutSec is the detached
    # broker's deadline, not this call's, so a short outer timeout must be accepted.
    for op in ("start -RepoRoot . -RequestFile r.json -Provider auto -Platform claude -Authorization auto -TimeoutSec 1800",
               "status -RepoRoot . -RunId abc",
               "wait -RepoRoot . -RunId abc -Until terminal -WaitSec 240",
               "result -RepoRoot . -RunId abc",
               "cancel -RepoRoot . -RunId abc",
               "preflight -RepoRoot . -RequestFile r.json"):
        assert ptu.check_duration_guard(
            {"command": f"powershell -NoProfile -File $env:OBI_HOME/tools/peer-review.ps1 {op}",
             "timeout": 120000}
        ) is None, op
    # peer-plan-review defaults to a durable start as well.
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/peer-plan-review.ps1 -PlanPath p.md -Platform claude -Operation start -TimeoutSec 1800",
         "timeout": 120000}
    ) is None


def test_supervisor_floor_resolves_the_phase_budget_from_the_phase_table(tmp_path, monkeypatch):
    table = {
        "delegation_policy_defaults": {"claude": {"absolute_sec": 270, "cleanup_margin_sec": 30}},
        "phases": [
            {"n": 2, "delegation_policy": {"claude": {"absolute_sec": 540, "cleanup_margin_sec": 30}}},
            {"n": 10},
        ],
    }
    (tmp_path / "phases").mkdir()
    (tmp_path / "phases" / "phase-table.json").write_text(__import__("json").dumps(table), encoding="utf-8")
    monkeypatch.setenv("OBI_HOME", str(tmp_path))
    # Phase 2 without -TimeoutSec: the outer call must cover 540 + 30 s.
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -Persona obi-author -Phase 2",
         "timeout": 300000}
    )
    assert reason and "570000" in reason and "phase 2" in reason
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -Persona obi-author -Phase 2",
         "timeout": 570000}
    ) is None
    # A phase without its own policy uses the defaults (270 + 30 s).
    assert ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -Persona obi-learner -Phase 10",
         "timeout": 300000}
    ) is None
    # No readable table anywhere (OBI_HOME, OBIWAG_SOURCE, and the hook's own repo
    # are all candidates): fall back to the documented default budget, never crash.
    monkeypatch.setattr(
        ptu, "_phase_table_candidates",
        lambda: [str(tmp_path / "missing" / "phases" / "phase-table.json")],
    )
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/dispatch-worker.ps1 -Phase 2", "timeout": 120000}
    )
    assert reason and "default dispatch budget" in reason


def test_quick_backgrounded_command_allowed():
    # A quick, non-slow backgrounded command still returns control immediately.
    assert ptu.check_duration_guard(
        {"command": "git fetch origin", "run_in_background": True}
    ) is None


def test_bounded_foreground_build_allowed():
    # The bounded foreground equivalent of a background build is allowed.
    assert ptu.check_duration_guard(
        {"command": "powershell -File build.ps1 -Task All", "timeout": 300000}
    ) is None


def test_slow_command_with_bounded_timeout_allowed():
    assert ptu.check_duration_guard(
        {"command": "pip install foo", "timeout": 60000}
    ) is None


def test_fast_command_allowed():
    assert ptu.check_duration_guard({"command": "git status"}) is None
    assert ptu.check_duration_guard({"command": "ls -la"}) is None


def test_timeout_at_cap_allowed():
    assert ptu.check_duration_guard(
        {"command": "echo hi", "timeout": 300000}
    ) is None


def test_pester_only_commands_allowed_at_extended_cap():
    for cmd in (
        "powershell -File tools/run-tests.ps1 -PowerShellOnly -ShardCount 3 -ShardIndex 3",
        "powershell -File tools/run-tests.ps1 -Path hooks/example.tests.ps1",
        "Invoke-Pester -Path tests",
    ):
        assert ptu.check_duration_guard(
            {"command": cmd, "timeout": 600000}
        ) is None, cmd


# --- blocked cases ---------------------------------------------------------

def test_slow_command_foreground_no_timeout_blocked():
    for cmd in (
        "npx eslint crosswalk.js",
        "npm install",
        "npm ci",
        "yarn build",
        "pnpm install",
        "pip install psycopg2",
        "pip3 install -r reqs.txt",
        "uv run scripts/noggin_ask.py",
        "docker build -t x .",
        "docker compose up -d",
        "git clone https://example/repo",
    ):
        reason = ptu.check_duration_guard({"command": cmd})
        # Foreground slow command with no timeout is nudged to add a timeout or
        # use the bounded supervisor (backgrounding a raw slow command is now denied).
        assert reason and ("timeout" in reason or "supervisor" in reason), cmd


def test_explicit_timeout_over_cap_blocked():
    reason = ptu.check_duration_guard({"command": "echo hi", "timeout": 400000})
    assert reason and "5 min" in reason


def test_non_pester_run_tests_commands_retain_default_cap():
    for cmd in (
        "powershell -File tools/run-tests.ps1",
        "powershell -File tools/run-tests.ps1 -PythonOnly",
        "powershell -File tools/run-tests.ps1 -PowerShellOnly:$false",
        "powershell -File tools/run-tests.ps1 -Path hooks/example.tests.ps1,hooks/test_example.py",
        "Invoke-Pester -Path tests; pytest hooks/tests",
    ):
        reason = ptu.check_duration_guard({"command": cmd, "timeout": 600000})
        assert reason and "5 min" in reason, cmd


def test_pester_timeout_over_extended_cap_blocked():
    reason = ptu.check_duration_guard(
        {"command": "powershell -File tools/run-tests.ps1 -PowerShellOnly", "timeout": 600001}
    )
    assert reason and "10 min" in reason


def test_backgrounded_raw_slow_command_denied():
    # INVERSE of the old "background blesses anything" behavior (issue #200 §4):
    # a raw long-running command backgrounded is wall-clock-killed with no
    # durable terminal state, so it is now denied.
    reason = ptu.check_duration_guard(
        {"command": "npx eslint .", "run_in_background": True}
    )
    assert reason and "wall-clock" in reason


def test_backgrounded_raw_command_with_absurd_timeout_denied():
    # An absurd timeout does NOT bless a raw background install/build anymore.
    reason = ptu.check_duration_guard(
        {"command": "npm install", "timeout": 999999, "run_in_background": True}
    )
    assert reason and "wall-clock" in reason


def test_backgrounded_build_test_pipeline_denied():
    for cmd in (
        "powershell -File build.ps1 -Task All",
        "powershell -File tools/run-tests.ps1 -PowerShellOnly",
        "dotnet build",
        "dotnet test",
        "Invoke-Pester -Path tests",
        "pytest tests/",
        "gh run watch",
        "msbuild Foo.sln",
    ):
        reason = ptu.check_duration_guard({"command": cmd, "run_in_background": True})
        assert reason, cmd


def test_background_build_tokens_not_matched_inside_paths():
    # Anchored build tokens must not false-deny a background command that merely CONTAINS
    # a build word as a path/filename substring (Opus review #4).
    for cmd in (
        "./scripts/make-release.sh",
        "cat pytest.ini",
        "./msbuild-helper.sh run",
        "python cmake_wrapper.py",
    ):
        assert ptu.check_duration_guard({"command": cmd, "run_in_background": True}) is None, cmd
    # but the real commands are still denied
    assert ptu.check_duration_guard({"command": "make all", "run_in_background": True})
    assert ptu.check_duration_guard({"command": "pytest tests/", "run_in_background": True})


def test_acceptance_raw_background_build_rejected_bounded_foreground_allowed():
    # issue #200 §6 acceptance: a raw background build/test with an excessive
    # timeout is rejected; the bounded foreground equivalent is allowed.
    rejected = ptu.check_duration_guard(
        {"command": "build.ps1 -Task All", "run_in_background": True, "timeout": 3600000}
    )
    assert rejected
    allowed = ptu.check_duration_guard(
        {"command": "build.ps1 -Task All", "timeout": 300000}
    )
    assert allowed is None


# --- end-to-end through _handle (block shape) ------------------------------

class _Timer:
    def set_input_summary(self, *_): pass
    def set_output_summary(self, *_): pass


def test_handle_blocks_slow_bash():
    out = ptu._handle(
        {"tool_name": "Bash", "tool_input": {"command": "npx eslint ."}}, _Timer()
    )
    assert out.get("decision") == "block"


def test_handle_blocks_slow_powershell():
    out = ptu._handle(
        {"tool_name": "PowerShell", "tool_input": {"command": "pip install foo"}}, _Timer()
    )
    assert out.get("decision") == "block"


def test_handle_allows_fast_bash():
    out = ptu._handle(
        {"tool_name": "Bash", "tool_input": {"command": "git status"}}, _Timer()
    )
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

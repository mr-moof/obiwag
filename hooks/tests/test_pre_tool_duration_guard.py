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

def test_backgrounded_slow_command_allowed():
    assert ptu.check_duration_guard(
        {"command": "npx eslint .", "run_in_background": True}
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
        "uv run scripts/long_task.py",
        "docker build -t x .",
        "docker compose up -d",
        "git clone https://example/repo",
    ):
        reason = ptu.check_duration_guard({"command": cmd})
        assert reason and "run_in_background" in reason, cmd


def test_explicit_timeout_over_cap_blocked():
    reason = ptu.check_duration_guard({"command": "echo hi", "timeout": 400000})
    assert reason and "5 min" in reason


def test_backgrounded_overrides_over_cap_timeout():
    # background wins: even an absurd timeout is fine if backgrounded
    assert ptu.check_duration_guard(
        {"command": "npm install", "timeout": 999999, "run_in_background": True}
    ) is None


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

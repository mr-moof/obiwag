#!/usr/bin/env python3
"""PreToolUse hook for Obi Memory System.

This hook runs before each tool use to:
1. Block Bash anti-patterns (cd &&, cat, grep, find) that should use dedicated tools
2. Block heredoc/inline-python patterns that pollute settings.local.json
3. Inject a reminder when the agent edits a test file

Regex-shaped rules live in ``core/pre_tool_rules.py`` so each rule has a
stable id.  Procedural checks (heredoc-#-lines, long-inline-python,
Windows backslash paths) stay here because they need non-regex
evaluation logic.
"""

import json
import os
import re
import sys

# Add hook root to path for imports
HOOK_ROOT = os.path.dirname(os.path.abspath(__file__))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)


def _heredoc_has_hash_lines(command: str) -> bool:
    """Detect if a heredoc body contains lines starting with #.

    Claude Code's built-in safety check flags any Bash command that has a
    quoted newline followed by a #-prefixed line. This catches the pattern
    before Claude Code prompts the user for permission.
    """
    # Match heredoc start: <<EOF, <<'EOF', <<"EOF", << 'DELIM', etc.
    heredoc_match = re.search(r"<<\s*['\"]?(\w+)['\"]?", command)
    if not heredoc_match:
        return False
    delimiter = heredoc_match.group(1)
    # Extract content between heredoc start and delimiter.
    body_match = re.search(
        rf"<<\s*['\"]?{re.escape(delimiter)}['\"]?\s*\n(.*?)\n\s*{re.escape(delimiter)}",
        command,
        re.DOTALL,
    )
    if not body_match:
        # Heredoc may not have explicit newlines in the command string;
        # check for # after any newline in the entire command.
        after_heredoc = command[heredoc_match.end():]
        return bool(re.search(r"\n\s*#", after_heredoc))
    body = body_match.group(1)
    return bool(re.search(r"(?m)^\s*#", body))


# Threshold for inline python -c scripts. Commands longer than this
# trigger approval prompts, and "don't ask again" pollutes settings.local.json.
_INLINE_PYTHON_MAX_LEN = 200


def _is_long_inline_python(command: str) -> bool:
    """Detect long inline python -c scripts that risk settings pollution.

    When Claude Code prompts for approval on a long command and the user
    picks "don't ask again", the ENTIRE command string gets saved as a
    permission pattern in settings.local.json — corrupting it.
    """
    match = re.search(r'python(?:3|\.exe)?\s+-c\s+["\']', command)
    if not match:
        return False
    after_flag = command[match.end():]
    return len(after_flag) > _INLINE_PYTHON_MAX_LEN


def _has_windows_backslash_path(command: str) -> bool:
    """Detect Windows-style backslash paths that fail in bash.

    Bash interprets backslashes as escape characters, so
    C:\\Python314\\python.exe becomes C:Python314python.exe and fails.
    Forward-slash paths (C:/Python314/python.exe) or bare commands
    (python) work correctly in Git Bash on Windows.
    """
    # Match drive-letter paths with backslashes: C:\..., D:\...
    # Exclude paths inside single-quoted strings (they're literal in bash).
    # Double-quoted strings are NOT excluded — bash still interprets \t, \n.
    return bool(re.search(r"(?<!['])[A-Za-z]:\\\w", command))


# --- Duration guard -----------------------------------------------------------
# A foreground shell call parks the turn until it returns; if the harness drops
# the result (the "[Tool result missing due to internal error]" / silent-hang
# failure) a long foreground call can sit unbounded with no way to interrupt it
# from inside the call. The guard forces likely-long commands to either run
# backgrounded (control returns immediately; poll the output file) or carry an
# explicit bounded timeout. The default foreground cap is 5 min; recognized
# Pester-only commands get 10 min because deterministic release shards can
# legitimately exceed the default without being stalled.

# Foreground runtime caps (ms). Keep the Pester exception narrow: the mixed
# no-argument run-tests.ps1 suite and every non-Pester command retain 5 min.
_FOREGROUND_TIMEOUT_CAP_MS = 300000
_PESTER_FOREGROUND_TIMEOUT_CAP_MS = 600000
# Absolute host ceiling for one foreground tool call. A bounded supervisor may
# use the whole window, but never more: past it the harness -- not the
# supervisor -- ends the call, and the terminal status is lost.
_SUPERVISOR_TIMEOUT_CAP_MS = 600000

_RUN_TESTS_ENTRYPOINT_RE = re.compile(r"\brun-tests\.ps1\b", re.IGNORECASE)
_POWERSHELL_ONLY_ARG_RE = re.compile(
    r"(?<!\w)-PowerShellOnly\b(?![:=])", re.IGNORECASE
)
_PYTHON_ONLY_ARG_RE = re.compile(r"(?<!\w)-PythonOnly\b", re.IGNORECASE)
_PESTER_TEST_PATH_RE = re.compile(r"\.tests\.ps1(?:['\"])?(?:\s|,|$)", re.IGNORECASE)
_PYTHON_TEST_PATH_RE = re.compile(r"\.py(?:['\"])?(?:\s|,|$)", re.IGNORECASE)
_INVOKE_PESTER_RE = re.compile(r"\bInvoke-Pester\b", re.IGNORECASE)
_COMMAND_CHAIN_RE = re.compile(r"(?:&&|\|\||[|;\r\n])")


def _is_pester_only_command(command: str) -> bool:
    """Return True only when the command is unambiguously Pester-only."""
    if _COMMAND_CHAIN_RE.search(command):
        return False
    if _PYTHON_ONLY_ARG_RE.search(command) or _PYTHON_TEST_PATH_RE.search(command):
        return False
    if _INVOKE_PESTER_RE.search(command):
        return True
    if not _RUN_TESTS_ENTRYPOINT_RE.search(command):
        return False
    return bool(
        _POWERSHELL_ONLY_ARG_RE.search(command)
        or _PESTER_TEST_PATH_RE.search(command)
    )


def _foreground_timeout_cap_ms(command: str) -> int:
    """Select the approved cap for this foreground command."""
    if _is_pester_only_command(command):
        return _PESTER_FOREGROUND_TIMEOUT_CAP_MS
    return _FOREGROUND_TIMEOUT_CAP_MS

# Commands that are "go get coffee" slow (network installs, cold-start tooling,
# clones). Conservative + high-confidence to keep false positives low. ssh/scp/
# curl/pytest are intentionally excluded (too context-dependent).
_SLOW_FOREGROUND_RE = re.compile(
    r"""\b(
        npx
      | npm\s+(?:install|ci|i|run\s+\w+)
      | (?:yarn|pnpm)\s+(?:install|add|build|run)
      | (?:pip|pip3)\s+install
      | uv\s+(?:run|sync|pip\s+install)
      | docker\s+(?:build|pull|push)
      | docker\s+compose\s+(?:up|build)
      | git\s+clone
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)

# Supervisors have different ownership contracts; matching a wrapper name is
# not proof that shell-level background execution is safe. dispatch-worker and
# every peer-review API call stay foreground. peer-review start returns a
# receipt quickly and its Python broker performs the only supported detach;
# shell background cancellation would still risk losing that receipt.
_FOREGROUND_SUPERVISOR_RE = re.compile(
    r"(dispatch-worker\.ps1|peer-review\.ps1)",
    re.IGNORECASE,
)
# Raw long-running commands that must NOT be launched as a bare background job
# (issue #200 §4). Backgrounded, they run detached with no durable terminal
# state; the environment wall-clock kills them silently and the orchestrator
# cannot distinguish "killed" from "completed" (the observed obi-auto-max
# failure). They must run through the bounded supervisor, or foreground with a
# timeout within the command-class cap. Superset of _SLOW_FOREGROUND_RE plus the
# build/test/lint/pipeline-wait class the issue calls out.
_BACKGROUND_DENY_RE = re.compile(
    r"""(
        \bnpx\b
      | \bnpm\s+(?:install|ci|i|run\s+\w+|test)
      | \b(?:yarn|pnpm)\s+(?:install|add|build|run|test)
      | \b(?:pip|pip3)\s+install
      | \buv\s+(?:run|sync|pip\s+install)
      | \bdocker\s+(?:build|pull|push)
      | \bdocker\s+compose\s+(?:up|build)
      | \bgit\s+clone
      | build\.ps1
      | \bdotnet\s+(?:build|test|publish|restore)
      | \bmsbuild\b(?![-.])
      | \bgradlew?\b(?![-.])
      | \bmvn\b(?![-.])
      | \bmake\b(?![-.])
      | \bcargo\s+(?:build|test)
      | \bgo\s+(?:build|test)
      | \bpytest\b(?![-.])
      | Invoke-Pester
      | run-tests\.ps1
      | \bgh\s+run\s+watch
    )""",
    re.IGNORECASE | re.VERBOSE,
)


_DISPATCH_WORKER_RE = re.compile(r"dispatch-worker\.ps1", re.IGNORECASE)
_PEER_REVIEW_RUN_RE = re.compile(r"peer-review\.ps1\s+run\b", re.IGNORECASE)
_TIMEOUT_SEC_ARG_RE = re.compile(r"-TimeoutSec\s+(\d+)", re.IGNORECASE)
_PHASE_ARG_RE = re.compile(r"-Phase\s+(\d+)", re.IGNORECASE)
_DEFAULT_CLEANUP_MARGIN_SEC = 30
_DEFAULT_DISPATCH_ABSOLUTE_SEC = 270
_DEFAULT_PEER_RUN_TIMEOUT_SEC = 240


def _phase_table_candidates() -> list[str]:
    roots = [os.environ.get("OBI_HOME"), os.environ.get("OBIWAG_SOURCE"),
             os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    return [os.path.join(r, "phases", "phase-table.json") for r in roots if r]


def _phase_outer_sec(phase: int) -> int | None:
    """absolute_sec + cleanup_margin_sec for the Claude delegation policy of `phase`.

    Reads the same phase table the supervisor uses (OBI_HOME first). Any failure
    returns None so the caller falls back to the documented default budget.
    """
    for path in _phase_table_candidates():
        try:
            if os.path.getsize(path) > 2 * 1024 * 1024:
                continue
            with open(path, "r", encoding="utf-8") as handle:
                table = json.load(handle)
        except (OSError, ValueError):
            continue
        defaults = (table.get("delegation_policy_defaults") or {}).get("claude") or {}
        policy = dict(defaults)
        for entry in table.get("phases") or []:
            if entry.get("n") == phase:
                policy.update(((entry.get("delegation_policy") or {}).get("claude")) or {})
                break
        try:
            return int(policy["absolute_sec"]) + int(policy.get("cleanup_margin_sec", _DEFAULT_CLEANUP_MARGIN_SEC))
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _supervisor_timeout_floor_ms(command: str) -> tuple[int, str]:
    """Smallest outer timeout that still outlives the supervisor's own deadline.

    Peer finding PR-003: accepting any positive timeout let a 120 000 ms outer
    call wrap a 540 s dispatch, which the harness then killed mid-run. An
    explicit `-TimeoutSec N` wins; otherwise a dispatch-worker call resolves its
    `-Phase` budget from the phase table (default policy when unresolvable) and a
    `peer-review.ps1 run` uses its documented 240 s default.
    """
    # Classify the operation FIRST (peer finding COD-001): only a call that OWNS a provider
    # process for its whole duration has a deadline the outer timeout must outlive --
    # dispatch-worker.ps1, and peer-review.ps1 `run`. `start` returns a receipt and detaches a
    # broker worker whose -TimeoutSec (up to 3600) is the broker's deadline, not this call's;
    # status/wait/result/cancel/preflight own nothing. Their floor is zero.
    owns_provider = bool(_DISPATCH_WORKER_RE.search(command)) or bool(_PEER_REVIEW_RUN_RE.search(command))
    if not owns_provider:
        return 0, "no foreground supervisor deadline (receipt or control-plane operation)"
    match = _TIMEOUT_SEC_ARG_RE.search(command)
    if match:
        sec = int(match.group(1)) + _DEFAULT_CLEANUP_MARGIN_SEC
        return sec * 1000, f"-TimeoutSec {match.group(1)} + {_DEFAULT_CLEANUP_MARGIN_SEC} s cleanup margin"
    if _DISPATCH_WORKER_RE.search(command):
        phase_match = _PHASE_ARG_RE.search(command)
        if phase_match:
            outer = _phase_outer_sec(int(phase_match.group(1)))
            if outer is not None:
                return outer * 1000, f"phase {phase_match.group(1)} delegation_policy"
        return (_DEFAULT_DISPATCH_ABSOLUTE_SEC + _DEFAULT_CLEANUP_MARGIN_SEC) * 1000, "the default dispatch budget"
    return (_DEFAULT_PEER_RUN_TIMEOUT_SEC + _DEFAULT_CLEANUP_MARGIN_SEC) * 1000, "the default peer-review run budget"


def check_duration_guard(tool_input: dict) -> str | None:
    """Guard shell calls that risk a silent stall (issue #200 §4).

    Background is no longer a blanket exemption. The execution-mode matrix is
    explicit: dispatch-worker/peer-review entrypoints must remain foreground so
    their receipt/terminal state cannot be discarded. A RAW long-running command
    backgrounded is denied.
    Foreground calls retain the 5-minute outer cap except for a narrow 10-minute
    Pester-only allowance. Internally-bounded supervisors may omit a shell
    timeout because their own deadline is earlier.
    Returns the block reason, or None to allow.
    """
    command = tool_input.get("command", "") or ""

    if tool_input.get("run_in_background") is True:
        if _FOREGROUND_SUPERVISOR_RE.search(command):
            return (
                "This supervisor must run FOREGROUND. Background task cancellation "
                "can discard its durable receipt or terminal status. Run it foreground. "
                "For a long review use peer-review.ps1 start; the broker owns the "
                "detach and returns a RunId immediately."
            )
        if _BACKGROUND_DENY_RE.search(command):
            return (
                "Raw long-running command backgrounded: detached, it is "
                "wall-clock-killed with no durable terminal state, so the "
                "orchestrator can't tell 'killed' from 'completed' (issue #200). "
                "Run it foreground with an explicit bounded timeout (<= 600000 ms "
                "for a recognized Pester-only command; <= 300000 ms otherwise), or "
                "through the bounded supervisor (dispatch-worker.ps1 / "
                "peer-review.ps1) which records a terminal status. For a CI/pipeline "
                "wait, use ONE poll-until-terminal command that exits on terminal "
                "state, not a background job that waits."
            )
        return None  # a quick backgrounded command returns control immediately

    timeout = tool_input.get("timeout")
    has_explicit_timeout = isinstance(timeout, (int, float)) and timeout > 0

    # Foreground supervisors are internally bounded, but the harness kills the
    # outer call at its own ceiling. Without an explicit timeout the caller
    # inherits the default cap, which can be EARLIER than the supervisor's own
    # deadline -- the call dies before a terminal status is written (issue #200
    # T1). Require the outer bound to be stated and to be <= the host ceiling.
    if _FOREGROUND_SUPERVISOR_RE.search(command):
        if not has_explicit_timeout:
            return (
                "Bounded supervisors must be called with an EXPLICIT timeout so "
                "the outer call outlives the supervisor's own deadline. Without "
                "one the harness applies its own default cap (120 s in Claude "
                "Code), which is earlier than the supervisor's deadline and can "
                "kill the call before it writes a terminal status, leaving a "
                "stale 'running' record and an orphan child. Pass timeout = the "
                "phase's absolute_sec + cleanup_margin_sec in ms "
                f"(<= {_SUPERVISOR_TIMEOUT_CAP_MS})."
            )
        if timeout > _SUPERVISOR_TIMEOUT_CAP_MS:
            return (
                "Supervisor timeout exceeds the host ceiling "
                f"({_SUPERVISOR_TIMEOUT_CAP_MS} ms). The harness would kill the "
                "call before the supervisor can persist a terminal status. Lower "
                "the timeout, or lower the phase budget in phases/phase-table.json "
                "so absolute_sec + cleanup_margin_sec fits."
            )
        floor_ms, floor_why = _supervisor_timeout_floor_ms(command)
        if timeout < floor_ms:
            return (
                f"Supervisor timeout {int(timeout)} ms is below the supervisor's own "
                f"deadline plus cleanup margin ({floor_ms} ms, from {floor_why}). The "
                "harness would kill the call before the supervisor finishes, leaving "
                "a stale 'running' record. Pass timeout = the phase's absolute_sec + "
                "cleanup_margin_sec in ms (get-dispatch-phase-policy.ps1 -Phase <N> "
                "prints outer_timeout_ms)."
            )
        return None

    timeout_cap = _foreground_timeout_cap_ms(command)
    if has_explicit_timeout and timeout > timeout_cap:
        if timeout_cap == _PESTER_FOREGROUND_TIMEOUT_CAP_MS:
            return (
                "Pester-only foreground shell calls are capped at 10 min "
                "(timeout <= 600000 ms). Lower the timeout or split the Pester "
                "selection into bounded shards."
            )
        return (
            "Foreground shell calls are capped at 5 min (timeout <= 300000 ms); "
            "only recognized Pester-only commands may use up to 600000 ms. "
            "Lower the timeout, or run it through the bounded supervisor "
            "(dispatch-worker.ps1 / peer-review.ps1) for long-running work."
        )

    if _SLOW_FOREGROUND_RE.search(command) and not has_explicit_timeout:
        return (
            "This looks long-running (network install / cold-start tooling) and "
            "is foreground with no explicit timeout, so a dropped result would "
            "park the turn unbounded. Add an explicit timeout <= 300000 ms, or "
            "run it through the bounded supervisor (dispatch-worker.ps1)."
        )
    return None


# Test file patterns — matched against file_path for Edit/Write operations.
# If matched, a reminder is injected (not a block): fix the code under test, not the test.
TEST_FILE_PATTERNS = [
    r'[/\\]tests?[/\\]',           # tests/ or test/ directories
    r'[/\\]test_\w+\.py$',         # test_*.py files
    r'[/\\]\w+_test\.py$',         # *_test.py files
    r'[/\\]\w+\.test\.[jt]sx?$',   # *.test.js/ts/tsx
    r'[/\\]\w+\.spec\.[jt]sx?$',   # *.spec.js/ts/tsx
    r'\.tests\.ps1$',              # *.tests.ps1 (Pester)
]


def check_test_file_edit(tool_input: dict) -> str | None:
    """Check if an Edit/Write targets a test file.

    Returns an additionalContext reminder string if the file matches
    a test pattern, else None. Does not block — only reminds.
    """
    file_path = tool_input.get('file_path', '')
    if not file_path:
        return None
    for pattern in TEST_FILE_PATTERNS:
        if re.search(pattern, file_path):
            return (
                "You are editing a test file. If this test is failing, fix the code "
                "under test rather than the test; a test rewritten to pass hides the "
                "defect it was guarding. If the test itself is wrong, proceed."
            )
    return None


def check_bash_anti_patterns(command: str) -> str | None:
    """Check a Bash command against known anti-patterns.

    Procedural checks run first (they cannot be expressed as a single
    regex), then the data-driven rule lists from ``pre_tool_rules``.
    Returns the reason string of the first rule that matches, or None.
    """
    from core.pre_tool_rules import (
        BASH_ANTI_PATTERN_RULES,
        RESTRICTED_NPM_RULES,
        evaluate_rules,
    )

    # Block heredocs containing #-prefixed lines — triggers Claude Code's
    # built-in "quoted newline followed by #-prefixed line" safety prompt.
    # Workaround: write content to a temp file with the Write tool, then
    # reference it with $(cat /tmp/file.txt).
    if _heredoc_has_hash_lines(command):
        return (
            "Heredoc contains #-prefixed lines which triggers Claude Code's safety prompt. "
            "Instead: 1) Use the Write tool to write the content to a temp file "
            "(e.g. /tmp/desc.txt), then 2) reference it in the command with "
            "$(cat /tmp/desc.txt)."
        )

    # Block long inline python -c scripts — triggers approval prompt and
    # option 2 ("don't ask again") saves the ENTIRE command as a permission
    # pattern, polluting settings.local.json. Write to a temp .py file instead.
    if _is_long_inline_python(command):
        return (
            "Long inline python -c script will trigger approval prompt and risks "
            "settings.local.json pollution if auto-approved. Instead: 1) Use the "
            "Write tool to write the script to a temp .py file, then 2) run it "
            "with: python /tmp/script.py"
        )

    # Strip heredoc content to avoid false positives on string payloads
    # (e.g. commit messages containing "type naming" or "C:\path\example").
    cmd_only = re.split(r"<<\s*['\"]?\w+['\"]?", command)[0]

    # Block Windows backslash paths — bash interprets \ as escape.
    # Runs on cmd_only to avoid false positives on backslash examples in
    # commit messages or issue descriptions (issue #81).
    if _has_windows_backslash_path(cmd_only):
        return (
            "Windows backslash path detected (e.g. C:\\dir\\file). Bash interprets "
            "backslashes as escapes, causing 'command not found'. Use forward slashes "
            "(C:/dir/file) or bare command names (python, not C:\\Python314\\python.exe)."
        )

    # NPM AppLocker rules first — domain-distinct from the anti-pattern set.
    npm_match = evaluate_rules(RESTRICTED_NPM_RULES, cmd_only)
    if npm_match is not None:
        return npm_match[1]

    bash_match = evaluate_rules(BASH_ANTI_PATTERN_RULES, cmd_only)
    if bash_match is not None:
        return bash_match[1]

    return None


def _allow_response() -> dict:
    """Default 'allow' shape — bypasses Claude Code's built-in checks for $()."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def _handle(input_data, timer):
    tool_name = input_data.get('tool_name', input_data.get('tool', 'unknown'))
    tool_input = input_data.get('tool_input', {})
    timer.set_input_summary(f"tool={tool_name}")

    if tool_name in ("Bash", "PowerShell"):
        # Duration guard applies to both shells (unbounded foreground parks the
        # turn regardless of which shell ran it).
        duration_reason = check_duration_guard(tool_input)
        if duration_reason:
            timer.set_output_summary(f"blocked (duration): {duration_reason[:50]}")
            return {"decision": "block", "reason": duration_reason}

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        reason = check_bash_anti_patterns(command)
        if reason:
            timer.set_output_summary(f"blocked: {reason[:60]}")
            return {"decision": "block", "reason": reason}

    if tool_name in ('Edit', 'Write'):
        test_reminder = check_test_file_edit(tool_input)
        if test_reminder:
            timer.set_output_summary(
                f"test file reminder: {tool_input.get('file_path', '')[:40]}"
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": test_reminder,
                }
            }

    timer.set_output_summary("allowed")
    return _allow_response()


def main():
    from core.hook_runtime import run_hook
    # Fallback {} defers to Claude's default permission checks (issue #122).
    run_hook("PreToolUse", _handle, error_name="pre_tool_use", fallback={})


if __name__ == '__main__':
    main()

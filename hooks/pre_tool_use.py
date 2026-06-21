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
# explicit bounded timeout, and caps any explicit foreground timeout at 5 min.

# Foreground runtime cap (ms). Mirrors the Bash tool's own max (600000) but
# tighter — nothing should silently hold the turn longer than this.
_FOREGROUND_TIMEOUT_CAP_MS = 300000

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


def check_duration_guard(tool_input: dict) -> str | None:
    """Block unbounded foreground shell calls that risk parking the turn.

    Allowed: any backgrounded call (``run_in_background: true``); any call with
    an explicit ``timeout`` <= the 5-min cap. Blocked: a slow-pattern command
    run foreground without an explicit timeout, and any explicit foreground
    timeout above the cap. Returns the block reason, or None to allow.
    """
    if tool_input.get("run_in_background") is True:
        return None  # backgrounded — returns control immediately, can't park

    timeout = tool_input.get("timeout")
    has_explicit_timeout = isinstance(timeout, (int, float)) and timeout > 0

    if has_explicit_timeout and timeout > _FOREGROUND_TIMEOUT_CAP_MS:
        return (
            "Foreground shell calls are capped at 5 min (timeout <= 300000 ms). "
            "Lower the timeout, or set run_in_background: true and poll the "
            "output file for long-running work."
        )

    command = tool_input.get("command", "") or ""
    if _SLOW_FOREGROUND_RE.search(command) and not has_explicit_timeout:
        return (
            "This looks long-running (network install / cold-start tooling) and "
            "is foreground with no explicit timeout, so a dropped result would "
            "park the turn unbounded. Re-issue with run_in_background: true (then "
            "poll the output file), or add an explicit timeout <= 300000 ms if "
            "you're sure it's quick."
        )
    return None


# Test file patterns — matched against file_path for Edit/Write operations.
# If matched, a reminder is injected (not a block) referencing Non-Negotiable Rule #1.
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
                "REMINDER: You are editing a test file. If this test is failing, "
                "fix the code under test — never modify the test to make it pass "
                "(Non-Negotiable Rule #1). If you have a legitimate reason to "
                "change this test, proceed."
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

    # NPM execution-restriction rules first — domain-distinct from the anti-pattern set.
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

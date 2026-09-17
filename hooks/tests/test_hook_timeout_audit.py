"""Regression tests for Claude-level hook timeout observability."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from hook_timeout_audit import (  # noqa: E402
    scan_claude_hook_timeouts,
    summarize_hook_timeouts,
)


NOW = datetime(2026, 8, 5, 22, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    timestamp = NOW.timestamp()
    os.utime(path, (timestamp, timestamp))


def _timeout_entry(
    *,
    timestamp: str = "2026-08-05T21:04:09.311Z",
    command: str = '"C:/Users/test/.claude/hooks/hook_wrapper.cmd" user_prompt_submit',
    timed_out: bool = True,
) -> dict:
    return {
        "type": "attachment",
        "timestamp": timestamp,
        "cwd": "C:/repo",
        "sessionId": "session-1",
        "attachment": {
            "type": "hook_cancelled",
            "hookName": "UserPromptSubmit",
            "hookEvent": "UserPromptSubmit",
            "command": command,
            "durationMs": 11666,
            "timedOut": timed_out,
            "timeoutMs": 5000,
        },
    }


def test_scans_only_true_recent_hook_timeouts(tmp_path):
    transcript = tmp_path / "project" / "session.jsonl"
    _write_transcript(
        transcript,
        [
            {"type": "attachment", "timestamp": "2026-08-05T21:00:00Z"},
            _timeout_entry(timed_out=False),
            _timeout_entry(timestamp="2026-08-03T21:00:00Z"),
            _timeout_entry(),
        ],
    )

    records = scan_claude_hook_timeouts(tmp_path, hours=24, now=NOW)

    assert len(records) == 1
    assert records[0]["event"] == "UserPromptSubmit"
    assert records[0]["duration_ms"] == 11666
    assert records[0]["timeout_ms"] == 5000
    assert records[0]["line"] == 4


def test_explicit_since_ignores_timeouts_before_current_settings(tmp_path):
    transcript = tmp_path / "project" / "session.jsonl"
    _write_transcript(transcript, [_timeout_entry()])

    records = scan_claude_hook_timeouts(
        tmp_path,
        since=datetime(2026, 8, 5, 21, 30, tzinfo=timezone.utc),
        now=NOW,
    )

    assert records == []


def test_summary_keeps_each_responsible_command(tmp_path):
    transcript = tmp_path / "project" / "session.jsonl"
    plugin_command = 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/security_reminder_hook.py"'
    _write_transcript(
        transcript,
        [
            _timeout_entry(),
            _timeout_entry(command=plugin_command),
            _timeout_entry(command=plugin_command, timestamp="2026-08-05T21:05:00Z"),
        ],
    )

    summary = summarize_hook_timeouts(
        scan_claude_hook_timeouts(tmp_path, hours=24, now=NOW)
    )

    assert summary["total"] == 3
    assert len(summary["by_command"]) == 2
    assert summary["by_command"][0]["command"] == plugin_command
    assert summary["by_command"][0]["count"] == 2


def test_malformed_transcript_lines_are_ignored(tmp_path):
    transcript = tmp_path / "project" / "session.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("not json\n" + json.dumps(_timeout_entry()) + "\n", encoding="utf-8")
    os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))

    records = scan_claude_hook_timeouts(tmp_path, hours=24, now=NOW)

    assert len(records) == 1
    assert records[0]["line"] == 2


def test_large_transcript_scan_is_bounded_to_recent_tail(tmp_path):
    transcript = tmp_path / "project" / "large.jsonl"
    transcript.parent.mkdir(parents=True)
    old = json.dumps(_timeout_entry(timestamp="2026-08-05T21:01:00Z"))
    recent = json.dumps(_timeout_entry(timestamp="2026-08-05T21:59:00Z"))
    transcript.write_text(old + "\n" + ("{}\n" * 200) + recent + "\n", encoding="utf-8")
    os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))

    records = scan_claude_hook_timeouts(
        tmp_path,
        hours=24,
        now=NOW,
        max_bytes_per_file=1024,
    )

    assert len(records) == 1
    assert records[0]["timestamp"] == "2026-08-05T21:59:00Z"
    assert records[0]["line_origin"] == "bounded_tail"


def test_max_files_keeps_only_newest_candidates(tmp_path):
    older = tmp_path / "project" / "older.jsonl"
    newer = tmp_path / "project" / "newer.jsonl"
    _write_transcript(older, [_timeout_entry(timestamp="2026-08-05T21:01:00Z")])
    _write_transcript(newer, [_timeout_entry(timestamp="2026-08-05T21:59:00Z")])
    os.utime(older, (NOW.timestamp() - 60, NOW.timestamp() - 60))

    records = scan_claude_hook_timeouts(tmp_path, hours=24, now=NOW, max_files=1)

    assert len(records) == 1
    assert records[0]["timestamp"] == "2026-08-05T21:59:00Z"


def test_tracked_obi_hooks_use_direct_exec_instead_of_shell_wrapper():
    settings = json.loads(
        (REPO_ROOT / "users" / "user" / "settings.json").read_text(encoding="utf-8")
    )

    for event, groups in settings["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"] == "python", f"{event} reintroduced a shell launch"
                assert len(hook.get("args", [])) == 1, f"{event} has no exec-form script arg"
                assert hook["args"][0].endswith(".py"), f"{event} does not directly launch Python"
                assert "hook_wrapper" not in hook["args"][0]

    prompt_hook = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert prompt_hook["timeout"] == 10


def test_public_template_does_not_bind_external_plugins():
    settings = json.loads(
        (REPO_ROOT / "users" / "user" / "settings.json").read_text(encoding="utf-8")
    )
    assert not settings.get("enabledPlugins")


def test_tracked_windows_shell_config_avoids_wsl_bash_resolution():
    settings = json.loads(
        (REPO_ROOT / "users" / "user" / "settings.json").read_text(encoding="utf-8")
    )
    environment = settings["env"]

    assert environment["CLAUDE_CODE_GIT_BASH_PATH"] == r"C:\Program Files\Git\bin\bash.exe"
    assert environment["CLAUDE_CODE_USE_POWERSHELL_TOOL"] == "1"

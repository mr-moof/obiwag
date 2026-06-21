"""Tests for the unified hook error handler (issue #122)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.hook_error_handler import (
    log_hook_error,
    respond_with_error,
    _ensure_system_message,
    _get_error_log_path,
)


# ── log_hook_error ─────────────────────────────────────────


class TestLogHookError:
    def test_appends_jsonl_record(self, tmp_path):
        log_path = tmp_path / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise ValueError("boom")
            except ValueError as exc:
                log_hook_error("pre_tool_use", exc)

        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["hook"] == "pre_tool_use"
        assert record["error_class"] == "ValueError"
        assert record["message"] == "boom"
        assert "traceback" in record
        assert "timestamp" in record

    def test_multiple_errors_append(self, tmp_path):
        log_path = tmp_path / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            for i in range(3):
                try:
                    raise RuntimeError(f"err-{i}")
                except RuntimeError as exc:
                    log_hook_error("stop", exc)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_includes_context_when_provided(self, tmp_path):
        log_path = tmp_path / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise KeyError("tool_name")
            except KeyError as exc:
                log_hook_error("post_tool_use", exc, context={"tool": "Bash", "pid": 123})

        record = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert record["context"] == {"tool": "Bash", "pid": 123}

    def test_never_raises_when_path_unwritable(self, tmp_path):
        """Logger must not raise even if the target directory cannot be created."""
        bad_path = tmp_path / "nonexistent" / "nested" / "path.jsonl"
        # Make the parent a file so mkdir fails
        blocker = tmp_path / "nonexistent"
        blocker.write_text("blocker")
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(bad_path)):
            try:
                raise ValueError("ignore me")
            except ValueError as exc:
                # Must not raise
                log_hook_error("session_start", exc)

    def test_creates_directory_if_missing(self, tmp_path):
        log_path = tmp_path / "deep" / "nested" / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise ValueError("x")
            except ValueError as exc:
                log_hook_error("pre_tool_use", exc)
        assert log_path.exists()


# ── _ensure_system_message ─────────────────────────────────


class TestEnsureSystemMessage:
    def test_adds_to_empty_output(self):
        result = _ensure_system_message({}, "failure notice")
        assert result == {"systemMessage": "failure notice"}

    def test_appends_to_existing_system_message(self):
        result = _ensure_system_message({"systemMessage": "original"}, "additional")
        assert result["systemMessage"] == "original\nadditional"

    def test_preserves_other_keys(self):
        result = _ensure_system_message({"foo": "bar"}, "msg")
        assert result == {"foo": "bar", "systemMessage": "msg"}

    def test_non_dict_input_treated_as_empty(self):
        result = _ensure_system_message(None, "msg")
        assert result == {"systemMessage": "msg"}


# ── respond_with_error ─────────────────────────────────────


class TestRespondWithError:
    """Integration tests for the top-level helper.

    respond_with_error() calls sys.exit(0), so we use pytest.raises(SystemExit).
    """

    def test_prints_fallback_with_system_message(self, tmp_path, capsys):
        log_path = tmp_path / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise RuntimeError("crash!")
            except RuntimeError as exc:
                with pytest.raises(SystemExit) as exit_info:
                    respond_with_error("pre_tool_use", exc, fallback={})
                assert exit_info.value.code == 0

        captured = capsys.readouterr().out
        parsed = json.loads(captured)
        assert "systemMessage" in parsed
        assert "pre_tool_use" in parsed["systemMessage"]
        assert "RuntimeError" in parsed["systemMessage"]
        assert "crash!" in parsed["systemMessage"]

    def test_logs_to_jsonl(self, tmp_path, capsys):
        log_path = tmp_path / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise ValueError("bad input")
            except ValueError as exc:
                with pytest.raises(SystemExit):
                    respond_with_error("post_tool_use", exc, fallback={})

        assert log_path.exists()
        record = json.loads(log_path.read_text().strip())
        assert record["hook"] == "post_tool_use"

    def test_preserves_existing_fallback_keys(self, tmp_path, capsys):
        log_path = tmp_path / "hook-errors.jsonl"
        fallback = {"permissionDecision": "allow"}
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise Exception("x")
            except Exception as exc:
                with pytest.raises(SystemExit):
                    respond_with_error("pre_tool_use", exc, fallback=fallback)

        parsed = json.loads(capsys.readouterr().out)
        assert parsed["permissionDecision"] == "allow"
        assert "systemMessage" in parsed

    def test_injects_into_nested_hookSpecificOutput(self, tmp_path, capsys):
        """session_start.py uses nested hookSpecificOutput.additionalContext."""
        log_path = tmp_path / "hook-errors.jsonl"
        fallback = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "[CODING_SESSION_START] Obi Wag is installed.",
            }
        }
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise RuntimeError("memory read failed")
            except RuntimeError as exc:
                with pytest.raises(SystemExit):
                    respond_with_error("session_start", exc, fallback=fallback)

        parsed = json.loads(capsys.readouterr().out)
        ctx = parsed["hookSpecificOutput"]["additionalContext"]
        assert "Obi Wag is installed" in ctx  # original preserved
        assert "memory read failed" in ctx    # error appended
        # systemMessage should NOT be set at top level — we used nested path
        assert "systemMessage" not in parsed

    def test_silent_mode_skips_system_message(self, tmp_path, capsys):
        log_path = tmp_path / "hook-errors.jsonl"
        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise Exception("x")
            except Exception as exc:
                with pytest.raises(SystemExit):
                    respond_with_error("post_tool_use", exc, fallback={}, silent=True)

        parsed = json.loads(capsys.readouterr().out)
        assert parsed == {}
        # But still logged to jsonl
        assert log_path.exists()

    def test_unserializable_fallback_falls_back_to_empty(self, tmp_path, capsys):
        log_path = tmp_path / "hook-errors.jsonl"

        class NotSerializable:
            pass

        with patch("core.hook_error_handler._get_error_log_path", return_value=str(log_path)):
            try:
                raise Exception("x")
            except Exception as exc:
                with pytest.raises(SystemExit):
                    # Pass unserializable value to force json.dumps failure
                    respond_with_error(
                        "stop", exc, fallback={"bad": NotSerializable()}, silent=True
                    )

        captured = capsys.readouterr().out.strip()
        # Must still emit valid JSON
        assert json.loads(captured) == {}


class TestErrorLogPath:
    def test_path_includes_claude_obi(self):
        path = _get_error_log_path()
        assert ".claude" in path
        assert ".obi" in path
        assert "hook-errors.jsonl" in path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

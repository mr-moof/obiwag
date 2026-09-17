"""Tests for hooks/core/hook_runtime.py — the shared hook scaffolding."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core import hook_runtime  # noqa: E402


# ---------------------------------------------------------------------------
# read_hook_input
# ---------------------------------------------------------------------------

class TestReadHookInput:
    def test_valid_json(self):
        stream = io.StringIO('{"a": 1, "b": "hi"}')
        assert hook_runtime.read_hook_input(stream) == {"a": 1, "b": "hi"}

    def test_empty_stream_returns_empty_dict(self):
        assert hook_runtime.read_hook_input(io.StringIO("")) == {}

    def test_invalid_json_returns_empty_dict(self):
        assert hook_runtime.read_hook_input(io.StringIO("not json {")) == {}

    def test_json_null_returns_empty_dict(self):
        # json.load returning None should normalize to {} so callers can
        # safely use .get() without None-checks.
        assert hook_runtime.read_hook_input(io.StringIO("null")) == {}

    def test_non_dict_payload_does_not_raise(self):
        # A non-dict top-level payload (e.g. a JSON list) is still parsed,
        # but downstream .get() calls would fail. The function does NOT
        # type-coerce — it returns the parsed value as-is for dicts and {}
        # for the other failure modes covered above. Document by example:
        result = hook_runtime.read_hook_input(io.StringIO('[1, 2, 3]'))
        # Lists are not dicts; behavior is permissive (returns the list).
        # Hooks that pass through run_hook will then crash inside their
        # handler when calling .get() — caught by the wrapper's except.
        assert result == [1, 2, 3]

    def test_default_uses_sys_stdin(self):
        with patch.object(sys, "stdin", io.StringIO('{"x": 1}')):
            assert hook_runtime.read_hook_input() == {"x": 1}

    def test_oversized_input_fails_fast_and_explicitly(self, monkeypatch):
        monkeypatch.setattr(hook_runtime, 'MAX_HOOK_INPUT_CHARS', 32)
        with pytest.raises(hook_runtime.HookInputTooLarge, match='exceeds'):
            hook_runtime.read_hook_input(io.StringIO('x' * 33))


# ---------------------------------------------------------------------------
# emit_hook_output
# ---------------------------------------------------------------------------

class TestEmitHookOutput:
    def test_writes_compact_json_line(self, capsys):
        hook_runtime.emit_hook_output({"a": 1})
        captured = capsys.readouterr()
        assert captured.out.strip() == '{"a": 1}'

    def test_none_normalized_to_empty_object(self, capsys):
        hook_runtime.emit_hook_output(None)
        captured = capsys.readouterr()
        assert captured.out.strip() == '{}'

    def test_preserves_nested_structure(self, capsys):
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }
        hook_runtime.emit_hook_output(payload)
        captured = capsys.readouterr()
        # Round-trip parse to assert structure rather than string equality
        # (key order is implementation-dependent in some Python versions).
        assert json.loads(captured.out) == payload


# ---------------------------------------------------------------------------
# run_hook orchestration
# ---------------------------------------------------------------------------

class TestRunHook:
    def test_handler_output_is_emitted(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"x": 1}'))

        def handler(input_data, timer):
            assert input_data == {"x": 1}
            timer.set_output_summary("ok")
            return {"systemMessage": "hello"}

        with pytest.raises(SystemExit) as exc_info:
            hook_runtime.run_hook(
                "TestHook", handler, error_name="testhook", fallback={},
            )
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"systemMessage": "hello"}

    def test_handler_returning_none_emits_empty_object(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

        def handler(input_data, timer):
            return None

        with pytest.raises(SystemExit):
            hook_runtime.run_hook(
                "TestHook", handler, error_name="testhook", fallback={},
            )
        captured = capsys.readouterr()
        assert captured.out.strip() == "{}"

    def test_handler_exception_routes_through_respond_with_error(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

        def handler(input_data, timer):
            raise RuntimeError("boom")

        # respond_with_error logs, prints a fallback with systemMessage, and
        # exits 0. Verify the fallback shape we passed comes back through.
        with pytest.raises(SystemExit) as exc_info:
            hook_runtime.run_hook(
                "TestHook",
                handler,
                error_name="testhook",
                fallback={"systemMessage": "original"},
            )
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        # systemMessage should now contain BOTH the original message AND
        # the appended error message from respond_with_error.
        assert "original" in out["systemMessage"]
        assert "RuntimeError" in out["systemMessage"]
        assert "boom" in out["systemMessage"]

    def test_handler_exception_with_session_start_fallback(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

        def handler(input_data, timer):
            raise RuntimeError("oops")

        fallback = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "fallback context",
            }
        }
        with pytest.raises(SystemExit):
            hook_runtime.run_hook(
                "SessionStart", handler, error_name="session_start", fallback=fallback,
            )
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        # Error handler appends to additionalContext, not systemMessage,
        # for the session_start nested shape.
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "fallback context" in ctx
        assert "RuntimeError" in ctx

    def test_malformed_stdin_does_not_abort(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("garbage {{"))

        seen = {}

        def handler(input_data, timer):
            seen['data'] = input_data
            return {"ok": True}

        with pytest.raises(SystemExit):
            hook_runtime.run_hook(
                "TestHook", handler, error_name="testhook", fallback={},
            )
        # Handler ran with empty dict (not None, not the unparsed string).
        assert seen['data'] == {}

    def test_array_payload_handler_crash_routes_to_fallback(self, capsys, monkeypatch):
        # read_hook_input passes JSON arrays through unchanged. A handler
        # that calls .get() on a list will crash with AttributeError; the
        # wrapper must catch it and emit the fallback rather than letting
        # the hook abort visibly.
        monkeypatch.setattr(sys, "stdin", io.StringIO("[1, 2, 3]"))

        def handler(input_data, timer):
            # Mirrors what every real handler does as its first move.
            return {"name": input_data.get("tool_name", "unknown")}

        with pytest.raises(SystemExit) as exc_info:
            hook_runtime.run_hook(
                "TestHook",
                handler,
                error_name="testhook",
                fallback={"systemMessage": "fallback"},
            )
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert "fallback" in out["systemMessage"]
        assert "AttributeError" in out["systemMessage"]

    def test_timer_records_error_on_handler_exception(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

        recorded_status = {}

        class FakeTimer:
            def __init__(self, name):
                self.hook_name = name
                self.status = 'success'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                recorded_status['final'] = self.status
                return False

            def set_input_summary(self, *_):
                pass

            def set_output_summary(self, *_):
                pass

            def set_error(self, msg):
                self.status = 'error'

        with patch("core.hook_runtime.HookTimer", FakeTimer):
            with pytest.raises(SystemExit):
                hook_runtime.run_hook(
                    "TestHook",
                    lambda i, t: (_ for _ in ()).throw(RuntimeError("boom")),
                    error_name="testhook",
                    fallback={},
                )
        assert recorded_status['final'] == 'error'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

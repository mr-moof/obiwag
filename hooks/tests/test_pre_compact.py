"""Tests for pre_compact._reset_compact_counter (WI-3 counter reset)."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from pre_compact import _reset_compact_counter  # noqa: E402

# Shared SessionState stub lives in tests/_helpers.py (#151).
sys.path.insert(0, str(Path(__file__).parent))
from _helpers import StubSessionState as _StubSessionState  # noqa: E402


class TestResetCompactCounter:
    """Returns the value written, or None when nothing was persisted.

    It used to return a bare True even when the write failed, so the hook could
    report a reset that never landed (synthetic fixtures).
    """

    def test_copies_tool_count_into_last_compacted(self):
        state = _StubSessionState({'tool_count': 42})
        assert _reset_compact_counter(state) == 42
        assert state.get('last_compacted_at_count') == 42

    def test_overwrites_previous_checkpoint(self):
        state = _StubSessionState({'tool_count': 100, 'last_compacted_at_count': 20})
        assert _reset_compact_counter(state) == 100
        assert state.get('last_compacted_at_count') == 100

    def test_handles_zero_tool_count(self):
        # Fresh session, hasn't used any tools yet — still a valid reset.
        state = _StubSessionState({'tool_count': 0})
        assert _reset_compact_counter(state) == 0
        assert state.get('last_compacted_at_count') == 0

    def test_missing_tool_count_treated_as_zero(self):
        state = _StubSessionState({})
        assert _reset_compact_counter(state) == 0
        assert state.get('last_compacted_at_count') == 0

    def test_none_session_state_returns_none(self):
        # Brand-new session where state isn't on disk yet — silent no-op.
        assert _reset_compact_counter(None) is None

    def test_unavailable_state_reports_no_reset(self):
        """A busy lock must not be reported as a successful reset."""
        from core.session_state import StateUnavailable

        class _Busy:
            def transaction(self):
                raise StateUnavailable('locked')

        assert _reset_compact_counter(_Busy()) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

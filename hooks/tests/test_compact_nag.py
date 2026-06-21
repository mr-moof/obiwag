"""Tests for post_tool_use.check_compact_nag (WI-3)."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from post_tool_use import check_compact_nag  # noqa: E402

# Shared SessionState stub lives in tests/_helpers.py (#151).
sys.path.insert(0, str(Path(__file__).parent))
from _helpers import StubSessionState as _StubSessionState  # noqa: E402


def _patch_interval(value):
    return patch('core.calibration.load_calibration', return_value={
        'intervals': {'compact_nag': value},
    })


class TestCheckCompactNag:
    def test_fires_at_threshold(self):
        state = _StubSessionState({'tool_count': 20, 'last_compacted_at_count': 0})
        with _patch_interval(20):
            alerts = check_compact_nag(state)
        assert len(alerts) == 1
        assert alerts[0].startswith('[Context]')
        assert '20 tool calls' in alerts[0]
        assert '/compact' in alerts[0]

    def test_fires_above_threshold(self):
        state = _StubSessionState({'tool_count': 55, 'last_compacted_at_count': 10})
        with _patch_interval(20):
            alerts = check_compact_nag(state)
        assert len(alerts) == 1
        assert '45 tool calls' in alerts[0]

    def test_silent_below_threshold(self):
        state = _StubSessionState({'tool_count': 19, 'last_compacted_at_count': 0})
        with _patch_interval(20):
            alerts = check_compact_nag(state)
        assert alerts == []

    def test_silent_just_after_compact(self):
        # tool_count == last_compacted_at_count → delta 0 → no nag
        state = _StubSessionState({'tool_count': 42, 'last_compacted_at_count': 42})
        with _patch_interval(20):
            alerts = check_compact_nag(state)
        assert alerts == []

    def test_fresh_session_no_compact_yet(self):
        # last_compacted_at_count missing → treated as 0
        state = _StubSessionState({'tool_count': 25})
        with _patch_interval(20):
            alerts = check_compact_nag(state)
        assert len(alerts) == 1
        assert '25 tool calls' in alerts[0]

    def test_default_interval_when_calibration_missing(self):
        # No intervals block at all — helper falls back to default 20
        state = _StubSessionState({'tool_count': 20})
        with patch('core.calibration.load_calibration', return_value={}):
            alerts = check_compact_nag(state)
        assert len(alerts) == 1

    def test_interval_zero_disables_nag(self):
        # Setting the interval to 0 disables the nag entirely
        state = _StubSessionState({'tool_count': 1000, 'last_compacted_at_count': 0})
        with _patch_interval(0):
            alerts = check_compact_nag(state)
        assert alerts == []

    def test_none_session_state_returns_empty(self):
        # First tool in a brand-new session where state isn't initialized yet
        alerts = check_compact_nag(None)
        assert alerts == []

    def test_tunable_interval(self):
        state = _StubSessionState({'tool_count': 10, 'last_compacted_at_count': 0})
        with _patch_interval(10):
            alerts = check_compact_nag(state)
        assert len(alerts) == 1
        assert '10 tool calls' in alerts[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

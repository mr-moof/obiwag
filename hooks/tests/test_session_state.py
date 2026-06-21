"""Unit tests for session_state module."""

import os
import sys
from pathlib import Path


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.session_state import SessionState


class TestSessionState:
    """Tests for SessionState class."""

    def test_init_creates_state_dir(self, tmp_path, monkeypatch):
        """Test initialization creates the state directory."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path / 'sessions')
        )
        state = SessionState('test-session-123')
        assert os.path.isdir(str(tmp_path / 'sessions'))

    def test_get_default_value(self, tmp_path, monkeypatch):
        """Test get returns default when key doesn't exist."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state = SessionState('test-session')
        assert state.get('nonexistent') is None
        assert state.get('nonexistent', 42) == 42

    def test_set_and_get(self, tmp_path, monkeypatch):
        """Test set stores value and get retrieves it."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state = SessionState('test-session')
        state.set('my_key', 'my_value')
        assert state.get('my_key') == 'my_value'

    def test_increment(self, tmp_path, monkeypatch):
        """Test increment creates and increases counter."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state = SessionState('test-session')
        assert state.increment('counter') == 1
        assert state.increment('counter') == 2
        assert state.increment('counter', 5) == 7

    def test_append_to_list(self, tmp_path, monkeypatch):
        """Test append_to_list creates and appends to list."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state = SessionState('test-session')
        state.append_to_list('items', 'a')
        state.append_to_list('items', 'b')
        assert state.get('items') == ['a', 'b']

    def test_append_to_list_handles_non_list(self, tmp_path, monkeypatch):
        """Test append_to_list resets if value is not a list."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state = SessionState('test-session')
        state.set('items', 'not-a-list')
        state.append_to_list('items', 'a')
        assert state.get('items') == ['a']

    def test_get_all(self, tmp_path, monkeypatch):
        """Test get_all returns complete state."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state = SessionState('test-session')
        state.set('key1', 'val1')
        all_state = state.get_all()
        assert all_state['key1'] == 'val1'
        assert all_state['session_id'] == 'test-session'

    def test_corrupted_file_returns_default(self, tmp_path, monkeypatch):
        """Test corrupted state file falls back to defaults."""
        monkeypatch.setattr(
            'core.session_state.get_active_sessions_path',
            lambda: str(tmp_path)
        )
        state_file = tmp_path / 'test-session.json'
        state_file.write_text('not valid json {{{')

        state = SessionState('test-session')
        result = state.get_all()
        assert result['session_id'] == 'test-session'
        assert result['tool_count'] == 0

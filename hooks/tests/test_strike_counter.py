"""Unit tests for strike_counter module."""

import json
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.strike_counter import StrikeCounter, check_strike_status


class TestStrikeCounter:
    """Tests for StrikeCounter class."""

    def test_init_creates_empty_state(self, tmp_path):
        """Test initialization with no existing state file."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        assert counter.state['strikes'] == []
        assert counter.state['current_issue'] is None
        assert counter.state['checkpoint_triggered'] is False

    def test_init_loads_existing_state(self, tmp_path):
        """Test initialization loads existing state from file."""
        state_file = tmp_path / 'strike-state.json'
        existing_state = {
            'strikes': [{'issue_signature': 'test:error', 'attempt': 'first try'}],
            'current_issue': 'test:error',
            'checkpoint_triggered': False
        }
        state_file.write_text(json.dumps(existing_state))

        counter = StrikeCounter(str(state_file))

        assert len(counter.state['strikes']) == 1
        assert counter.state['current_issue'] == 'test:error'

    def test_init_handles_corrupted_state_file(self, tmp_path):
        """Test initialization handles corrupted JSON gracefully."""
        state_file = tmp_path / 'strike-state.json'
        state_file.write_text('not valid json {{{')

        counter = StrikeCounter(str(state_file))

        # Should reset to empty state
        assert counter.state['strikes'] == []
        assert counter.state['current_issue'] is None

    def test_record_failure_first_strike(self, tmp_path):
        """Test recording first failure for an issue."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        result = counter.record_failure('linting:error', {
            'error_message': 'File not found',
            'attempt_description': 'Tried to find config'
        })

        assert result['strike_count'] == 1
        assert result['should_checkpoint'] is False
        assert result['checkpoint_message'] is None
        assert len(result['previous_attempts']) == 1

    def test_record_failure_second_strike_triggers_checkpoint(self, tmp_path):
        """Test that second strike triggers checkpoint."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        # First strike
        counter.record_failure('linting:error', {
            'error_message': 'Error 1',
            'attempt_description': 'First attempt'
        })

        # Second strike
        result = counter.record_failure('linting:error', {
            'error_message': 'Error 2',
            'attempt_description': 'Second attempt'
        })

        assert result['strike_count'] == 2
        assert result['should_checkpoint'] is True
        assert result['checkpoint_message'] is not None
        assert 'Strike #2 Checkpoint' in result['checkpoint_message']

    def test_record_failure_third_strike_no_checkpoint(self, tmp_path):
        """Test that third strike does not trigger checkpoint again."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        # Three strikes same issue
        counter.record_failure('linting:error', {'error_message': 'Error 1', 'attempt_description': 'Try 1'})
        counter.record_failure('linting:error', {'error_message': 'Error 2', 'attempt_description': 'Try 2'})
        result = counter.record_failure('linting:error', {'error_message': 'Error 3', 'attempt_description': 'Try 3'})

        assert result['strike_count'] == 3
        assert result['should_checkpoint'] is False  # Already triggered on strike 2

    def test_record_failure_new_issue_resets_counter(self, tmp_path):
        """Test that a different issue resets the strike counter."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        # Strike for issue A
        counter.record_failure('issue:A', {'error_message': 'A error', 'attempt_description': 'A attempt'})
        counter.record_failure('issue:A', {'error_message': 'A error 2', 'attempt_description': 'A attempt 2'})

        # Strike for issue B - should reset
        result = counter.record_failure('issue:B', {
            'error_message': 'B error',
            'attempt_description': 'B attempt'
        })

        assert result['strike_count'] == 1
        assert counter.state['current_issue'] == 'issue:B'

    def test_record_success_resets_state(self, tmp_path):
        """Test that recording success resets all state."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        # Add some strikes
        counter.record_failure('test:error', {'error_message': 'Error', 'attempt_description': 'Try'})
        counter.record_failure('test:error', {'error_message': 'Error 2', 'attempt_description': 'Try 2'})

        # Record success
        counter.record_success()

        assert counter.state['strikes'] == []
        assert counter.state['current_issue'] is None
        assert counter.state['checkpoint_triggered'] is False

    def test_reset_counter_with_new_approach(self, tmp_path):
        """Test reset with new approach keeps history but clears current issue."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        counter.record_failure('test:error', {'error_message': 'Error', 'attempt_description': 'Try 1'})
        counter.reset_counter(new_approach='Trying different strategy')

        assert counter.state['current_issue'] is None
        assert counter.state['checkpoint_triggered'] is False
        # History should contain resolution note
        assert any('New approach' in str(s) for s in counter.state['strikes'])

    def test_get_current_status(self, tmp_path):
        """Test getting current status."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        counter.record_failure('test:error', {'error_message': 'Error', 'attempt_description': 'Try'})

        status = counter.get_current_status()

        assert status['strike_count'] == 1
        assert status['current_issue'] == 'test:error'
        assert status['checkpoint_triggered'] is False
        assert len(status['recent_strikes']) == 1

    def test_state_persists_to_disk(self, tmp_path):
        """Test that state is saved to disk after modifications."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        counter.record_failure('test:error', {
            'error_message': 'Error',
            'attempt_description': 'Try'
        })

        # Verify file was created and contains state
        assert state_file.exists()
        saved_state = json.loads(state_file.read_text())
        assert len(saved_state['strikes']) == 1

    def test_checkpoint_message_format(self, tmp_path):
        """Test that checkpoint message contains expected elements."""
        state_file = tmp_path / 'strike-state.json'
        counter = StrikeCounter(str(state_file))

        counter.record_failure('linting:config_not_found', {
            'error_message': 'Config file not found at expected location',
            'attempt_description': 'Looked in root directory'
        })

        result = counter.record_failure('linting:config_not_found', {
            'error_message': 'Config still not found',
            'attempt_description': 'Looked in parent directory'
        })

        msg = result['checkpoint_message']
        assert 'linting:config_not_found' in msg
        assert 'Attempt 1' in msg
        assert 'Attempt 2' in msg
        assert 'Options:' in msg

    def test_default_state_file_location(self):
        """Test default state file is in .obi directory."""
        counter = StrikeCounter()
        assert '.obi' in str(counter.state_file)
        assert 'strike-state.json' in str(counter.state_file)


class TestCheckStrikeStatusFunction:
    """Tests for the convenience function check_strike_status."""

    def test_check_strike_status_returns_analysis(self, tmp_path):
        """Test the convenience function returns proper analysis."""
        state_file = tmp_path / 'strike-state.json'

        result = check_strike_status(
            'test:error',
            {'error_message': 'Test error', 'attempt_description': 'Test attempt'},
            str(state_file)
        )

        assert 'strike_count' in result
        assert 'should_checkpoint' in result
        assert 'previous_attempts' in result

    def test_check_strike_status_increments_counter(self, tmp_path):
        """Test that repeated calls increment the counter."""
        state_file = tmp_path / 'strike-state.json'

        result1 = check_strike_status('test:error', {'error_message': 'E1', 'attempt_description': 'A1'}, str(state_file))
        result2 = check_strike_status('test:error', {'error_message': 'E2', 'attempt_description': 'A2'}, str(state_file))

        assert result1['strike_count'] == 1
        assert result2['strike_count'] == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

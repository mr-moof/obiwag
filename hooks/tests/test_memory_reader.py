"""Unit tests for memory_reader module."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.memory_reader import read_claude_history


class TestReadClaudeHistory:
    """Tests for read_claude_history function."""

    def test_empty_file(self, tmp_path):
        """Test reading from empty history file."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text('')

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert result == []

    def test_missing_file(self, tmp_path):
        """Test reading from non-existent history file."""
        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert result == []

    def test_malformed_json_lines(self, tmp_path):
        """Test handling of malformed JSON lines."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            'not valid json\n'
            '{"display":"valid entry","sessionId":"123","timestamp":1000000,"project":"test"}\n'
            '{incomplete json\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1
            assert result[0]['topic'] == 'valid entry'

    def test_missing_session_id(self, tmp_path):
        """Test filtering out entries without sessionId."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"no session id","timestamp":1000000,"project":"test"}\n'
            '{"display":"has session id","sessionId":"123","timestamp":2000000,"project":"test"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1
            assert result[0]['sessionId'] == '123'

    def test_project_filtering(self, tmp_path):
        """Test filtering by project path."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"project A entry","sessionId":"1","timestamp":1000000,"project":"C:\\\\projectA"}\n'
            '{"display":"project B entry","sessionId":"2","timestamp":2000000,"project":"C:\\\\projectB"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history(project_filter='C:\\projectB')
            assert len(result) == 1
            assert result[0]['project'] == 'C:\\projectB'

    def test_timestamp_conversion(self, tmp_path):
        """Test that timestamps are preserved correctly."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        timestamp = 1640000000000  # Unix timestamp in milliseconds
        history_file.write_text(
            f'{{"display":"test entry","sessionId":"123","timestamp":{timestamp},"project":"test"}}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1
            assert result[0]['timestamp'] == timestamp

    def test_long_prompt_truncation(self, tmp_path):
        """Test that long prompts are truncated to 100 chars."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        long_text = 'a' * 150
        history_file.write_text(
            f'{{"display":"{long_text}","sessionId":"123","timestamp":1000000,"project":"test"}}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1
            assert len(result[0]['topic']) == 103  # 100 chars + '...'
            assert result[0]['topic'].endswith('...')

    def test_command_filtering(self, tmp_path):
        """Test filtering out slash commands and short entries."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"/obi-auto","sessionId":"1","timestamp":1000000,"project":"test"}\n'
            '{"display":"short","sessionId":"2","timestamp":2000000,"project":"test"}\n'
            '{"display":"this is a valid entry","sessionId":"3","timestamp":3000000,"project":"test"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1
            assert result[0]['sessionId'] == '3'
            assert result[0]['topic'] == 'this is a valid entry'

    def test_session_deduplication(self, tmp_path):
        """Test that only first entry per session is kept."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"first entry in session","sessionId":"123","timestamp":1000000,"project":"test"}\n'
            '{"display":"second entry in session","sessionId":"123","timestamp":1500000,"project":"test"}\n'
            '{"display":"third entry in session","sessionId":"123","timestamp":2000000,"project":"test"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1
            assert result[0]['topic'] == 'first entry in session'

    def test_most_recent_first(self, tmp_path):
        """Test that results are sorted by timestamp descending."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"oldest entry","sessionId":"1","timestamp":1000000,"project":"test"}\n'
            '{"display":"middle entry","sessionId":"2","timestamp":2000000,"project":"test"}\n'
            '{"display":"newest entry","sessionId":"3","timestamp":3000000,"project":"test"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 3
            assert result[0]['topic'] == 'newest entry'
            assert result[1]['topic'] == 'middle entry'
            assert result[2]['topic'] == 'oldest entry'

    def test_limit_parameter(self, tmp_path):
        """Test that limit parameter restricts number of results."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        entries = []
        for i in range(10):
            # Use longer display text to avoid <10 char filter
            entries.append(f'{{"display":"test entry number {i}","sessionId":"{i}","timestamp":{i * 1000000},"project":"test"}}')
        history_file.write_text('\n'.join(entries) + '\n')

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history(limit=3)
            assert len(result) == 3
            # Should get the 3 most recent (highest timestamps)
            assert result[0]['sessionId'] == '9'
            assert result[1]['sessionId'] == '8'
            assert result[2]['sessionId'] == '7'

    def test_file_permission_error(self, tmp_path):
        """Test graceful handling of file permission errors."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text('{"display":"test","sessionId":"1","timestamp":1000000,"project":"test"}\n')

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            with patch('builtins.open', side_effect=PermissionError('Access denied')):
                result = read_claude_history()
                assert result == []

    def test_performance_with_large_file(self, tmp_path):
        """Test that deque optimization handles large files efficiently."""
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)

        # Create a file with 200 lines (more than the 100-line deque limit)
        # Use longer display text to avoid <10 char filter
        entries = []
        for i in range(200):
            entries.append(f'{{"display":"test entry number {i}","sessionId":"{i}","timestamp":{i * 1000000},"project":"test"}}')
        history_file.write_text('\n'.join(entries) + '\n')

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history(limit=5)
            # Should get the 5 most recent sessions from the last 100 lines
            assert len(result) == 5
            # The most recent entry should be from the end of the file
            assert result[0]['sessionId'] == '199'
            assert result[0]['topic'] == 'test entry number 199'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

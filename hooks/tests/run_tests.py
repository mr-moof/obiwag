"""Simple test runner that doesn't require pytest."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import tempfile

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.memory_reader import read_claude_history


def run_ruff_check() -> bool:
    """Run ruff F401/F811 check on the hooks/ tree. Returns True on clean."""
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'ruff', 'check',
             '--select', 'F401,F811', str(HOOKS_DIR)],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("SKIP ruff_check: ruff not installed (pip install ruff)")
        return True
    if result.returncode == 0:
        print("PASS ruff_check (no unused imports / redefinitions)")
        return True
    print("FAIL ruff_check: unused imports or redefinitions found")
    print(result.stdout)
    print(result.stderr)
    return False


def test_empty_file():
    """Test reading from empty history file."""
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text('')

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert result == [], f"Expected empty list, got {result}"
    print("PASS test_empty_file")


def test_missing_file():
    """Test reading from non-existent history file."""
    with tempfile.TemporaryDirectory() as tmp_path:
        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert result == [], f"Expected empty list, got {result}"
    print("PASS test_missing_file")


def test_malformed_json_lines():
    """Test handling of malformed JSON lines."""
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            'not valid json\n'
            '{"display":"valid entry","sessionId":"123","timestamp":1000000,"project":"test"}\n'
            '{incomplete json\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1, f"Expected 1 result, got {len(result)}"
            assert result[0]['topic'] == 'valid entry', f"Expected 'valid entry', got {result[0]['topic']}"
    print("PASS test_malformed_json_lines")


def test_command_filtering():
    """Test filtering out slash commands and short entries."""
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"/obi-auto","sessionId":"1","timestamp":1000000,"project":"test"}\n'
            '{"display":"short","sessionId":"2","timestamp":2000000,"project":"test"}\n'
            '{"display":"this is a valid entry","sessionId":"3","timestamp":3000000,"project":"test"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1, f"Expected 1 result, got {len(result)}"
            assert result[0]['sessionId'] == '3', f"Expected sessionId '3', got {result[0]['sessionId']}"
    print("PASS test_command_filtering")


def test_long_prompt_truncation():
    """Test that long prompts are truncated to 100 chars."""
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        long_text = 'a' * 150
        history_file.write_text(
            f'{{"display":"{long_text}","sessionId":"123","timestamp":1000000,"project":"test"}}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 1, f"Expected 1 result, got {len(result)}"
            assert len(result[0]['topic']) == 103, f"Expected 103 chars, got {len(result[0]['topic'])}"
            assert result[0]['topic'].endswith('...'), f"Expected to end with '...', got {result[0]['topic'][-3:]}"
    print("PASS test_long_prompt_truncation")


def test_most_recent_first():
    """Test that results are sorted by timestamp descending."""
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
        history_file = tmp_path / '.claude' / 'history.jsonl'
        history_file.parent.mkdir(parents=True)
        history_file.write_text(
            '{"display":"oldest entry","sessionId":"1","timestamp":1000000,"project":"test"}\n'
            '{"display":"middle entry","sessionId":"2","timestamp":2000000,"project":"test"}\n'
            '{"display":"newest entry","sessionId":"3","timestamp":3000000,"project":"test"}\n'
        )

        with patch('os.path.expanduser', return_value=str(tmp_path)):
            result = read_claude_history()
            assert len(result) == 3, f"Expected 3 results, got {len(result)}"
            assert result[0]['topic'] == 'newest entry', f"Expected 'newest entry' first, got {result[0]['topic']}"
            assert result[2]['topic'] == 'oldest entry', f"Expected 'oldest entry' last, got {result[2]['topic']}"
    print("PASS test_most_recent_first")


def test_performance_with_large_file():
    """Test that deque optimization handles large files efficiently."""
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
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
            assert len(result) == 5, f"Expected 5 results, got {len(result)}"
            # The most recent entry should be from the end of the file
            assert result[0]['sessionId'] == '199', f"Expected sessionId '199', got {result[0]['sessionId']}"
    print("PASS test_performance_with_large_file")


def main():
    """Run all tests."""
    tests = [
        test_empty_file,
        test_missing_file,
        test_malformed_json_lines,
        test_command_filtering,
        test_long_prompt_truncation,
        test_most_recent_first,
        test_performance_with_large_file,
    ]

    print(f"\nRunning {len(tests)} tests...\n")

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"FAIL {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print(f"{'='*60}\n")

    ruff_ok = run_ruff_check()

    return 0 if failed == 0 and ruff_ok else 1


if __name__ == '__main__':
    sys.exit(main())

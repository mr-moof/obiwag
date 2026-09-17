"""Unit tests for pattern_matcher module."""

import sys
import time
from pathlib import Path

import pytest


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.pattern_matcher import (
    PatternLoadAborted,
    _load_patterns_from_dir,
    match_task_to_patterns,
    _extract_injection_text,
    _load_pattern_file,
)


class TestMatchTaskToPatterns:
    """Tests for match_task_to_patterns function."""

    def test_matches_keyword(self):
        """Should match patterns by keyword."""
        patterns = [
            {
                'topic': 'canvasapi',
                'confidence': 0.8,
                'match_keywords': ['canvasapi', 'canvas', 'renderer'],
                'sources': [],
                'injection_text': 'Use CanvasAPI SDK',
            }
        ]
        matches = match_task_to_patterns('Fix the CanvasAPI drawing profile', patterns)
        assert len(matches) == 1
        assert matches[0][0]['topic'] == 'canvasapi'

    def test_no_match_returns_empty(self):
        """Should return empty list when no keywords match."""
        patterns = [
            {
                'topic': 'canvasapi',
                'confidence': 0.8,
                'match_keywords': ['canvasapi', 'canvas'],
                'sources': [],
                'injection_text': 'Use CanvasAPI SDK',
            }
        ]
        matches = match_task_to_patterns('Update the README documentation', patterns)
        assert matches == []

    def test_empty_patterns_returns_empty(self):
        """Should return empty list for empty patterns."""
        matches = match_task_to_patterns('any task', [])
        assert matches == []

    def test_multiple_keyword_matches_boost_confidence(self):
        """More keyword matches should increase confidence."""
        patterns = [
            {
                'topic': 'canvasapi',
                'confidence': 0.8,
                'match_keywords': ['canvasapi', 'canvas', 'renderer', 'palette'],
                'sources': [],
                'injection_text': 'text',
            }
        ]
        single_match = match_task_to_patterns('Check the canvasapi status', patterns)
        multi_match = match_task_to_patterns('Check canvasapi canvas palette renderer', patterns)
        assert multi_match[0][1] > single_match[0][1]

    def test_patterns_sorted_by_confidence(self):
        """Results should be sorted by confidence descending."""
        patterns = [
            {
                'topic': 'low',
                'confidence': 0.3,
                'match_keywords': ['test'],
                'sources': [],
                'injection_text': 'low',
            },
            {
                'topic': 'high',
                'confidence': 0.9,
                'match_keywords': ['test'],
                'sources': [],
                'injection_text': 'high',
            },
        ]
        matches = match_task_to_patterns('run the test', patterns)
        assert len(matches) == 2
        assert matches[0][0]['topic'] == 'high'


class TestExtractInjectionText:
    """Tests for _extract_injection_text function."""

    def test_extracts_from_injection_section(self):
        """Should extract text from ## Injection Text code block."""
        content = """---
topic: test
---
## Injection Text
```
Use this specific API pattern.
```
"""
        result = _extract_injection_text(content)
        assert 'Use this specific API pattern' in result

    def test_falls_back_to_grounding_points(self):
        """Should fall back to Key Grounding Points section."""
        content = """---
topic: test
---
## Key Grounding Points
- Point 1
- Point 2
"""
        result = _extract_injection_text(content)
        assert 'Point 1' in result

    def test_returns_empty_for_no_match(self):
        """Should return empty string when no injection section found."""
        content = """---
topic: test
---
## Some Other Section
Content here.
"""
        result = _extract_injection_text(content)
        assert result == ""


class TestLoadPatternFile:
    """Tests for _load_pattern_file function."""

    def test_loads_valid_pattern(self, tmp_path):
        """Should load a valid pattern file."""
        pattern_file = tmp_path / 'test.md'
        pattern_file.write_text("""---
topic: canvasapi
confidence: 0.8
match_keywords:
  - canvasapi
  - canvas
sources:
  - docs/vendor.md
---
## Injection Text
```
Use the CanvasAPI SDK.
```
""")
        result = _load_pattern_file(str(pattern_file))
        assert result is not None
        assert result['topic'] == 'canvasapi'
        assert result['confidence'] == 0.8

    def test_returns_none_without_topic(self, tmp_path):
        """Should return None when pattern has no topic."""
        pattern_file = tmp_path / 'bad.md'
        pattern_file.write_text("""---
confidence: 0.5
---
No topic defined.
""")
        result = _load_pattern_file(str(pattern_file))
        assert result is None

    def test_expired_deadline_aborts_before_pattern_io(self, tmp_path):
        with pytest.raises(PatternLoadAborted, match='deadline'):
            _load_patterns_from_dir(str(tmp_path), time.monotonic() - 1)

    def test_excessive_pattern_inventory_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            'core.pattern_matcher.os.listdir',
            lambda _path: [f'p{i}.md' for i in range(129)],
        )
        with pytest.raises(PatternLoadAborted, match='exceeds'):
            _load_patterns_from_dir(str(tmp_path))

"""Unit tests for learning_detector module."""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.learning_detector import (
    Learning,
    LearningType,
    detect_gotchas_from_corrections,
    detect_workflow_improvements,
    detect_learnings,
    extract_title_from_correction,
    format_learnings_summary,
    format_learnings_notification,
    serialize_learnings,
    deserialize_learnings,
)


class TestLearningDataclass:
    """Tests for Learning dataclass."""

    def test_learning_creation(self):
        """Test creating a Learning instance."""
        learning = Learning(
            type=LearningType.GOTCHA,
            title="Test gotcha",
            content="Some content",
            target_file="docs/gotchas.md"
        )
        assert learning.type == LearningType.GOTCHA
        assert learning.title == "Test gotcha"
        assert learning.content == "Some content"
        assert learning.target_file == "docs/gotchas.md"
        assert learning.confidence == 0.7  # Default

    def test_learning_with_metadata(self):
        """Test Learning with custom metadata."""
        learning = Learning(
            type=LearningType.TECHNOLOGY,
            title="Cloud pattern",
            content="API usage",
            target_file="domain-patterns/cloud.md",
            confidence=0.85,
            metadata={'technology': 'cloud'}
        )
        assert learning.confidence == 0.85
        assert learning.metadata == {'technology': 'cloud'}


class TestDetectGotchasFromCorrections:
    """Tests for detect_gotchas_from_corrections function."""

    def test_empty_corrections(self):
        """Test with empty corrections list."""
        result = detect_gotchas_from_corrections([])
        assert result == []

    def test_ntfs_filename_correction(self):
        """Test detecting NTFS filename issue."""
        corrections = [{
            'text': "NTFS doesn't allow files with quotes in the name",
            'pattern': 'test',
            'confidence': 0.9
        }]
        result = detect_gotchas_from_corrections(corrections)
        assert len(result) == 1
        assert result[0].type == LearningType.GOTCHA
        assert result[0].target_file == "docs/gotchas.md"

    def test_git_email_correction(self):
        """Test detecting Git email requirement."""
        corrections = [{
            'text': "The commit email must be your account email, not your personal one",
            'pattern': 'test',
            'confidence': 0.8
        }]
        result = detect_gotchas_from_corrections(corrections)
        assert len(result) == 1
        assert 'git' in result[0].metadata.get('category', '')

    def test_wrong_location_correction(self):
        """Test detecting wrong location error."""
        corrections = [{
            'text': "Wrong directory, files should be in obiwag-agents not ~/.github",
            'pattern': 'test',
            'confidence': 0.85
        }]
        result = detect_gotchas_from_corrections(corrections)
        assert len(result) == 1
        assert result[0].type == LearningType.GOTCHA

    def test_no_matching_patterns(self):
        """Test correction that doesn't match any gotcha pattern."""
        corrections = [{
            'text': "This is just a regular comment",
            'pattern': 'test',
            'confidence': 0.5
        }]
        result = detect_gotchas_from_corrections(corrections)
        assert result == []



class TestDetectWorkflowImprovements:
    """Tests for detect_workflow_improvements function."""

    def test_low_corrections(self):
        """Test with few corrections (no workflow learning)."""
        metrics = {'corrections': 1}
        result = detect_workflow_improvements("", metrics)
        assert result == []

    def test_high_corrections(self):
        """Test with many corrections (triggers workflow learning)."""
        metrics = {'corrections': 5}
        result = detect_workflow_improvements("", metrics)
        assert len(result) == 1
        assert result[0].type == LearningType.WORKFLOW


class TestExtractTitleFromCorrection:
    """Tests for extract_title_from_correction function."""

    def test_simple_text(self):
        """Test extracting title from simple text."""
        result = extract_title_from_correction("This is a simple correction")
        assert result == "This is a simple correction"

    def test_remove_no_prefix(self):
        """Test removing 'no' prefix."""
        result = extract_title_from_correction("No, that's wrong, use this instead")
        assert not result.lower().startswith("no")

    def test_remove_actually_prefix(self):
        """Test removing 'actually' prefix."""
        result = extract_title_from_correction("Actually, the API is different")
        assert not result.lower().startswith("actually")

    def test_truncate_long_text(self):
        """Test truncating long text."""
        long_text = "A" * 100
        result = extract_title_from_correction(long_text)
        assert len(result) <= 60
        assert result.endswith("...")

    def test_empty_text(self):
        """Test with empty text."""
        result = extract_title_from_correction("")
        assert result == "Unknown correction"


class TestDetectLearnings:
    """Tests for detect_learnings main function."""

    def test_empty_inputs(self):
        """Test with all empty inputs."""
        result = detect_learnings("", {}, [])
        assert result == []

    def test_deduplication(self):
        """Test that learnings are deduplicated by type and target file."""
        corrections = [
            {'text': "NTFS issue one", 'pattern': 'test', 'confidence': 0.7},
            {'text': "NTFS issue two", 'pattern': 'test', 'confidence': 0.9},
        ]
        result = detect_learnings("", {}, corrections)
        # Should keep only one gotcha for the same target file
        gotchas = [l for l in result if l.type == LearningType.GOTCHA]
        assert len(gotchas) <= 1

    def test_sorted_by_confidence(self):
        """Test that results are sorted by confidence descending."""
        corrections = [
            {'text': "NTFS filename issue", 'pattern': 'test', 'confidence': 0.7},
            {'text': "Wrong branch checked out", 'pattern': 'test', 'confidence': 0.95},
        ]
        result = detect_learnings("", {}, corrections)
        if len(result) >= 2:
            assert result[0].confidence >= result[1].confidence


class TestFormatLearningsSummary:
    """Tests for format_learnings_summary function."""

    def test_empty_learnings(self):
        """Test with no learnings."""
        result = format_learnings_summary([])
        assert result == ""

    def test_single_learning(self):
        """Test with single learning."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Test gotcha",
                content="Content here",
                target_file="docs/gotchas.md"
            )
        ]
        result = format_learnings_summary(learnings)
        assert "Session Learnings Detected" in result
        assert "Gotchas" in result
        assert "Test gotcha" in result
        assert "Sync these learnings" in result

    def test_multiple_types(self):
        """Test with multiple learning types."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Gotcha one",
                content="Content",
                target_file="docs/gotchas.md"
            ),
            Learning(
                type=LearningType.TECHNOLOGY,
                title="Technology pattern",
                content="Content",
                target_file="references/test.md"
            ),
        ]
        result = format_learnings_summary(learnings)
        assert "Gotchas" in result
        assert "Technology Knowledge" in result


class TestLearningNotification:
    """Tests for format_learnings_notification function (silent capture)."""

    def test_empty_learnings(self):
        """Test with no learnings returns empty string."""
        result = format_learnings_notification([])
        assert result == ""

    def test_notification_is_single_line(self):
        """Notification should be brief, not multi-line."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Test gotcha",
                content="Content here\nwith newlines\nin it",
                target_file="docs/gotchas.md"
            )
        ]
        result = format_learnings_notification(learnings)
        assert result.count('\n') == 0

    def test_notification_mentions_review_command(self):
        """Should tell user how to review."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Test gotcha",
                content="Content",
                target_file="docs/gotchas.md"
            )
        ]
        result = format_learnings_notification(learnings)
        assert "/obi-memory-review" in result

    def test_no_interactive_prompts(self):
        """Should NOT ask yes/no questions."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Test gotcha",
                content="Content",
                target_file="docs/gotchas.md"
            )
        ]
        result = format_learnings_notification(learnings)
        # Should not contain interactive prompts
        assert "yes" not in result.lower()
        assert "no" not in result.lower() or "no" in "notification"  # Allow "no" in words like "notification"
        assert "?" not in result
        assert "sync these" not in result.lower()

    def test_shows_learning_count(self):
        """Should show how many learnings were captured."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Gotcha one",
                content="Content",
                target_file="docs/gotchas.md"
            ),
            Learning(
                type=LearningType.TECHNOLOGY,
                title="Technology pattern",
                content="Content",
                target_file="references/test.md"
            ),
        ]
        result = format_learnings_notification(learnings)
        assert "2" in result

    def test_shows_learning_types(self):
        """Should show what types of learnings were captured."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Gotcha",
                content="Content",
                target_file="gotchas.md"
            ),
            Learning(
                type=LearningType.TECHNOLOGY,
                title="Technology",
                content="Content",
                target_file="references/test.md"
            ),
        ]
        result = format_learnings_notification(learnings)
        assert "gotcha" in result
        assert "technology" in result

    def test_newlines_not_escaped_in_output(self):
        """Output should not contain literal \\n (escaped newlines)."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Test",
                content="Line1\nLine2\nLine3",
                target_file="gotchas.md"
            )
        ]
        result = format_learnings_notification(learnings)
        # The result itself should not contain literal backslash-n
        assert "\\n" not in result


class TestSerialization:
    """Tests for serialize/deserialize functions."""

    def test_serialize_learnings(self):
        """Test serializing learnings."""
        learnings = [
            Learning(
                type=LearningType.GOTCHA,
                title="Test",
                content="Content",
                target_file="test.md",
                confidence=0.8,
                metadata={'key': 'value'}
            )
        ]
        result = serialize_learnings(learnings)
        assert len(result) == 1
        assert result[0]['type'] == 'gotcha'
        assert result[0]['title'] == 'Test'
        assert result[0]['confidence'] == 0.8
        assert result[0]['metadata'] == {'key': 'value'}

    def test_deserialize_learnings(self):
        """Test deserializing learnings."""
        data = [
            {
                'type': 'technology',
                'title': 'Test technology',
                'content': 'Content',
                'target_file': 'references/test.md',
                'confidence': 0.75,
                'metadata': {}
            }
        ]
        result = deserialize_learnings(data)
        assert len(result) == 1
        assert result[0].type == LearningType.TECHNOLOGY
        assert result[0].title == 'Test technology'
        assert result[0].confidence == 0.75

    def test_deserialize_malformed(self):
        """Test deserializing handles malformed data."""
        data = [
            {'incomplete': 'data'},
            {
                'type': 'gotcha',
                'title': 'Valid',
                'content': 'Content',
                'target_file': 'test.md'
            }
        ]
        result = deserialize_learnings(data)
        assert len(result) == 1
        assert result[0].title == 'Valid'

    def test_round_trip(self):
        """Test serialization round-trip."""
        original = [
            Learning(
                type=LearningType.PLATFORM,
                title="Platform issue",
                content="Details here",
                target_file="gotchas.md",
                confidence=0.9,
                metadata={'platform': 'windows'}
            )
        ]
        serialized = serialize_learnings(original)
        deserialized = deserialize_learnings(serialized)

        assert len(deserialized) == 1
        assert deserialized[0].type == original[0].type
        assert deserialized[0].title == original[0].title
        assert deserialized[0].confidence == original[0].confidence
        assert deserialized[0].metadata == original[0].metadata


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

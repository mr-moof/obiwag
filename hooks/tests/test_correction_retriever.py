"""Unit tests for correction_retriever module."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.correction_retriever import (
    score_correction_relevance,
    extract_keywords_from_task,
    read_recent_corrections,
)


class TestScoreCorrectionRelevance:
    """Tests for score_correction_relevance function."""

    def test_task_type_match_boosts_score(self):
        """Task type match should add 0.4 to score."""
        correction = {'task_type': 'review', 'timestamp': datetime.now().isoformat()}
        score = score_correction_relevance(correction, 'review', [])
        assert score >= 0.4

    def test_task_type_mismatch(self):
        """Different task type should not get type bonus."""
        correction = {'task_type': 'review', 'timestamp': datetime.now().isoformat()}
        score = score_correction_relevance(correction, 'author', [])
        assert score < 0.4

    def test_keyword_match_boosts_score(self):
        """Keyword matches should increase score."""
        correction = {
            'task_type': 'other',
            'user_message': 'Fix the cloud API call',
            'correction_type': 'api_shape',
        }
        score_with = score_correction_relevance(correction, 'other', ['cloud', 'api'])
        score_without = score_correction_relevance(correction, 'other', ['unrelated'])
        assert score_with > score_without

    def test_vendor_hallucination_bonus(self):
        """vendor_hallucination correction type should get extra weight."""
        base = {'task_type': 'other', 'correction_type': 'general'}
        vendor = {'task_type': 'other', 'correction_type': 'vendor_hallucination'}
        score_base = score_correction_relevance(base, 'other', [])
        score_vendor = score_correction_relevance(vendor, 'other', [])
        assert score_vendor > score_base

    def test_score_capped_at_one(self):
        """Score should never exceed 1.0."""
        correction = {
            'task_type': 'review',
            'correction_type': 'vendor_hallucination',
            'user_message': 'cloud azure powershell api database',
            'timestamp': datetime.now().isoformat(),
        }
        keywords = ['cloud', 'azure', 'powershell', 'api', 'database']
        score = score_correction_relevance(correction, 'review', keywords)
        assert score <= 1.0

    def test_recency_bonus(self):
        """Recent corrections should score higher than old ones."""
        recent = {
            'task_type': 'other',
            'timestamp': datetime.now().isoformat(),
        }
        old = {
            'task_type': 'other',
            'timestamp': (datetime.now() - timedelta(days=30)).isoformat(),
        }
        score_recent = score_correction_relevance(recent, 'other', [])
        score_old = score_correction_relevance(old, 'other', [])
        assert score_recent >= score_old


class TestExtractKeywordsFromTask:
    """Tests for extract_keywords_from_task function."""

    def test_finds_vendor_keywords(self):
        """Should find known vendor keywords in task text."""
        keywords = extract_keywords_from_task('Fix the cloud API endpoint')
        assert 'cloud' in keywords
        assert 'api' in keywords
        assert 'endpoint' in keywords

    def test_empty_text(self):
        """Should return empty list for empty text."""
        keywords = extract_keywords_from_task('')
        assert keywords == []


class TestReadRecentCorrections:
    """Tests for read_recent_corrections function."""

    def test_returns_empty_for_missing_dir(self, monkeypatch):
        """Should return empty list when corrections dir doesn't exist."""
        monkeypatch.setattr(
            'core.correction_retriever.get_corrections_path',
            lambda: '/nonexistent/path'
        )
        result = read_recent_corrections()
        assert result == []

    def test_reads_jsonl_files(self, tmp_path, monkeypatch):
        """Should read corrections from JSONL files."""
        monkeypatch.setattr(
            'core.correction_retriever.get_corrections_path',
            lambda: str(tmp_path)
        )
        today = datetime.now().strftime('%Y-%m-%d')
        jsonl_file = tmp_path / f'{today}.jsonl'
        entry = {'task_type': 'review', 'user_message': 'test'}
        jsonl_file.write_text(json.dumps(entry) + '\n')

        result = read_recent_corrections(days=1)
        assert len(result) == 1
        assert result[0]['task_type'] == 'review'

    def test_skips_old_files(self, tmp_path, monkeypatch):
        """Should skip files older than the cutoff."""
        monkeypatch.setattr(
            'core.correction_retriever.get_corrections_path',
            lambda: str(tmp_path)
        )
        old_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        jsonl_file = tmp_path / f'{old_date}.jsonl'
        entry = {'task_type': 'review', 'user_message': 'old'}
        jsonl_file.write_text(json.dumps(entry) + '\n')

        result = read_recent_corrections(days=30)
        assert result == []

"""Unit tests for swarm_classifier module."""

import sys
from pathlib import Path

import pytest

# Add tools directory to path for imports
TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLS_DIR))

from swarm_classifier import classify_issue, classify_batch, LABEL_TIER_MAP, DEFAULT_TIER


class TestClassifyIssueLabels:
    """Test label-based classification (highest priority)."""

    def test_explicit_risk_config_label(self):
        """Explicit risk:config label maps to config-only."""
        result = classify_issue('Something', '', labels=['risk:config'])
        assert result == 'config-only'

    def test_explicit_risk_single_label(self):
        """Explicit risk:single label maps to single-file."""
        result = classify_issue('Something', '', labels=['risk:single'])
        assert result == 'single-file'

    def test_explicit_risk_multi_label(self):
        """Explicit risk:multi label maps to multi-file."""
        result = classify_issue('Something', '', labels=['risk:multi'])
        assert result == 'multi-file'

    def test_docs_label(self):
        """Documentation label maps to config-only."""
        result = classify_issue('Update API endpoints', '', labels=['docs'])
        assert result == 'config-only'

    def test_bug_label(self):
        """Bug label maps to single-file."""
        result = classify_issue('Refactor the entire module', '', labels=['bug'])
        assert result == 'single-file'

    def test_refactor_label(self):
        """Refactor label maps to multi-file."""
        result = classify_issue('Fix typo in readme', '', labels=['refactor'])
        assert result == 'multi-file'

    def test_label_takes_precedence_over_keywords(self):
        """Labels override keyword-based classification."""
        # Title says 'refactor' (multi-file keyword) but label says config
        result = classify_issue('Refactor everything', '', labels=['risk:config'])
        assert result == 'config-only'

    def test_first_matching_label_wins(self):
        """First matching label in the list wins."""
        result = classify_issue('Something', '', labels=['risk:config', 'risk:multi'])
        assert result == 'config-only'

    def test_unknown_labels_ignored(self):
        """Non-matching labels fall through to keyword matching."""
        result = classify_issue('Fix typo in README', '', labels=['enhancement', 'ready'])
        assert result == 'config-only'  # keyword match on 'typo' and 'readme'

    def test_label_case_insensitive(self):
        """Label matching is case-insensitive."""
        result = classify_issue('Something', '', labels=['Risk:Config'])
        assert result == 'config-only'


class TestClassifyIssueKeywords:
    """Test keyword-based classification."""

    def test_readme_keyword(self):
        """'readme' in title triggers config-only."""
        result = classify_issue('Update README.md')
        assert result == 'config-only'

    def test_typo_keyword(self):
        """'typo' in title triggers config-only."""
        result = classify_issue('Fix typo in deployment docs')
        assert result == 'config-only'

    def test_changelog_keyword(self):
        """'changelog' in title triggers config-only."""
        result = classify_issue('Add changelog entry')
        assert result == 'config-only'

    def test_docstring_keyword(self):
        """'docstring' in description triggers config-only."""
        result = classify_issue('Add missing docs', 'Add docstring to main()')
        assert result == 'config-only'

    def test_fix_keyword(self):
        """'fix' in title triggers single-file."""
        result = classify_issue('Fix null check in handler')
        assert result == 'single-file'

    def test_bug_keyword(self):
        """'bug' in title triggers single-file."""
        result = classify_issue('Bug in error handler')
        assert result == 'single-file'

    def test_refactor_keyword(self):
        """'refactor' in title triggers multi-file."""
        result = classify_issue('Refactor auth module')
        assert result == 'multi-file'

    def test_rename_across_keyword(self):
        """'rename across' in description triggers multi-file."""
        result = classify_issue('Update naming', 'Rename across all modules')
        assert result == 'multi-file'

    def test_keywords_case_insensitive(self):
        """Keyword matching is case-insensitive."""
        result = classify_issue('Update the README')
        assert result == 'config-only'

    def test_keyword_in_description(self):
        """Keywords in description are also matched."""
        result = classify_issue('Update file', 'This is a documentation change')
        assert result == 'config-only'

    def test_tie_prefers_lower_risk(self):
        """On keyword tie, prefer lower-risk tier."""
        # 'fix' (single) and 'readme' (config) both match
        result = classify_issue('Fix readme typo')
        assert result == 'config-only'


class TestClassifyIssueDefault:
    """Test default fallback behavior."""

    def test_no_keywords_returns_default(self):
        """No matching keywords returns default tier."""
        result = classify_issue('Implement new feature XYZ')
        assert result == DEFAULT_TIER

    def test_empty_title(self):
        """Empty title returns default."""
        result = classify_issue('')
        assert result == DEFAULT_TIER

    def test_none_labels(self):
        """None labels handled gracefully."""
        result = classify_issue('Something', '', labels=None)
        assert result == DEFAULT_TIER

    def test_empty_labels(self):
        """Empty labels list handled gracefully."""
        result = classify_issue('Something', '', labels=[])
        assert result == DEFAULT_TIER


class TestClassifyBatch:
    """Test batch classification."""

    def test_empty_batch(self):
        """Empty batch returns empty groups."""
        result = classify_batch([])
        assert result == {'config-only': [], 'single-file': [], 'multi-file': []}

    def test_groups_by_tier(self):
        """Issues are grouped into correct tiers."""
        issues = [
            {'id': 1, 'title': 'Fix typo in README', 'labels': []},
            {'id': 2, 'title': 'Fix null check', 'labels': ['bug']},
            {'id': 3, 'title': 'Refactor auth', 'labels': []},
        ]
        result = classify_batch(issues)

        assert len(result['config-only']) == 1
        assert result['config-only'][0]['id'] == 1
        assert result['config-only'][0]['tier'] == 'config-only'

        assert len(result['single-file']) == 1
        assert result['single-file'][0]['id'] == 2

        assert len(result['multi-file']) == 1
        assert result['multi-file'][0]['id'] == 3

    def test_batch_preserves_issue_data(self):
        """Batch adds tier but preserves original issue data."""
        issues = [
            {'id': 5, 'title': 'Fix typo', 'description': 'In docs', 'labels': ['ready']},
        ]
        result = classify_batch(issues)

        classified = result['config-only'][0]
        assert classified['id'] == 5
        assert classified['title'] == 'Fix typo'
        assert classified['description'] == 'In docs'
        assert classified['labels'] == ['ready']
        assert classified['tier'] == 'config-only'

    def test_batch_with_labels_and_keywords(self):
        """Batch handles mix of label and keyword classification."""
        issues = [
            {'id': 1, 'title': 'Something', 'labels': ['risk:config']},
            {'id': 2, 'title': 'Fix changelog entry', 'labels': []},
        ]
        result = classify_batch(issues)

        assert len(result['config-only']) == 2

    def test_batch_missing_fields_handled(self):
        """Issues with missing fields don't crash."""
        issues = [
            {'id': 1},  # no title, description, or labels
        ]
        result = classify_batch(issues)
        # Should get default tier
        assert len(result[DEFAULT_TIER]) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

"""Unit tests for hooks/core/pipeline_classifier.py.

The classifier is the unit; the `gh run` subprocess boundary is exercised via
integration tests in tests/integration/.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.pipeline_classifier import (
    classify_failure,
    is_terminal_failure,
    same_classification,
)


class TestClassifyFailure:

    def test_runner_unavailable_explicit_log(self):
        trace = "This job is waiting for a runner to come online with labels: self-hosted, windows"
        result = classify_failure(trace)
        assert result['class'] == 'runner-unavailable'
        assert 'runner' in result['action'].lower()

    def test_runner_unavailable_pending_too_long(self):
        # No log mention of a runner, but queued for 10 minutes with no pickup
        result = classify_failure(trace='', pending_seconds=600)
        assert result['class'] == 'runner-unavailable'

    def test_pending_under_threshold_is_script_error(self):
        # Queued for 4 minutes is below the 5-minute threshold
        result = classify_failure(trace='', pending_seconds=240)
        assert result['class'] == 'script-error'

    def test_quota_exhaustion(self):
        trace = 'Error: You have exceeded your spending limit for GitHub Actions'
        result = classify_failure(trace)
        assert result['class'] == 'quota'
        assert 'NEEDS USER INPUT' in result['action']

    def test_quota_alternate_message(self):
        trace = 'Job aborted: usage limit reached for Actions minutes'
        result = classify_failure(trace)
        assert result['class'] == 'quota'

    def test_yaml_syntax_error(self):
        trace = 'Invalid workflow file: .github/workflows/ci.yml#L12'
        result = classify_failure(trace)
        assert result['class'] == 'yaml-error'
        assert 'workflow' in result['action'].lower()

    def test_yaml_syntax_alt(self):
        trace = "The workflow is not valid. Unexpected value 'foo'"
        result = classify_failure(trace)
        assert result['class'] == 'yaml-error'

    def test_image_pull_failure(self):
        trace = 'Error: pull access denied for myregistry/private:latest'
        result = classify_failure(trace)
        assert result['class'] == 'image-pull-failure'
        assert 'registry' in result['action'].lower()

    def test_image_pull_manifest(self):
        trace = 'manifest unknown for image:tag'
        result = classify_failure(trace)
        assert result['class'] == 'image-pull-failure'

    def test_image_pull_not_found(self):
        trace = 'docker: image foo:latest not found'
        result = classify_failure(trace)
        assert result['class'] == 'image-pull-failure'

    def test_script_error_catch_all(self):
        trace = 'npm test exited with code 1\n  AssertionError: 2 + 2 != 5'
        result = classify_failure(trace)
        assert result['class'] == 'script-error'
        assert 'Author' in result['action']

    def test_empty_trace(self):
        result = classify_failure('')
        assert result['class'] == 'script-error'

    def test_none_trace(self):
        result = classify_failure(None)
        assert result['class'] == 'script-error'


class TestIsTerminalFailure:

    def test_quota_is_terminal(self):
        assert is_terminal_failure('quota') is True

    def test_others_are_not_terminal(self):
        assert is_terminal_failure('runner-unavailable') is False
        assert is_terminal_failure('yaml-error') is False
        assert is_terminal_failure('image-pull-failure') is False
        assert is_terminal_failure('script-error') is False


class TestSameClassification:

    def test_same(self):
        assert same_classification('script-error', 'script-error') is True

    def test_different(self):
        assert same_classification('script-error', 'yaml-error') is False

    def test_first_iteration(self):
        # No previous classification => not the same
        assert same_classification(None, 'script-error') is False


class TestThreeFailureTypes:
    """Acceptance: an iterate-until-green session that sees three different
    failure classes in succession. The classifier should produce three
    distinct classes; same_classification flags only consecutive repeats.
    """

    def test_three_distinct_classifications(self):
        # Iteration 1: yaml-error
        trace1 = "Invalid workflow file: .github/workflows/ci.yml typo"
        # Iteration 2: image-pull-failure
        trace2 = "Error: pull access denied for myimg"
        # Iteration 3: script-error
        trace3 = "Exception: assertion failed at test_foo"

        c1 = classify_failure(trace1)
        c2 = classify_failure(trace2)
        c3 = classify_failure(trace3)

        assert c1['class'] == 'yaml-error'
        assert c2['class'] == 'image-pull-failure'
        assert c3['class'] == 'script-error'

        # No consecutive duplicates - 3-strike checkpoint should not fire
        assert same_classification(None, c1['class']) is False
        assert same_classification(c1['class'], c2['class']) is False
        assert same_classification(c2['class'], c3['class']) is False

    def test_two_consecutive_same_triggers_strike_flow(self):
        trace1 = "Invalid workflow file: typo on line 3"
        trace2 = "Invalid workflow file: still wrong"

        c1 = classify_failure(trace1)
        c2 = classify_failure(trace2)

        assert c1['class'] == c2['class'] == 'yaml-error'
        # Same classification twice in a row - 3-strike flow should engage
        assert same_classification(c1['class'], c2['class']) is True

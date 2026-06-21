"""Unit tests for calibration module."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import core.calibration as calibration_module
from core.calibration import (
    get_calibration_path,
    parse_yaml_frontmatter,
    parse_value,
    load_calibration,
    get_verification_budget,
    is_safety_enabled,
    get_pattern_threshold,
    get_max_injected_sources,
    increment_detector_firings,
)


@pytest.fixture
def reset_calibration_cache():
    """Clear the module-level calibration cache before and after a test.

    The mtime cache persists for the life of the process, so a value cached
    by an earlier test would otherwise leak into read-count assertions here.
    """
    calibration_module._calibration_cache = None
    calibration_module._calibration_mtime = 0
    calibration_module._calibration_path = None
    yield
    calibration_module._calibration_cache = None
    calibration_module._calibration_mtime = 0
    calibration_module._calibration_path = None


class TestParseValue:
    """Tests for parse_value function."""

    def test_parse_true(self):
        """Test parsing boolean true."""
        assert parse_value('true') is True
        assert parse_value('True') is True
        assert parse_value('TRUE') is True

    def test_parse_false(self):
        """Test parsing boolean false."""
        assert parse_value('false') is False
        assert parse_value('False') is False
        assert parse_value('FALSE') is False

    def test_parse_integer(self):
        """Test parsing integers."""
        assert parse_value('42') == 42
        assert parse_value('0') == 0
        assert parse_value('1000') == 1000

    def test_parse_float(self):
        """Test parsing floats."""
        assert parse_value('3.14') == 3.14
        assert parse_value('0.5') == 0.5
        assert parse_value('-1.5') == -1.5

    def test_parse_string(self):
        """Test parsing strings."""
        assert parse_value('hello') == 'hello'
        assert parse_value('"quoted"') == 'quoted'
        assert parse_value("'single quoted'") == 'single quoted'

    def test_parse_string_with_spaces(self):
        """Test parsing strings preserves content after stripping quotes."""
        assert parse_value('  hello  ') == 'hello'


class TestParseYamlFrontmatter:
    """Tests for parse_yaml_frontmatter function."""

    def test_empty_content(self):
        """Test parsing empty content."""
        assert parse_yaml_frontmatter('') == {}

    def test_no_frontmatter(self):
        """Test content without frontmatter delimiters."""
        assert parse_yaml_frontmatter('just some text') == {}

    def test_incomplete_frontmatter(self):
        """Test content with only one delimiter."""
        assert parse_yaml_frontmatter('---\nkey: value') == {}

    def test_simple_frontmatter(self):
        """Test parsing simple key-value pairs."""
        content = """---
key1: value1
key2: 42
key3: true
---
body content"""
        result = parse_yaml_frontmatter(content)

        assert result['key1'] == 'value1'
        assert result['key2'] == 42
        assert result['key3'] is True

    def test_nested_frontmatter(self):
        """Test parsing nested structure."""
        content = """---
section:
  key1: value1
  key2: 42
---
body"""
        result = parse_yaml_frontmatter(content)

        assert 'section' in result
        assert result['section']['key1'] == 'value1'
        assert result['section']['key2'] == 42

    def test_deeply_nested_frontmatter(self):
        """Test parsing three levels of nesting."""
        content = """---
level1:
  level2:
    level3: deep_value
---
body"""
        result = parse_yaml_frontmatter(content)

        assert result['level1']['level2']['level3'] == 'deep_value'

    def test_comments_ignored(self):
        """Test that comment lines are ignored."""
        content = """---
# This is a comment
key: value
---
body"""
        result = parse_yaml_frontmatter(content)

        assert result.get('key') == 'value'
        assert '#' not in str(result)


class TestLoadCalibration:
    """Tests for load_calibration function."""

    def test_returns_defaults_when_file_missing(self, tmp_path):
        """Test that defaults are returned when file doesn't exist."""
        with patch('core.calibration.get_calibration_path', return_value=str(tmp_path / 'nonexistent.md')):
            result = load_calibration()

        assert 'verification' in result
        assert result['verification']['default_budget'] == 1
        assert 'safety' in result
        assert 'patterns' in result

    def test_loads_from_file(self, tmp_path):
        """Test loading calibration from file."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
verification:
  default_budget: 3
---
# Calibration file""")

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            result = load_calibration()

        assert result['verification']['default_budget'] == 3

    def test_merges_with_defaults(self, tmp_path):
        """Test that loaded values merge with defaults."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
verification:
  default_budget: 5
---
content""")

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            result = load_calibration()

        # Custom value should be loaded
        assert result['verification']['default_budget'] == 5
        # Default value should still exist
        assert 'safety' in result
        assert result['safety']['auto_inject_sources'] is True

    def test_handles_corrupted_file(self, tmp_path):
        """Test graceful handling of corrupted file."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text('not valid yaml content without frontmatter')

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            result = load_calibration()

        # Should return defaults
        assert result['verification']['default_budget'] == 1


class TestGetVerificationBudget:
    """Tests for get_verification_budget function."""

    def test_returns_specific_budget(self, tmp_path):
        """Test getting budget for specific task type."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
verification:
  budgets_by_type:
    cloud: 5
---
""")

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            budget = get_verification_budget('cloud')

        assert budget == 5

    def test_returns_default_for_unknown_type(self, tmp_path):
        """Test getting default budget for unknown task type."""
        with patch('core.calibration.get_calibration_path', return_value=str(tmp_path / 'nonexistent.md')):
            budget = get_verification_budget('completely_unknown_type')

        assert budget == 1  # default_budget

    def test_case_insensitive(self, tmp_path):
        """Test that task type matching is case insensitive."""
        with patch('core.calibration.get_calibration_path', return_value=str(tmp_path / 'nonexistent.md')):
            budget_lower = get_verification_budget('cloud')
            budget_upper = get_verification_budget('CLOUD')

        assert budget_lower == budget_upper


class TestIsSafetyEnabled:
    """Tests for is_safety_enabled function."""

    def test_returns_true_by_default(self, tmp_path):
        """Test safety settings default to true."""
        with patch('core.calibration.get_calibration_path', return_value=str(tmp_path / 'nonexistent.md')):
            assert is_safety_enabled('auto_inject_sources') is True
            assert is_safety_enabled('log_corrections') is True

    def test_returns_configured_value(self, tmp_path):
        """Test returning configured safety value."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
safety:
  auto_inject_sources: false
---
""")

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            assert is_safety_enabled('auto_inject_sources') is False


class TestGetPatternThreshold:
    """Tests for get_pattern_threshold function."""

    def test_returns_default(self, tmp_path):
        """Test default threshold value."""
        with patch('core.calibration.get_calibration_path', return_value=str(tmp_path / 'nonexistent.md')):
            threshold = get_pattern_threshold()

        assert threshold == 0.7

    def test_returns_configured_value(self, tmp_path):
        """Test configured threshold value."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
patterns:
  source_injection_threshold: 0.9
---
""")

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            threshold = get_pattern_threshold()

        assert threshold == 0.9


class TestGetMaxInjectedSources:
    """Tests for get_max_injected_sources function."""

    def test_returns_default(self, tmp_path):
        """Test default max sources value."""
        with patch('core.calibration.get_calibration_path', return_value=str(tmp_path / 'nonexistent.md')):
            max_sources = get_max_injected_sources()

        assert max_sources == 3

    def test_returns_configured_value(self, tmp_path):
        """Test configured max sources value."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
patterns:
  max_injected_sources: 10
---
""")

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            max_sources = get_max_injected_sources()

        assert max_sources == 10


class TestGetCalibrationPath:
    """Tests for get_calibration_path function."""

    def test_returns_path_in_obi_directory(self):
        """Test path contains expected components."""
        path = get_calibration_path()

        assert '.claude' in path
        assert '.obi' in path
        assert 'calibration.md' in path


class TestIncrementDetectorFiringsAutoSeed:
    """Issue #150: increment_detector_firings must auto-seed missing blocks.

    Before the fix, a fresh-install workstation with no detectors: block in
    calibration.md silently dropped every fire (function returned False, the
    counter never wrote). After the fix, the first fire auto-seeds the
    detector block with default thresholds and ``firings_total = delta``.
    """

    def test_auto_seeds_detector_when_block_missing(self, tmp_path):
        """No detectors: block at all → first fire writes a fresh one."""
        cal_path = tmp_path / 'calibration.md'
        cal_path.write_text(
            "---\n"
            "version: \"1.0\"\n"
            "safety:\n"
            "  capability_claim: true\n"
            "---\n",
            encoding='utf-8',
        )

        with patch('core.calibration.get_calibration_path', return_value=str(cal_path)):
            result = increment_detector_firings('capability_claim', delta=1)

        assert result is True, "Auto-seed should succeed on missing block"
        content = cal_path.read_text(encoding='utf-8')
        assert 'detectors:' in content
        assert 'capability_claim:' in content
        assert 'firings_total: 1' in content

    def test_auto_seeds_when_detectors_block_exists_but_detector_missing(self, tmp_path):
        """detectors: block exists, but the named detector isn't there yet."""
        cal_path = tmp_path / 'calibration.md'
        cal_path.write_text(
            "---\n"
            "version: \"1.0\"\n"
            "detectors:\n"
            "  other_detector:\n"
            "    enabled: true\n"
            "    firings_total: 5\n"
            "---\n",
            encoding='utf-8',
        )

        with patch('core.calibration.get_calibration_path', return_value=str(cal_path)):
            result = increment_detector_firings('capability_claim', delta=2)

        assert result is True
        content = cal_path.read_text(encoding='utf-8')
        # Both detectors should be present now
        assert 'other_detector:' in content
        assert 'capability_claim:' in content
        assert 'firings_total: 5' in content  # untouched
        assert 'firings_total: 2' in content  # new

    def test_existing_detector_increments_in_place(self, tmp_path):
        """Pre-fix behavior preserved: existing detector block bumps the counter."""
        cal_path = tmp_path / 'calibration.md'
        cal_path.write_text(
            "---\n"
            "version: \"1.0\"\n"
            "detectors:\n"
            "  capability_claim:\n"
            "    enabled: true\n"
            "    review_after_sessions: 20\n"
            "    review_after_firings: 15\n"
            "    firings_total: 7\n"
            "---\n",
            encoding='utf-8',
        )

        with patch('core.calibration.get_calibration_path', return_value=str(cal_path)):
            result = increment_detector_firings('capability_claim', delta=3)

        assert result is True
        content = cal_path.read_text(encoding='utf-8')
        assert 'firings_total: 10' in content
        # Original surrounding values should remain intact
        assert 'review_after_sessions: 20' in content


class TestLoadCalibrationCaching:
    """Issue #176 (OPT-03): load_calibration must dedupe reads within a process.

    PostToolUse calls load_calibration / get_interval / is_safety_enabled
    2-3 times per tool event. On network-profile VDIs each open() is a stat
    storm. The mtime cache must serve repeat calls from memory: at most ONE
    calibration.md read per process while the file is unchanged.
    """

    def test_unchanged_mtime_reads_file_once(self, tmp_path, reset_calibration_cache):
        """Two load_calibration() calls with unchanged mtime → exactly one read."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
verification:
  default_budget: 4
---
# Calibration""", encoding='utf-8')

        real_open = open
        read_count = {'n': 0}

        def counting_open(path, *args, **kwargs):
            # Only count opens of the calibration file itself.
            if str(path) == str(cal_file):
                read_count['n'] += 1
            return real_open(path, *args, **kwargs)

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            with patch('core.calibration.open', counting_open, create=True):
                first = load_calibration()
                second = load_calibration()

        assert read_count['n'] == 1, (
            f"expected 1 calibration.md read for two cached calls, "
            f"got {read_count['n']}"
        )
        # Both calls must return the same parsed content.
        assert first['verification']['default_budget'] == 4
        assert second['verification']['default_budget'] == 4

    def test_changed_mtime_rereads_file(self, tmp_path, reset_calibration_cache):
        """A changed mtime must invalidate the cache and trigger a fresh read."""
        cal_file = tmp_path / 'calibration.md'
        cal_file.write_text("""---
verification:
  default_budget: 2
---
""", encoding='utf-8')

        real_open = open
        read_count = {'n': 0}

        def counting_open(path, *args, **kwargs):
            if str(path) == str(cal_file):
                read_count['n'] += 1
            return real_open(path, *args, **kwargs)

        with patch('core.calibration.get_calibration_path', return_value=str(cal_file)):
            with patch('core.calibration.open', counting_open, create=True):
                first = load_calibration()
                # Rewrite with a different mtime + value, then read again.
                import os
                cal_file.write_text("""---
verification:
  default_budget: 9
---
""", encoding='utf-8')
                bumped = os.path.getmtime(str(cal_file)) + 5
                os.utime(str(cal_file), (bumped, bumped))
                second = load_calibration()

        assert read_count['n'] == 2, (
            f"expected 2 reads after mtime change, got {read_count['n']}"
        )
        assert first['verification']['default_budget'] == 2
        assert second['verification']['default_budget'] == 9


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

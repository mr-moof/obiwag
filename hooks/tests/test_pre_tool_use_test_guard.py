"""Tests for pre_tool_use test-file edit detection (fix the code under test, not the test)."""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from pre_tool_use import check_test_file_edit


class TestTestFilePatternMatching:
    """Verify TEST_FILE_PATTERNS match the right files."""

    def test_matches_test_directory(self):
        result = check_test_file_edit({"file_path": "C:/repo/tests/test_foo.py"})
        assert result is not None
        assert "fix the code" in result

    def test_matches_test_underscore_prefix(self):
        result = check_test_file_edit({"file_path": "C:/repo/test_quality_signals.py"})
        assert result is not None

    def test_matches_suffix_test_py(self):
        result = check_test_file_edit({"file_path": "C:/repo/quality_signals_test.py"})
        assert result is not None

    def test_matches_test_js(self):
        result = check_test_file_edit({"file_path": "C:/repo/src/utils.test.js"})
        assert result is not None

    def test_matches_spec_tsx(self):
        result = check_test_file_edit({"file_path": "C:/repo/Component.spec.tsx"})
        assert result is not None

    def test_matches_pester_test(self):
        result = check_test_file_edit({"file_path": "C:/repo/Deploy.tests.ps1"})
        assert result is not None

    def test_matches_test_subdir(self):
        """Files inside a test/ directory should match."""
        result = check_test_file_edit({"file_path": "C:/repo/test/helpers.py"})
        assert result is not None

    def test_matches_backslash_paths(self):
        """Windows backslash paths should also match."""
        result = check_test_file_edit({"file_path": "C:\\repo\\tests\\test_foo.py"})
        assert result is not None


class TestNonTestFilesNotMatched:
    """Verify normal files do NOT trigger the reminder."""

    def test_no_match_normal_py(self):
        assert check_test_file_edit({"file_path": "C:/repo/hooks/pre_tool_use.py"}) is None

    def test_no_match_main_py(self):
        assert check_test_file_edit({"file_path": "C:/repo/src/main.py"}) is None

    def test_no_match_readme(self):
        assert check_test_file_edit({"file_path": "C:/repo/README.md"}) is None

    def test_no_match_settings_json(self):
        assert check_test_file_edit({"file_path": "C:/repo/settings.json"}) is None

    def test_no_match_powershell_module(self):
        assert check_test_file_edit({"file_path": "C:/repo/Module.psm1"}) is None

    def test_no_match_conftest(self):
        """conftest.py is test infrastructure, not a test file itself."""
        assert check_test_file_edit({"file_path": "C:/repo/conftest.py"}) is None


class TestEdgeCases:
    """Edge cases for the test file guard."""

    def test_empty_file_path(self):
        assert check_test_file_edit({"file_path": ""}) is None

    def test_missing_file_path(self):
        assert check_test_file_edit({}) is None

    def test_none_file_path(self):
        assert check_test_file_edit({"file_path": None}) is None

    def test_reminder_content(self):
        """Verify the reminder has the expected content."""
        result = check_test_file_edit({"file_path": "C:/repo/tests/test_foo.py"})
        assert "test file" in result.lower()
        assert "fix the code under test rather than the test" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

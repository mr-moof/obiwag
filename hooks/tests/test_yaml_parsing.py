"""Edge-case tests for the YAML frontmatter parser in calibration.py.

The parser was replaced with ``yaml.safe_load`` under issue #120. This file
originally pinned the hand-rolled parser's observable output; tests that
documented divergences known to flip under pyyaml have been updated to the
pyyaml-native expectations (and are annotated inline with the flip).

Covers:
- List handling (top-level and nested)
- Values containing colons
- Malformed / partial frontmatter
- Quoting edge cases
- Numeric edge cases
- Whitespace and indentation
- Deep nesting (now supported by pyyaml beyond the legacy 3-level limit)
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.calibration import parse_yaml_frontmatter, parse_value


# ── Lists (untested code path in the parser) ─────────────────


class TestListParsing:
    """The parser has branches for `- value` lines but no existing list tests."""

    def test_top_level_list(self):
        content = """---
items:
  - first
  - second
  - third
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result.get("items") == ["first", "second", "third"]

    def test_list_with_mixed_types(self):
        content = """---
mixed:
  - hello
  - 42
  - true
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["mixed"] == ["hello", 42, True]

    def test_empty_list_key(self):
        """A key with empty value resolves to None under pyyaml (YAML-native).

        Legacy parser returned ``[]`` because it defaulted empty-value keys
        to lists. pyyaml treats an empty value as null. Callers that need
        an iterable handle ``None`` defensively (see pattern_matcher.py).
        """
        content = """---
items:
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result.get("items") is None

    def test_nested_list_under_section(self):
        content = """---
section:
  items:
    - a
    - b
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["section"]["items"] == ["a", "b"]


# ── Values that contain colons ───────────────────────────────


class TestValuesWithColons:
    """Colons in values (URLs, times) must not break key:value splitting."""

    def test_url_as_value(self):
        content = """---
homepage: https://example.com/path
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["homepage"] == "https://example.com/path"

    def test_time_as_value(self):
        content = """---
start: 09:30
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["start"] == "09:30"

    def test_multiple_colons_in_value(self):
        """pyyaml's YAML 1.1 SafeLoader parses ``1:2:3`` as sexagesimal (3723).

        Legacy parser returned the literal string because it didn't implement
        YAML 1.1's base-60 shorthand. Any callers that need the string must
        quote the value explicitly in the source YAML (``ratio: "1:2:3"``).
        """
        content = """---
ratio: 1:2:3
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["ratio"] == 3723  # 1*3600 + 2*60 + 3
        quoted = parse_yaml_frontmatter('---\nratio: "1:2:3"\n---\nbody')
        assert quoted["ratio"] == "1:2:3"


# ── Malformed / partial frontmatter ──────────────────────────


class TestMalformedFrontmatter:
    """Parser must return {} or partial results without raising."""

    def test_only_opening_delimiter(self):
        assert parse_yaml_frontmatter("---\nkey: value\nmore: text") == {}

    def test_empty_frontmatter_block(self):
        assert parse_yaml_frontmatter("---\n---\nbody") == {}

    def test_frontmatter_with_only_whitespace(self):
        assert parse_yaml_frontmatter("---\n   \n\n---\nbody") == {}

    def test_delimiter_with_trailing_chars_rejected_by_pyyaml(self):
        """pyyaml rejects the mixed scalar+mapping document; we return ``{}``.

        The legacy parser permissively accepted ``---anything`` as an opener
        and salvaged any ``key: value`` lines that followed. pyyaml treats
        ``something\\nkey: v`` as a scalar-then-mapping conflict and raises
        ``YAMLError``; the wrapper catches and returns ``{}``. This is the
        deliberate behavioral change anticipated by the file header (issue #120).
        """
        result = parse_yaml_frontmatter("---something\nkey: v\n---\nbody")
        assert result == {}

    def test_raises_nothing_on_garbage(self):
        """Random binary-ish input should never raise."""
        garbage = "---\n\x00\x01\x02not yaml\n:::\n---\n"
        result = parse_yaml_frontmatter(garbage)
        assert isinstance(result, dict)


# ── Quoting and special characters ───────────────────────────


class TestQuotingEdgeCases:
    """parse_value strips outer single or double quotes but not both layers."""

    def test_double_quoted_value(self):
        assert parse_value('"hello world"') == "hello world"

    def test_single_quoted_value(self):
        assert parse_value("'hello world'") == "hello world"

    def test_mixed_quote_layers_only_outer_stripped(self):
        """Outer quotes are stripped; inner ones remain part of the string."""
        assert parse_value("\"it's here\"") == "it's here"

    def test_unquoted_preserved(self):
        assert parse_value("plain-value") == "plain-value"

    def test_empty_quoted_string(self):
        assert parse_value('""') == ""
        assert parse_value("''") == ""


# ── Numeric edge cases ───────────────────────────────────────


class TestNumericEdges:
    def test_zero(self):
        assert parse_value("0") == 0

    def test_negative_integer(self):
        """Negative integers match the float regex, not isdigit()."""
        assert parse_value("-42") == -42.0

    def test_large_integer(self):
        assert parse_value("1000000000") == 1000000000

    def test_float_without_leading_digit(self):
        """`.5` does not match the current regex — stays as string."""
        assert parse_value(".5") == ".5"

    def test_scientific_notation_kept_as_string(self):
        """Current parser does not support scientific notation."""
        assert parse_value("1e5") == "1e5"


# ── Whitespace and indentation ───────────────────────────────


class TestWhitespaceHandling:
    def test_blank_lines_ignored(self):
        content = """---

key1: value1


key2: value2

---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["key1"] == "value1"
        assert result["key2"] == "value2"

    def test_trailing_whitespace_stripped(self):
        content = "---\nkey: value   \n---\nbody"
        result = parse_yaml_frontmatter(content)
        assert result["key"] == "value"

    def test_comments_mid_section(self):
        content = """---
section:
  # inline comment
  key: value
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["section"]["key"] == "value"


# ── Depth beyond parser's stated limit ───────────────────────


class TestDepthAndStructure:
    """Legacy parser silently dropped content past 3 indent levels; pyyaml doesn't."""

    def test_invalid_indent_structure_rejected_by_pyyaml(self):
        """pyyaml raises on ``c: keep_me`` followed by a deeper-indented child.

        The legacy parser quietly dropped the over-indented ``d: drop_me``
        line and kept ``c`` as a scalar. pyyaml parses the over-indent as
        an attempted child of a scalar key and raises ``YAMLError``; the
        wrapper catches it and returns ``{}``. Valid YAML with clean nesting
        is preserved to arbitrary depth (see ``test_clean_four_level_nesting``).
        """
        content = """---
a:
  b:
    c: keep_me
      d: drop_me
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result == {}

    def test_clean_four_level_nesting(self):
        """pyyaml supports arbitrary depth when indentation is consistent.

        Legacy parser hard-limited to 3 levels. Not currently exercised by
        any production calibration file, but unlocks future schema growth.
        """
        content = """---
a:
  b:
    c:
      d: deep_value
---
body"""
        result = parse_yaml_frontmatter(content)
        assert result["a"]["b"]["c"]["d"] == "deep_value"


# ── Type coercion at load boundary ───────────────────────────


class TestValueCoercion:
    """parse_value is called on every non-list scalar — pin its coercion rules."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True),
            ("false", False),
            ("True", True),
            ("FALSE", False),
            ("yes", "yes"),  # only true/false coerce to bool
            ("no", "no"),
            ("null", "null"),  # no null coercion
        ],
    )
    def test_boolean_coercion_scope(self, raw, expected):
        assert parse_value(raw) == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

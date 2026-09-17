"""Tests for hooks/core/pre_tool_rules.py — the CommandRule infrastructure."""

import re
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.pre_tool_rules import (  # noqa: E402
    BASH_ANTI_PATTERN_RULES,
    CommandRule,
    RESTRICTED_NPM_RULES,
    evaluate_rules,
)


# ---------------------------------------------------------------------------
# CommandRule
# ---------------------------------------------------------------------------

class TestCommandRule:
    def test_match_returns_message_on_hit(self):
        rule = CommandRule(id="test", pattern=r"foo", message="found foo")
        assert rule.match("see foo here") == "found foo"

    def test_match_returns_none_on_miss(self):
        rule = CommandRule(id="test", pattern=r"foo", message="found foo")
        assert rule.match("no match") is None

    def test_flags_applied_at_match_time(self):
        rule = CommandRule(
            id="test",
            pattern=r"FOO",
            message="found foo",
            flags=re.IGNORECASE,
        )
        assert rule.match("foo") == "found foo"

    def test_default_flags_are_zero(self):
        rule = CommandRule(id="test", pattern=r"FOO", message="msg")
        assert rule.flags == 0
        assert rule.match("foo") is None

    def test_dataclass_is_frozen(self):
        rule = CommandRule(id="test", pattern=r"foo", message="msg")
        with pytest.raises((AttributeError, Exception)):  # FrozenInstanceError
            rule.id = "different"

    def test_id_is_required(self):
        # Construction without id should fail at TypeError time.
        with pytest.raises(TypeError):
            CommandRule(pattern="x", message="y")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# evaluate_rules
# ---------------------------------------------------------------------------

class TestEvaluateRules:
    def test_returns_first_match(self):
        rules = [
            CommandRule(id="a", pattern=r"foo", message="A msg"),
            CommandRule(id="b", pattern=r"foo", message="B msg"),
        ]
        result = evaluate_rules(rules, "foo bar")
        assert result is not None
        rule, msg = result
        assert rule.id == "a"
        assert msg == "A msg"

    def test_returns_none_when_no_rule_matches(self):
        rules = [CommandRule(id="a", pattern=r"foo", message="msg")]
        assert evaluate_rules(rules, "no match here") is None

    def test_empty_rules_returns_none(self):
        assert evaluate_rules([], "anything") is None

    def test_returns_tuple_with_rule_and_message(self):
        rules = [CommandRule(id="x", pattern=r"hello", message="greeting")]
        result = evaluate_rules(rules, "hello world")
        assert isinstance(result, tuple)
        assert len(result) == 2
        rule, msg = result
        assert isinstance(rule, CommandRule)
        assert isinstance(msg, str)

    def test_malformed_regex_is_skipped_not_propagated(self):
        # A rule with an invalid pattern (e.g. unbalanced bracket) must not
        # bring down the PreToolUse pipeline. Subsequent rules should still
        # be evaluated, and the loop falls through to None when nothing
        # else matches.
        rules = [
            CommandRule(id="bad", pattern=r"[unclosed", message="never seen"),
            CommandRule(id="good", pattern=r"hello", message="greeting"),
        ]
        result = evaluate_rules(rules, "hello world")
        assert result is not None
        assert result[0].id == "good"

    def test_only_malformed_regex_returns_none(self):
        rules = [CommandRule(id="bad", pattern=r"[unclosed", message="never seen")]
        assert evaluate_rules(rules, "hello world") is None


# ---------------------------------------------------------------------------
# Built-in rule lists — every rule must have a unique stable id
# ---------------------------------------------------------------------------

class TestBuiltinRuleLists:
    def test_bash_rule_ids_are_unique(self):
        ids = [r.id for r in BASH_ANTI_PATTERN_RULES]
        assert len(ids) == len(set(ids)), f"Duplicate ids: {ids}"

    def test_npm_rule_ids_are_unique(self):
        ids = [r.id for r in RESTRICTED_NPM_RULES]
        assert len(ids) == len(set(ids))

    def test_no_id_collision_between_rule_lists(self):
        bash = {r.id for r in BASH_ANTI_PATTERN_RULES}
        npm = {r.id for r in RESTRICTED_NPM_RULES}
        assert not (bash & npm), f"id collision: {bash & npm}"

    def test_every_rule_has_non_empty_message(self):
        for rule in BASH_ANTI_PATTERN_RULES + RESTRICTED_NPM_RULES:
            assert rule.message, f"empty message for {rule.id}"

    def test_all_patterns_compile(self):
        # If a regex is malformed, evaluate_rules would raise at runtime —
        # this test catches it at import-test time instead.
        for rule in BASH_ANTI_PATTERN_RULES + RESTRICTED_NPM_RULES:
            re.compile(rule.pattern)


# ---------------------------------------------------------------------------
# Behavior contract for the bash rules — preserves what the regex list
# used to enforce. This is essentially a regression contract: if any
# rule changes wording, these tests show what users will see.
# ---------------------------------------------------------------------------

class TestBashRuleBehavior:
    def test_cd_and_then_blocks(self):
        result = evaluate_rules(BASH_ANTI_PATTERN_RULES, "cd /tmp && ls")
        assert result is not None
        assert result[0].id == "shell-cd-then-run"

    def test_cat_blocks(self):
        result = evaluate_rules(BASH_ANTI_PATTERN_RULES, "cat /etc/hosts")
        assert result is not None
        assert result[0].id == "shell-read-via-cat"

    def test_grep_blocks(self):
        result = evaluate_rules(BASH_ANTI_PATTERN_RULES, "grep foo file")
        assert result[0].id == "shell-search-via-grep"

    def test_get_content_blocks_case_insensitive(self):
        result = evaluate_rules(BASH_ANTI_PATTERN_RULES, "powershell get-content x")
        assert result is not None
        assert result[0].id == "powershell-read-cmdlet"

    def test_obi_ephemeral_blocks(self):
        result = evaluate_rules(
            BASH_ANTI_PATTERN_RULES, "git add .obi/discovery-report.md"
        )
        assert result[0].id == "git-add-ephemeral-obi-artifact"

    def test_normal_commands_dont_match(self):
        for cmd in [
            "git -C /repo status",
            "git push origin main",
            "python -m pytest tests/",
            "npm install",
            "go build ./cmd/server",
            "docker compose up -d",
        ]:
            assert evaluate_rules(BASH_ANTI_PATTERN_RULES, cmd) is None, cmd


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

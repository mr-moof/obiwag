"""Tests for capability_claim_detector (WI-6)."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.capability_claim_detector import (  # noqa: E402
    detect_unverified_denials,
    format_capability_denial_nag,
)
from core.transcript_analyzer import get_recent_assistant_turns  # noqa: E402


def _turn(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestDetectUnverifiedDenials:
    def test_denial_without_tool_use_is_flagged(self):
        turns = [_turn("assistant", "I don't have write access to that directory.")]
        result = detect_unverified_denials(turns)
        assert len(result) == 1
        assert "write access" in result[0].lower()

    def test_denial_with_tool_use_block_is_not_flagged(self):
        content = (
            '<invoke name="Glob">\n<parameter name="pattern">**/*</parameter>\n'
            "</invoke>\n"
            "I don't have access to that file."
        )
        turns = [_turn("assistant", content)]
        assert detect_unverified_denials(turns) == []

    def test_denial_with_tool_name_json_is_not_flagged(self):
        content = (
            'Checking first: {"tool_name": "Read", "path": "/etc/hosts"}. '
            "I can't read that file."
        )
        turns = [_turn("assistant", content)]
        assert detect_unverified_denials(turns) == []

    def test_denial_inside_fenced_code_block_is_not_flagged(self):
        content = (
            "Here is a prior-session quote:\n"
            "```\n"
            "I don't have write access to that directory.\n"
            "```\n"
            "Continuing the work."
        )
        turns = [_turn("assistant", content)]
        assert detect_unverified_denials(turns) == []

    def test_denial_inside_inline_backticks_is_not_flagged(self):
        content = (
            "The phrase `I cannot write` is what we want the detector to catch. "
            "The rest of this turn is fine."
        )
        turns = [_turn("assistant", content)]
        assert detect_unverified_denials(turns) == []

    def test_denial_inside_blockquote_is_not_flagged(self):
        content = (
            "The prior session said:\n"
            "> I don't have access to that file.\n"
            "But we verified otherwise."
        )
        turns = [_turn("assistant", content)]
        assert detect_unverified_denials(turns) == []

    def test_paraphrased_denial_is_flagged(self):
        turns = [_turn(
            "assistant",
            "Sorry, that's outside what I can do here without a hand-off.",
        )]
        result = detect_unverified_denials(turns)
        assert len(result) == 1

    def test_no_denial_no_flag(self):
        turns = [_turn("assistant", "All done — tests pass and the file is committed.")]
        assert detect_unverified_denials(turns) == []

    def test_empty_input_no_flag(self):
        assert detect_unverified_denials([]) == []
        assert detect_unverified_denials([_turn("assistant", "")]) == []
        assert detect_unverified_denials([_turn("assistant", "   \n")]) == []

    def test_multiple_phrases_in_one_turn_count_once(self):
        content = (
            "I don't have write access. "
            "My tool is read-only. "
            "There is no access to that API."
        )
        turns = [_turn("assistant", content)]
        result = detect_unverified_denials(turns)
        # One incident per turn, not one per phrase.
        assert len(result) == 1

    def test_two_turns_each_with_denial_flag_twice(self):
        turns = [
            _turn("assistant", "I don't have access to that directory."),
            _turn("assistant", "I cannot reach the server."),
        ]
        assert len(detect_unverified_denials(turns)) == 2

    def test_turn_with_read_only_environment_claim_flagged(self):
        turns = [_turn("assistant", "My environment is read-only here.")]
        assert len(detect_unverified_denials(turns)) == 1

    def test_turn_with_that_is_not_possible_flagged(self):
        turns = [_turn("assistant", "That's not possible in this sandbox.")]
        assert len(detect_unverified_denials(turns)) == 1


class TestRoleFiltering:
    """Cross-cutting rule 3: detector only runs on assistant turns.

    The caller uses ``get_recent_assistant_turns``, which already role-filters.
    These tests confirm the utility delivers only assistant turns so a user
    message that quotes Claude verbatim cannot be flagged as Claude's denial.
    """

    def test_user_turn_with_denial_quote_is_not_returned(self):
        transcript = (
            '[{"role": "user", "content": "why did you say \'I don\'t have access\'?"},'
            '{"role": "assistant", "content": "Here is the fix."}]'
        )
        recent = get_recent_assistant_turns(transcript, n=2)
        assert len(recent) == 1
        assert recent[0]['role'] == 'assistant'
        assert detect_unverified_denials(recent) == []

    def test_slices_to_last_n_assistant_turns(self):
        transcript = (
            '[{"role": "assistant", "content": "turn one"},'
            '{"role": "user", "content": "ok"},'
            '{"role": "assistant", "content": "turn two"},'
            '{"role": "user", "content": "next"},'
            '{"role": "assistant", "content": "turn three"}]'
        )
        recent = get_recent_assistant_turns(transcript, n=2)
        assert len(recent) == 2
        assert recent[0]['content'] == 'turn two'
        assert recent[1]['content'] == 'turn three'


class TestFormatCapabilityDenialNag:
    def test_empty_list_returns_empty_string(self):
        assert format_capability_denial_nag([]) == ""

    def test_single_flagged_includes_prefix(self):
        msg = format_capability_denial_nag(["I don't have write access."])
        assert msg.startswith("[Deflection]")
        assert "1 recent turn" in msg
        assert '"I don\'t have write access."' in msg

    def test_multiple_flagged_cites_each(self):
        msg = format_capability_denial_nag([
            "I don't have write access.",
            "I cannot reach the server.",
        ])
        assert "2 recent turn" in msg
        assert "write access" in msg
        assert "reach the server" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

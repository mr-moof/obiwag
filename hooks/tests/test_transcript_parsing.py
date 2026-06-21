"""Edge-case tests for split_transcript_into_turns in stop.py.

Behavior pin — freezes the current splitter's observable output for cases not
covered by test_correction_detector.py. The splitter drives the memory
system's correction counting, so regressions are invisible without tests.

If stop.py is later split (see issue #125) and the function moves to
transcript_analyzer.py, these tests document the behavioral contract the
replacement must preserve.

Covers:
- Multiple turns in one JSON string
- Escaped quotes and Unicode in JSON content
- Text format with A:/Assistant: markers
- Mixed JSON and text formats
- Malformed JSON (truncated, missing fields)
- False-positive-prone content (JSON-like strings in plain text)
- Empty/whitespace content
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from stop import split_transcript_into_turns


# ── Multi-turn JSON ──────────────────────────────────────────


class TestMultiTurnJson:
    def test_three_turns_in_sequence(self):
        transcript = (
            '{"role": "human", "content": "first"}'
            '{"role": "assistant", "content": "second"}'
            '{"role": "human", "content": "third"}'
        )
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 3
        assert turns[0]["role"] == "human"
        assert turns[1]["role"] == "assistant"
        assert turns[2]["role"] == "human"
        assert turns[0]["content"] == "first"
        assert turns[1]["content"] == "second"
        assert turns[2]["content"] == "third"

    def test_role_before_content_required(self):
        """Current regex expects role to appear before content in the same object."""
        transcript = '{"content": "first", "role": "human"}'
        turns = split_transcript_into_turns(transcript)
        # The regex `"role"..."content"` does not match content-first ordering
        assert turns == []

    def test_extra_fields_between_role_and_content(self):
        """Regex uses [^}]* between role and content — tolerates intermediate fields."""
        transcript = '{"role": "human", "timestamp": 123, "content": "hello"}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["content"] == "hello"


# ── Special characters in content ────────────────────────────


class TestSpecialCharacters:
    def test_unicode_content_preserved(self):
        transcript = '{"role": "human", "content": "héllo wörld 日本語"}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["content"] == "héllo wörld 日本語"

    def test_embedded_escaped_quote_terminates_content(self):
        """Current regex uses `[^"]*` so an escaped \\" acts as a content terminator.

        Documents existing behavior — do not change the source to 'fix' this
        without reviewing callers first.
        """
        transcript = '{"role": "human", "content": "say \\"hi\\" to me"}'
        turns = split_transcript_into_turns(transcript)
        # Splitter sees content up to the first unescaped quote
        assert len(turns) >= 1
        assert turns[0]["role"] == "human"

    def test_content_with_colons_and_braces(self):
        transcript = '{"role": "human", "content": "use http://example.com for the API"}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["content"] == "use http://example.com for the API"


# ── Text format variations ───────────────────────────────────


class TestTextFormatVariations:
    def test_multiple_human_markers(self):
        transcript = (
            "Human: first question\n"
            "Assistant: first answer\n"
            "Human: follow up\n"
            "Assistant: second answer"
        )
        turns = split_transcript_into_turns(transcript)
        # Text format captures human turns; assistant turns are not yielded by current regex
        assert len(turns) >= 2
        assert all(t["role"] == "human" for t in turns)
        assert "first question" in turns[0]["content"]
        assert "follow up" in turns[1]["content"]

    def test_h_short_marker(self):
        transcript = "H: short marker question\nA: answer"
        turns = split_transcript_into_turns(transcript)
        assert len(turns) >= 1
        assert turns[0]["role"] == "human"
        assert "short marker question" in turns[0]["content"]

    def test_case_insensitive_markers(self):
        transcript = "HUMAN: upper case\nASSISTANT: reply"
        turns = split_transcript_into_turns(transcript)
        assert len(turns) >= 1
        assert turns[0]["role"] == "human"

    def test_user_marker(self):
        transcript = "User: a question\nAssistant: a reply"
        turns = split_transcript_into_turns(transcript)
        assert len(turns) >= 1
        assert turns[0]["role"] == "human"


# ── JSON-preferred-over-text precedence ──────────────────────


class TestFormatPrecedence:
    """If JSON matches anywhere, the text-format fallback is not attempted."""

    def test_json_wins_over_text(self):
        transcript = (
            '{"role": "human", "content": "json turn"}\n'
            "Human: text turn that should be ignored\n"
        )
        turns = split_transcript_into_turns(transcript)
        # JSON matched first, so text pattern is not applied
        assert len(turns) == 1
        assert turns[0]["content"] == "json turn"


# ── Malformed input ──────────────────────────────────────────


class TestMalformedInput:
    """Splitter must never raise on malformed input."""

    def test_truncated_json_no_crash(self):
        transcript = '{"role": "human", "content": "never cl'
        # Must not raise
        turns = split_transcript_into_turns(transcript)
        assert isinstance(turns, list)

    def test_no_role_field(self):
        transcript = '{"author": "human", "content": "no role"}'
        assert split_transcript_into_turns(transcript) == []

    def test_unsupported_role_value(self):
        """Roles other than human/user/assistant are dropped by the regex."""
        transcript = '{"role": "system", "content": "system message"}'
        assert split_transcript_into_turns(transcript) == []

    def test_binary_garbage(self):
        transcript = "\x00\x01\x02not a transcript\x03"
        assert split_transcript_into_turns(transcript) == []

    def test_very_long_input_no_crash(self):
        """A big but well-formed transcript must not raise."""
        transcript = '{"role": "human", "content": "' + ("x" * 50000) + '"}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert len(turns[0]["content"]) == 50000


# ── False-positive guards ────────────────────────────────────


class TestFalsePositiveGuards:
    def test_plain_text_mentioning_json_shape(self):
        """A plain description of JSON must not be parsed as JSON turns."""
        transcript = (
            "The API returns objects like role and content "
            "but this is prose, not a transcript."
        )
        assert split_transcript_into_turns(transcript) == []

    def test_code_block_with_role_keyword(self):
        transcript = 'def get_role():\n    return "role"\nHuman: now do it'
        turns = split_transcript_into_turns(transcript)
        # The text-format regex should still find the Human: marker
        assert any("now do it" in t["content"] for t in turns)

    def test_empty_content_field_yields_turn(self):
        transcript = '{"role": "human", "content": ""}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["content"] == ""


# ── Role normalization ───────────────────────────────────────


class TestRoleNormalization:
    @pytest.mark.parametrize("raw_role", ["human", "Human", "HUMAN", "user", "User", "USER"])
    def test_all_human_variants_normalize(self, raw_role):
        transcript = f'{{"role": "{raw_role}", "content": "x"}}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["role"] == "human"

    @pytest.mark.parametrize("raw_role", ["assistant", "Assistant", "ASSISTANT"])
    def test_assistant_variants_normalize(self, raw_role):
        transcript = f'{{"role": "{raw_role}", "content": "x"}}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["role"] == "assistant"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

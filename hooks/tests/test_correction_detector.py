"""Tests for correction detection in stop.py.

Covers:
- split_transcript_into_turns (JSON and text formats)
- detect_corrections_in_human_turns (pattern matching, filtering)
- analyze_transcript correction counting
- Issue 114: hook blocking messages must NOT count as corrections
"""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from stop import (
    split_transcript_into_turns,
    detect_corrections_in_human_turns,
    analyze_transcript,
)


# ── split_transcript_into_turns ──────────────────────────────


class TestSplitTranscriptIntoTurns:
    """Tests for turn-splitting logic."""

    def test_json_format_parses_human_and_assistant(self):
        transcript = (
            '{"role": "human", "content": "fix the bug"}'
            '{"role": "assistant", "content": "Sure, let me look."}'
        )
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 2
        assert turns[0]["role"] == "human"
        assert turns[1]["role"] == "assistant"

    def test_json_format_normalizes_user_to_human(self):
        transcript = '{"role": "user", "content": "hello"}'
        turns = split_transcript_into_turns(transcript)
        assert len(turns) == 1
        assert turns[0]["role"] == "human"

    def test_text_format_with_human_markers(self):
        transcript = "Human: Fix the tests\nAssistant: Working on it"
        turns = split_transcript_into_turns(transcript)
        assert len(turns) >= 1
        assert turns[0]["role"] == "human"

    def test_empty_string_returns_empty(self):
        turns = split_transcript_into_turns("")
        assert turns == []

    def test_no_markers_returns_empty(self):
        turns = split_transcript_into_turns("just some random text without markers")
        assert turns == []


# ── detect_corrections_in_human_turns ────────────────────────


class TestDetectCorrectionsInHumanTurns:
    """Tests for correction pattern matching."""

    def test_direct_negation_detected(self):
        turns = [{"role": "human", "content": "No, that's wrong. Use the wrapper."}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) >= 1
        assert corrections[0]["confidence"] >= 0.9

    def test_actually_comma_detected(self):
        turns = [{"role": "human", "content": "Actually, it should be the other flag."}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) >= 1
        assert corrections[0]["confidence"] >= 0.8

    def test_instead_use_detected(self):
        """The 'instead' pattern should match real corrections."""
        turns = [{"role": "human", "content": "Instead, use the wrapper module."}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) >= 1

    def test_api_hallucination_detected(self):
        turns = [{"role": "human", "content": "That API doesn't exist, you made it up."}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) >= 1
        assert any(c["correction_type"] == "api_hallucination" for c in corrections)

    def test_assistant_turns_ignored(self):
        turns = [{"role": "assistant", "content": "No, that's wrong, I'll fix it."}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) == 0

    def test_short_lines_skipped(self):
        turns = [{"role": "human", "content": "no"}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) == 0

    def test_long_content_skipped(self):
        turns = [{"role": "human", "content": "No, that's wrong. " + "x" * 2000}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) == 0

    def test_empty_turns_no_crash(self):
        corrections = detect_corrections_in_human_turns([])
        assert corrections == []

    def test_one_correction_per_line(self):
        """Multiple patterns on one line should only produce one correction."""
        turns = [{"role": "human", "content": "No, that's wrong. Actually, use something else instead of that."}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) == 1


# ── Issue 114: Hook block false positives ────────────────────


class TestHookBlockFalsePositives:
    """Issue 114: PreToolUse hook blocking messages must not be counted as corrections.

    These messages appear in <system-reminder> tags in the transcript.
    The fix calls strip_system_content() before turn splitting.
    """

    # Actual hook blocking messages from pre_tool_use.py
    HOOK_BLOCK_MESSAGES = [
        "Use absolute paths instead of 'cd && ...'. For git, use 'git -C <path>'.",
        "Use the Read tool instead of cat/head/tail.",
        "Use the Grep tool instead of grep/rg.",
        "Use the Glob tool instead of find.",
        "Use the Glob tool instead of piped ls.",
        "Use the Read/Grep tool instead of Get-Content/Select-String.",
        "Use the Write tool instead of echo/printf redirection.",
        "Use the Edit tool instead of sed/awk.",
    ]

    def _wrap_in_system_reminder(self, msg: str) -> str:
        return (
            f'<system-reminder>\n'
            f'PreToolUse:Bash hook blocking error from command: '
            f'"hook_wrapper.cmd" pre_tool_use": {msg}\n'
            f'</system-reminder>'
        )

    def test_single_hook_block_not_counted(self):
        """A transcript with one hook block should produce 0 corrections."""
        block = self._wrap_in_system_reminder(self.HOOK_BLOCK_MESSAGES[0])
        transcript = (
            '{"role": "human", "content": "run the tests"}\n'
            f'{block}\n'
            '{"role": "assistant", "content": "Let me fix that."}'
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == 0

    def test_multiple_hook_blocks_not_counted(self):
        """A transcript with many hook blocks should produce 0 corrections."""
        blocks = "\n".join(
            self._wrap_in_system_reminder(msg)
            for msg in self.HOOK_BLOCK_MESSAGES
        )
        transcript = (
            '{"role": "human", "content": "help me with the code"}\n'
            f'{blocks}\n'
            '{"role": "assistant", "content": "Sure."}'
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == 0

    def test_real_correction_still_detected_alongside_hook_blocks(self):
        """A real correction should still be detected even when hook blocks are present."""
        block = self._wrap_in_system_reminder(self.HOOK_BLOCK_MESSAGES[0])
        transcript = (
            '{"role": "human", "content": "No, that\'s wrong. Use the wrapper."}\n'
            f'{block}\n'
            '{"role": "assistant", "content": "Sorry, fixing now."}'
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == 1
        assert metrics["correction_details"][0]["confidence"] >= 0.9

    def test_hook_block_in_raw_text_not_counted(self):
        """Hook blocks outside JSON turns should also be stripped."""
        transcript = (
            '<system-reminder>\n'
            'PreToolUse:Bash hook blocking error: '
            'Use the Read tool instead of cat/head/tail.\n'
            '</system-reminder>\n'
            'Human: check the file\n'
        )
        metrics = analyze_transcript(transcript)
        # The human turn "check the file" doesn't match any correction pattern
        assert metrics["corrections"] == 0

    def test_assistant_acknowledgment_of_hook_block_not_counted(self):
        """If assistant echoes hook block text, it should not be a false positive."""
        transcript = (
            '{"role": "human", "content": "run the tests"}\n'
            '<system-reminder>PreToolUse:Bash hook blocking error: '
            'Use absolute paths instead of cd && ...</system-reminder>\n'
            '{"role": "assistant", "content": "Right, I need to use absolute paths instead of cd."}'
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == 0


# ── analyze_transcript integration ───────────────────────────


class TestAnalyzeTranscript:
    """Integration tests for analyze_transcript metrics."""

    def test_empty_transcript(self):
        metrics = analyze_transcript("")
        assert metrics["corrections"] == 0
        assert metrics["correction_details"] == []

    def test_clean_session_no_corrections(self):
        transcript = (
            '{"role": "human", "content": "please add a test"}\n'
            '{"role": "assistant", "content": "Done, here is the test."}'
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == 0

    def test_correction_count_matches_details(self):
        transcript = (
            '{"role": "human", "content": "No, that\'s wrong."}\n'
            '{"role": "human", "content": "Actually, it should be X."}\n'
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == len(metrics["correction_details"])
        assert metrics["corrections"] >= 2


# ── Issue #141: loaded-context false positives ───────────────


class TestLoadedContextFalsePositives:
    """Content loaded by the harness (memory, slash-command args, hook
    injections) must not register as user corrections.
    """

    def test_memory_content_with_instead_phrase_not_counted(self):
        """Auto-memory injection with ``instead of X, use Y`` text in a
        ``<system-reminder>`` block must not produce a correction, even when
        the reminder is embedded in a user turn (issue #141).
        """
        memory_block = (
            '<system-reminder>\n'
            '# auto memory\n'
            'For path handling on Windows, instead of os.path.join, use pathlib.Path.\n'
            'Do not commit, instead of direct pushes, use PR workflow.\n'
            '</system-reminder>\n'
        )
        turn_content = memory_block + 'please review the change'
        turns = [{'role': 'human', 'content': turn_content}]
        corrections = detect_corrections_in_human_turns(turns)
        assert corrections == []

    def test_command_args_with_instead_phrase_not_counted(self):
        """Slash-command args ``/cmd "X instead of Y"`` must not trip the
        correction detector. ``<command-args>`` is harness-framed text, not
        a user correction.
        """
        turn_content = (
            '<command-args>use the wrapper instead of direct calls</command-args>\n'
            'run the workflow'
        )
        turns = [{'role': 'human', 'content': turn_content}]
        corrections = detect_corrections_in_human_turns(turns)
        assert corrections == []

    def test_genuine_correction_still_counted(self):
        """The fix above must not suppress real user corrections — pattern
        match after stripping must still succeed on freshly-typed text.
        """
        turn_content = (
            'No, that is wrong. Instead, use the Foo API — the Bar one was deprecated.'
        )
        turns = [{'role': 'human', 'content': turn_content}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) >= 1

    def test_mixed_harness_and_real_correction(self):
        """Harness noise in the turn should not mask a real correction that
        follows it.
        """
        turn_content = (
            '<system-reminder>loaded note: instead of A, use B</system-reminder>\n'
            "No, that's wrong, use the wrapper instead."
        )
        turns = [{'role': 'human', 'content': turn_content}]
        corrections = detect_corrections_in_human_turns(turns)
        assert len(corrections) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

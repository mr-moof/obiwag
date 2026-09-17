"""Tests for hooks/core/stop_pipeline.py."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core import stop_pipeline as sp  # noqa: E402


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------

class TestAppendMessage:
    def test_both_none_returns_none(self):
        assert sp.append_message(None, None) is None

    def test_both_empty_returns_none(self):
        assert sp.append_message("", "") is None

    def test_only_existing(self):
        assert sp.append_message("hello", None) == "hello"
        assert sp.append_message("hello", "") == "hello"

    def test_only_addition(self):
        assert sp.append_message(None, "world") == "world"
        assert sp.append_message("", "world") == "world"

    def test_concatenates_with_newline(self):
        assert sp.append_message("a", "b") == "a\nb"

    def test_preserves_existing_newlines_in_addition(self):
        # Advisory messages embed their own leading newlines; we should not
        # double-add separators when the addition starts with one.
        result = sp.append_message("hello", "\n[Note] something")
        assert result == "hello\n\n[Note] something"


# ---------------------------------------------------------------------------
# load_transcript
# ---------------------------------------------------------------------------

class TestLoadTranscript:
    def test_inline_transcript_preferred_when_path_missing(self):
        assert sp.load_transcript({"transcript": "abc"}) == "abc"

    def test_transcript_path_read_from_disk(self, tmp_path):
        transcript_file = tmp_path / "tr.jsonl"
        transcript_file.write_text("line one\nline two", encoding="utf-8")
        result = sp.load_transcript({"transcript_path": str(transcript_file)})
        assert result == "line one\nline two"

    def test_path_takes_precedence_over_inline(self, tmp_path):
        # If both keys present, transcript_path is preferred (matches the
        # original main() ordering).
        transcript_file = tmp_path / "tr.jsonl"
        transcript_file.write_text("from-disk", encoding="utf-8")
        result = sp.load_transcript({
            "transcript_path": str(transcript_file),
            "transcript": "from-inline",
        })
        assert result == "from-disk"

    def test_missing_path_returns_empty_string(self, tmp_path):
        bogus = tmp_path / "does-not-exist.jsonl"
        assert sp.load_transcript({"transcript_path": str(bogus)}) == ""

    def test_no_keys_returns_empty(self):
        assert sp.load_transcript({}) == ""

    def test_inline_none_value_returns_empty(self):
        assert sp.load_transcript({"transcript": None}) == ""


# ---------------------------------------------------------------------------
# resolve_task_type
# ---------------------------------------------------------------------------

class TestResolveTaskType:
    def test_falls_back_when_no_state_and_no_prompt(self):
        assert sp.resolve_task_type({}, None) == 'unknown'

    def test_uses_session_state_when_available(self):
        state = MagicMock()
        state.get.return_value = 'widgetapi'
        assert sp.resolve_task_type({}, state) == 'widgetapi'

    def test_state_unknown_falls_back_to_prompt_detection(self):
        state = MagicMock()
        state.get.return_value = 'unknown'
        with patch("core.pattern_matcher.detect_task_type", return_value='canvasapi'):
            result = sp.resolve_task_type({"prompt": "canvasapi stuff"}, state)
        assert result == 'canvasapi'

    def test_state_none_uses_prompt_message_field(self):
        with patch("core.pattern_matcher.detect_task_type", return_value='debugging'):
            result = sp.resolve_task_type({"message": "debug this"}, None)
        assert result == 'debugging'

    def test_pattern_matcher_import_failure_falls_back(self):
        with patch.dict(sys.modules, {"core.pattern_matcher": None}):
            assert sp.resolve_task_type({"prompt": "x"}, None) == 'unknown'

    def test_custom_fallback_value(self):
        assert sp.resolve_task_type({}, None, fallback='custom') == 'custom'


# ---------------------------------------------------------------------------
# extract_tools_used
# ---------------------------------------------------------------------------

class TestExtractToolsUsed:
    def test_none_state_returns_empty(self):
        metrics = {}
        assert sp.extract_tools_used(metrics, None) == []
        assert metrics == {}

    def test_state_tool_count_supersedes_metrics(self):
        state = MagicMock()
        state.get.side_effect = lambda key, default=None: {
            'tool_count': 42,
            'tools_used': [{'tool': 'Read'}],
        }.get(key, default)
        metrics = {'tool_calls': 5}
        result = sp.extract_tools_used(metrics, state)
        assert metrics['tool_calls'] == 42
        assert result == [{'tool': 'Read'}]

    def test_zero_tool_count_does_not_overwrite(self):
        state = MagicMock()
        state.get.side_effect = lambda key, default=None: {
            'tool_count': 0,
            'tools_used': [],
        }.get(key, default)
        metrics = {'tool_calls': 5}
        sp.extract_tools_used(metrics, state)
        assert metrics['tool_calls'] == 5

    def test_missing_tools_used_returns_empty_list(self):
        state = MagicMock()
        state.get.side_effect = lambda key, default=None: {
            'tool_count': 1,
        }.get(key, default)
        assert sp.extract_tools_used({}, state) == []


# ---------------------------------------------------------------------------
# build_detailed_corrections
# ---------------------------------------------------------------------------

class TestBuildDetailedCorrections:
    def test_empty_metrics_returns_empty_list(self):
        assert sp.build_detailed_corrections({}) == []

    def test_uses_provided_correction_type(self):
        metrics = {"correction_details": [
            {"text": "hi", "correction_type": "over_building"},
        ]}
        result = sp.build_detailed_corrections(metrics)
        assert result == [{
            'type': 'over_building',
            'context': 'hi',
            'source_provided': None,
        }]

    def test_classifies_when_type_missing(self):
        metrics = {"correction_details": [{"text": "hallucinated foo"}]}
        with patch(
            "core.memory_reader.classify_correction",
            return_value='api_hallucination',
        ):
            result = sp.build_detailed_corrections(metrics)
        assert result[0]['type'] == 'api_hallucination'

    def test_truncates_context_to_100(self):
        long_text = 'x' * 250
        metrics = {"correction_details": [{
            "text": long_text, "correction_type": "general",
        }]}
        result = sp.build_detailed_corrections(metrics)
        assert len(result[0]['context']) == 100


# ---------------------------------------------------------------------------
# derive_outcome
# ---------------------------------------------------------------------------

class TestDeriveOutcome:
    def test_default_success(self):
        assert sp.derive_outcome({}) == 'success'

    def test_tests_failed_is_incomplete(self):
        assert sp.derive_outcome({"tests_passed": False}) == 'incomplete'

    def test_many_corrections_marks_challenging(self):
        assert sp.derive_outcome({"corrections": 4}) == 'challenging'

    def test_corrections_at_three_is_success(self):
        # Threshold is > 3, so 3 still maps to success.
        assert sp.derive_outcome({"corrections": 3}) == 'success'


# ---------------------------------------------------------------------------
# Best-effort wrappers — must not abort on internal failure
# ---------------------------------------------------------------------------

class TestNonCriticalPathSafety:
    def test_quality_signal_write_swallows_failure(self):
        with patch("core.quality_signals.compute_quality_signals",
                   side_effect=RuntimeError("boom")):
            # Should not raise.
            sp.run_quality_signal_write("sid", "tt", [], [])

    def test_calibration_update_swallows_failure(self):
        with patch("core.calibration.update_performance_metrics",
                   side_effect=RuntimeError("boom")):
            sp.update_calibration_metrics({"corrections": 1}, [], 'success')

    def test_evolution_proposal_swallows_failure(self):
        with patch("core.calibration.is_safety_enabled", return_value=True), \
             patch("core.session_summarizer.should_propose_evolution",
                   side_effect=RuntimeError("boom")):
            sp.run_evolution_proposal_if_due(True, "tt", {})

    def test_evolution_proposal_skipped_when_over_budget(self):
        with patch("core.calibration.is_safety_enabled", return_value=True), \
             patch("core.session_summarizer.should_propose_evolution") as proposer:
            sp.run_evolution_proposal_if_due(False, "tt", {})
        proposer.assert_not_called()

    def test_strike_counter_no_corrections_records_success(self):
        fake_counter = MagicMock()
        with patch("core.strike_counter.StrikeCounter", return_value=fake_counter):
            assert sp.run_strike_counter({"corrections": 0}, "sid") is None
        fake_counter.record_success.assert_called_once()

    def test_strike_counter_high_confidence_returns_checkpoint(self):
        fake_counter = MagicMock()
        fake_counter.record_failure.return_value = {
            'should_checkpoint': True,
            'checkpoint_message': 'STOP NOW',
        }
        metrics = {
            "corrections": 1,
            "correction_details": [
                {"correction_type": "x", "confidence": 0.9, "text": "bad"},
            ],
        }
        with patch("core.strike_counter.StrikeCounter", return_value=fake_counter):
            result = sp.run_strike_counter(metrics, "sid")
        assert result is not None
        assert "STOP NOW" in result

    def test_strike_counter_low_confidence_skipped(self):
        fake_counter = MagicMock()
        fake_counter.record_failure.return_value = {'should_checkpoint': False}
        metrics = {
            "corrections": 1,
            "correction_details": [
                {"correction_type": "x", "confidence": 0.5, "text": "weak"},
            ],
        }
        with patch("core.strike_counter.StrikeCounter", return_value=fake_counter):
            sp.run_strike_counter(metrics, "sid")
        fake_counter.record_failure.assert_not_called()


# ---------------------------------------------------------------------------
# write_session_outputs — corrections logging is best-effort per entry
# ---------------------------------------------------------------------------

class TestWriteSessionOutputs:
    def test_per_correction_failure_does_not_abort(self):
        with patch("core.memory_reader.write_session_summary"), \
             patch("core.memory_reader.log_correction",
                   side_effect=RuntimeError("boom")), \
             patch("core.calibration.is_safety_enabled", return_value=True):
            # Should not raise.
            sp.write_session_outputs(
                session_id="sid",
                task_type="tt",
                outcome="success",
                metrics={},
                summary_text="s",
                detailed_corrections=[
                    {"type": "x", "context": "y"},
                    {"type": "z", "context": "q"},
                ],
            )

    def test_disabled_logging_skips_log_correction(self):
        with patch("core.memory_reader.write_session_summary"), \
             patch("core.memory_reader.log_correction") as logger, \
             patch("core.calibration.is_safety_enabled", return_value=False):
            sp.write_session_outputs(
                session_id="sid",
                task_type="tt",
                outcome="success",
                metrics={},
                summary_text="s",
                detailed_corrections=[{"type": "x", "context": "y"}],
            )
        logger.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

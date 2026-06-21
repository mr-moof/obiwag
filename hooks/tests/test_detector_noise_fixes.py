"""Tests for the learning-detector noise fixes (detector-noise integration).

Covers four fixes that eliminated recurring false positives:

1. JSONL-aware turn parsing excludes tool_result/hook feedback from the
   correction scan (was: "Use the Glob tool instead of find" counted as a
   user correction -> inflated "High correction session").
2. High-correction WORKFLOW signal is no longer graduated as a learning.
3. reference_impl requires the success signal NEAR the integration keyword
   (was: skills-catalog "Analytics MCP servers" + unrelated "works" elsewhere).
4. Persistent suppression list drops dismissed (type, category) shapes.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.learning_types import Learning, LearningType
from core.transcript_analyzer import (
    analyze_transcript,
    extract_conversation_text,
    parse_jsonl_conversation,
)
from core.learning_vendor_detector import detect_reference_implementations
from core.learning_detector import detect_learnings
import core.learning_suppression as supp


def _jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records)


def _user_text(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _user_tool_result(text: str) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": text}],
        },
    }


def _assistant_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


# ── Fix #1: tool_result feedback excluded from corrections ───────────────────


class TestJsonlConversationParsing:
    def test_tool_result_not_a_human_turn(self):
        transcript = _jsonl(
            _user_text("reconnect"),
            _user_tool_result("Use the Glob tool instead of find."),
            _assistant_text("ok"),
        )
        turns = parse_jsonl_conversation(transcript)
        human = [t for t in turns if t["role"] == "human"]
        assert len(human) == 1
        assert human[0]["content"] == "reconnect"

    def test_hook_feedback_not_counted_as_correction(self):
        # 6 tool_result hook rejections + 1 benign user line -> would have been
        # 6 corrections under the old whole-transcript scan; must now be 0.
        transcript = _jsonl(
            _user_text("please refactor this"),
            *[_user_tool_result("Use the Glob tool instead of find.") for _ in range(6)],
            _assistant_text("done"),
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] == 0

    def test_genuine_user_correction_still_counted(self):
        transcript = _jsonl(
            _user_text("No, that's wrong. Use the wrapper instead."),
            _assistant_text("fixing"),
        )
        metrics = analyze_transcript(transcript)
        assert metrics["corrections"] >= 1

    def test_attachment_record_excluded(self):
        skills = (
            "The following skills are available for use with the Skill tool:\n"
            "- reviewing-code: Two-pass methodology. sql-safety: rules for Analytics MCP servers."
        )
        transcript = _jsonl(
            {"type": "attachment", "content": skills},
            _user_text("hello"),
        )
        convo = extract_conversation_text(transcript)
        assert "Analytics MCP servers" not in convo
        assert "hello" in convo

    def test_non_jsonl_returns_empty(self):
        assert parse_jsonl_conversation("just some prose, not jsonl") == []


# ── Fix #2: high-correction signal not graduated ─────────────────────────────


class TestWorkflowDemotion:
    def test_high_corrections_produce_no_workflow_learning(self):
        metrics = {"corrections": 20, "correction_details": []}
        learnings = detect_learnings(
            transcript="some assistant prose about work",
            metrics=metrics,
            correction_details=[],
            min_confidence=0.7,
        )
        assert not any(l.type == LearningType.WORKFLOW for l in learnings)


# ── Fix #3: reference_impl proximity ─────────────────────────────────────────


class TestReferenceImplProximity:
    def test_keyword_far_from_success_no_trigger(self):
        # Integration keyword and success signal >300 chars apart (skills-catalog
        # shape): keyword in a listing, "works" in unrelated prose far away.
        transcript = (
            "sql-safety: SQL query safety rules for Analytics MCP servers. "
            + ("filler narrative about invoices and circuits. " * 12)
            + "The deployment works correctly and is a working implementation."
        )
        result = detect_reference_implementations(transcript)
        assert all(r.metadata.get("category") != "mcp-integration" for r in result)

    def test_keyword_near_success_still_triggers(self):
        transcript = "The MCP server is now tested and verified and works correctly."
        result = detect_reference_implementations(transcript)
        assert any(r.metadata.get("category") == "mcp-integration" for r in result)


# ── Fix #4: suppression list ─────────────────────────────────────────────────


class TestSuppression:
    @pytest.fixture
    def tmp_suppression(self, tmp_path, monkeypatch):
        path = tmp_path / "suppressed-learnings.json"
        monkeypatch.setattr(supp, "get_suppression_path", lambda: str(path))
        return path

    def test_suppress_then_filter(self, tmp_suppression):
        l = Learning(
            type=LearningType.PATTERN,
            title="mcp-integration: reference implementation",
            content="x",
            target_file="docs/domain-patterns/mcp.md",
            metadata={"category": "mcp-integration", "detector": "reference_impl"},
        )
        assert supp.filter_suppressed([l]) == [l]  # not yet suppressed
        assert supp.suppress_learning(l) is True
        assert supp.filter_suppressed([l]) == []  # now dropped

    def test_suppress_key_roundtrip(self, tmp_suppression):
        assert supp.suppress_key("workflow", "") is True
        assert "workflow::" in supp.load_suppressed()

    def test_unrelated_category_not_suppressed(self, tmp_suppression):
        supp.suppress_key("pattern", "mcp-integration")
        other = Learning(
            type=LearningType.TECHNOLOGY,
            title="t",
            content="x",
            target_file="f",
            metadata={"category": "service-management"},
        )
        assert supp.filter_suppressed([other]) == [other]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

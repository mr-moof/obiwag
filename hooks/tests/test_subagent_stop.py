"""Tests for OPT-22 SubagentStop hook signal extraction and completion recording."""

import json
import os
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from subagent_stop import extract_signal, _append_completion, AGENT_PHASE_MAP


class TestExtractSignal:
    """Each canonical signal is correctly extracted from text."""

    def test_discovery_complete(self):
        sig, phase = extract_signal("Some preamble\n\nDISCOVERY COMPLETE")
        assert sig == "DISCOVERY COMPLETE"
        assert phase == 1

    def test_author_complete(self):
        sig, phase = extract_signal("Changes made.\n\nAUTHOR COMPLETE")
        assert sig == "AUTHOR COMPLETE"
        assert phase == 2

    def test_simplify_complete(self):
        sig, phase = extract_signal("SIMPLIFY COMPLETE")
        assert sig == "SIMPLIFY COMPLETE"
        assert phase == 3

    def test_simplify_skipped(self):
        sig, phase = extract_signal("No changes needed.\n\nSIMPLIFY SKIPPED")
        assert sig == "SIMPLIFY SKIPPED"
        assert phase == 3

    def test_review_complete_pass(self):
        sig, phase = extract_signal("All checks pass.\n\nREVIEW COMPLETE: PASS")
        assert sig == "REVIEW COMPLETE: PASS"
        assert phase == 4

    def test_review_complete_fail(self):
        sig, phase = extract_signal("REVIEW COMPLETE: FAIL 3 issues")
        assert sig == "REVIEW COMPLETE: FAIL"
        assert phase == 4

    def test_integrate_complete(self):
        sig, phase = extract_signal("INTEGRATE COMPLETE")
        assert sig == "INTEGRATE COMPLETE"
        assert phase == 5

    def test_rereview_complete(self):
        sig, phase = extract_signal("Verdict: PASS\n\nRE-REVIEW COMPLETE")
        assert sig == "RE-REVIEW COMPLETE"
        assert phase == 6

    def test_readme_complete(self):
        sig, phase = extract_signal("README COMPLETE")
        assert sig == "README COMPLETE"
        assert phase == 7

    def test_readme_skipped(self):
        sig, phase = extract_signal("README SKIPPED")
        assert sig == "README SKIPPED"
        assert phase == 7

    def test_readme_review_complete(self):
        sig, phase = extract_signal("Verdict: PASS\n\nREADME REVIEW COMPLETE")
        assert sig == "README REVIEW COMPLETE"
        assert phase == 8

    def test_release_gate_passed(self):
        sig, phase = extract_signal("RELEASE GATE PASSED")
        assert sig == "RELEASE GATE PASSED"
        assert phase == 9

    def test_release_gate_failed(self):
        sig, phase = extract_signal("RELEASE GATE FAILED: missing tests")
        assert sig == "RELEASE GATE FAILED"
        assert phase == 9

    def test_learning_captured(self):
        sig, phase = extract_signal("LEARNING CAPTURED")
        assert sig == "LEARNING CAPTURED"
        assert phase == 10

    def test_no_signal_returns_none(self):
        sig, phase = extract_signal("Just some regular text with no signals")
        assert sig is None
        assert phase is None

    def test_empty_text_returns_none(self):
        sig, phase = extract_signal("")
        assert sig is None
        assert phase is None

    def test_none_text_returns_none(self):
        sig, phase = extract_signal(None)
        assert sig is None
        assert phase is None

    def test_complete_with_concerns(self):
        sig, phase = extract_signal("COMPLETE_WITH_CONCERNS: some issue")
        assert sig == "COMPLETE_WITH_CONCERNS"
        assert phase is None  # needs agent_id resolution


class TestAppendCompletion:
    """dispatch-state.json completions[] is appended correctly."""

    def test_appends_to_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        ds_path = state_dir / "dispatch-state.json"
        ds_path.write_text(json.dumps({
            "schema_version": 1,
            "run_id": "test-run",
            "per_phase": {},
            "per_run": 0,
        }))

        assert _append_completion(1, "DISCOVERY COMPLETE", "obi-discovery", "last_assistant_message")

        data = json.loads(ds_path.read_text())
        assert len(data["completions"]) == 1
        rec = data["completions"][0]
        assert rec["phase"] == 1
        assert rec["signal"] == "DISCOVERY COMPLETE"
        assert rec["agent_id"] == "obi-discovery"
        assert rec["source"] == "last_assistant_message"
        assert "ts" in rec

    def test_appends_multiple_completions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        ds_path = state_dir / "dispatch-state.json"
        ds_path.write_text(json.dumps({"schema_version": 1, "run_id": "r"}))

        assert _append_completion(1, "DISCOVERY COMPLETE", "obi-discovery", "last_assistant_message")
        assert _append_completion(2, "AUTHOR COMPLETE", "obi-author", "last_assistant_message")

        data = json.loads(ds_path.read_text())
        assert len(data["completions"]) == 2
        assert data["completions"][0]["phase"] == 1
        assert data["completions"][1]["phase"] == 2

    def test_creates_completions_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        ds_path = state_dir / "dispatch-state.json"
        ds_path.write_text(json.dumps({"schema_version": 1}))

        _append_completion(3, "SIMPLIFY COMPLETE", "obi-simplify", "last_assistant_message")

        data = json.loads(ds_path.read_text())
        assert len(data["completions"]) == 1

    def test_graceful_on_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        # No dispatch-state.json — should not raise

        _append_completion(10, "LEARNING CAPTURED", "obi-learner", "last_assistant_message")

        ds_path = state_dir / "dispatch-state.json"
        data = json.loads(ds_path.read_text())
        assert len(data["completions"]) == 1

    def test_graceful_on_malformed_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        ds_path = state_dir / "dispatch-state.json"
        ds_path.write_text("not valid json {{{{")

        _append_completion(5, "INTEGRATE COMPLETE", "obi-integrator", "last_assistant_message")

        data = json.loads(ds_path.read_text())
        assert len(data["completions"]) == 1

    def test_preserves_existing_fields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        ds_path = state_dir / "dispatch-state.json"
        ds_path.write_text(json.dumps({
            "schema_version": 1,
            "run_id": "keep-me",
            "per_phase": {"1_Discovery": 1},
            "per_run": 2,
        }))

        _append_completion(2, "AUTHOR COMPLETE", "obi-author", "last_assistant_message")

        data = json.loads(ds_path.read_text())
        assert data["run_id"] == "keep-me"
        assert data["per_phase"] == {"1_Discovery": 1}
        assert data["per_run"] == 2
        assert len(data["completions"]) == 1

    def test_completion_history_is_bounded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".obi" / "state"
        state_dir.mkdir(parents=True)
        ds_path = state_dir / "dispatch-state.json"
        ds_path.write_text(json.dumps({
            "completions": [{"phase": 1, "signal": str(i)} for i in range(100)]
        }))

        assert _append_completion(2, "AUTHOR COMPLETE", "obi-author", "last_assistant_message")
        data = json.loads(ds_path.read_text())
        assert len(data["completions"]) == 100
        assert data["completions"][-1]["signal"] == "AUTHOR COMPLETE"
        assert not list(state_dir.glob("dispatch-state.json.*.tmp"))


class TestAgentPhaseMap:
    """Agent-to-phase mapping covers all 10 agents."""

    def test_all_agents_mapped(self):
        assert len(AGENT_PHASE_MAP) == 10
        phases = sorted(AGENT_PHASE_MAP.values())
        assert phases == list(range(1, 11))

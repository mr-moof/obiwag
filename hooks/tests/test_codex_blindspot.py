"""Acceptance tests for hooks/core/codex_blindspot.py -- threshold-triggered
blind-spot learnings.

Headline flow:
  seed 3 catches of one category -> run check -> 1 pending learning
  -> reject (suppress) -> re-run -> no regeneration
  -> 4th catch after rejection -> still no re-propose (needs N new)
  -> 3 MORE catches after rejection (N new = 3) -> re-propose
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks/ to sys.path for core.* imports.
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.codex_blindspot import (
    check_blindspot_thresholds,
    count_confirmed_by_category,
)
from core.learning_suppression import (
    suppress_key_with_metadata,
    load_suppression_metadata,
    load_suppressed,
    _key,
)
from core.learning_types import Learning, LearningType


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_catch(
    category="missed-edge-case",
    severity="high",
    disputed=False,
    dispute_resolution=None,
    ts=None,
    ref="TEST-1",
    phase="review",
):
    return {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "repo": "test-repo",
        "ref": ref,
        "phase": phase,
        "category": category,
        "severity": severity,
        "summary": f"Test catch for {category}",
        "disputed": disputed,
        "dispute_resolution": dispute_resolution,
    }


def _write_catches(path: Path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _append_catches(path: Path, entries):
    """Append new catch entries to an existing JSONL file."""
    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _read_pending(path: Path):
    if not path.exists():
        return {"learnings": []}
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCountConfirmedByCategory:
    def test_empty_file(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        counts = count_confirmed_by_category(catches_path)
        assert len(counts) == 0

    def test_counts_confirmed_only(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="T-1", disputed=False),
            _make_catch(ref="T-2", disputed=True, dispute_resolution="codex-right"),
            _make_catch(ref="T-3", disputed=True, dispute_resolution="claude-right"),
        ])
        counts = count_confirmed_by_category(catches_path)
        assert counts["missed-edge-case"] == 2  # T-1 + T-2

    def test_deduplicates_by_ref_category(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="T-1", category="test-gap"),
            _make_catch(ref="T-1", category="test-gap"),  # duplicate
        ])
        counts = count_confirmed_by_category(catches_path)
        assert counts["test-gap"] == 1


class TestBlindspotThresholdCheck:
    """Core acceptance tests for the threshold -> pending-learning flow."""

    def _setup_env(self, tmp_path):
        """Set up isolated catches, pending, and suppression paths."""
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        pending_path = tmp_path / "obi" / "pending-learnings.json"
        suppression_path = str(tmp_path / "obi" / "suppressed-learnings.json")
        return catches_path, pending_path, suppression_path

    def test_below_threshold_no_learning(self, tmp_path):
        """2 catches < threshold 3: no learning generated."""
        catches_path, pending_path, sup_path = self._setup_env(tmp_path)
        _write_catches(catches_path, [
            _make_catch(ref="T-1"),
            _make_catch(ref="T-2"),
        ])
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=set()), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert result == []
        assert not pending_path.exists()

    def test_threshold_reached_generates_one_learning(self, tmp_path):
        """3 catches of one category -> exactly 1 pending learning."""
        catches_path, pending_path, sup_path = self._setup_env(tmp_path)
        _write_catches(catches_path, [
            _make_catch(ref="T-1"),
            _make_catch(ref="T-2"),
            _make_catch(ref="T-3"),
        ])
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=set()), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )

        assert len(result) == 1
        learning = result[0]
        assert learning.type == LearningType.BLINDSPOT
        assert "missed-edge-case" in learning.title
        assert "3 confirmed catches" in learning.title
        assert learning.metadata["category"] == "missed-edge-case"
        assert learning.confidence == 0.8

        # Verify it was written to pending
        pending = _read_pending(pending_path)
        assert len(pending["learnings"]) == 1
        assert pending["learnings"][0]["type"] == "blindspot"

    def test_review_category_targets_skill(self, tmp_path):
        """Review-time categories target skills/reviewing-code/SKILL.md."""
        catches_path, pending_path, _ = self._setup_env(tmp_path)
        _write_catches(catches_path, [
            _make_catch(ref="T-1", category="test-gap"),
            _make_catch(ref="T-2", category="test-gap"),
            _make_catch(ref="T-3", category="test-gap"),
        ])
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=set()), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert len(result) == 1
        assert result[0].target_file == "skills/reviewing-code/SKILL.md"
        assert "test coverage" in result[0].content.lower()

    def test_prevention_category_targets_discovery(self, tmp_path):
        """Prevention categories target discovery/author phase."""
        catches_path, pending_path, _ = self._setup_env(tmp_path)
        _write_catches(catches_path, [
            _make_catch(ref="T-1", category="invented-api"),
            _make_catch(ref="T-2", category="invented-api"),
            _make_catch(ref="T-3", category="invented-api"),
        ])
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=set()), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert len(result) == 1
        assert result[0].target_file == "phases/01-discovery/command.md"
        assert "api" in result[0].content.lower()

    def test_disabled_detector_skips_check(self, tmp_path):
        """enabled: false in calibration blocks the check."""
        catches_path, pending_path, _ = self._setup_env(tmp_path)
        _write_catches(catches_path, [
            _make_catch(ref="T-1"),
            _make_catch(ref="T-2"),
            _make_catch(ref="T-3"),
        ])
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": False, "threshold": 3}}
        }):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert result == []

    def test_configurable_threshold(self, tmp_path):
        """Custom threshold of 5: 3 catches don't trigger."""
        catches_path, pending_path, _ = self._setup_env(tmp_path)
        _write_catches(catches_path, [
            _make_catch(ref="T-1"),
            _make_catch(ref="T-2"),
            _make_catch(ref="T-3"),
        ])
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 5}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=set()), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert result == []


class TestBlindspotSuppression:
    """Suppression flow: reject -> no regeneration -> N new -> re-propose."""

    def test_headline_flow_seed_reject_suppress_no_repropose(self, tmp_path):
        """The full headline acceptance flow:

        1. Seed 3 catches -> 1 learning generated
        2. Suppress (reject) -> record metadata
        3. Re-run -> no regeneration
        4. Add 1 more catch (4 total, 1 new past rejection) -> no re-propose
        5. Add 2 more catches (6 total, 3 new past rejection) -> re-propose
        """
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        pending_path = tmp_path / "obi" / "pending-learnings.json"
        sup_path = tmp_path / "obi" / "suppressed-learnings.json"

        # --- Step 1: Seed 3 catches ---
        _write_catches(catches_path, [
            _make_catch(ref="T-1", category="missed-edge-case"),
            _make_catch(ref="T-2", category="missed-edge-case"),
            _make_catch(ref="T-3", category="missed-edge-case"),
        ])

        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=set()), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert len(result) == 1
        assert "missed-edge-case" in result[0].title

        # --- Step 2: Suppress (reject) with metadata ---
        sup_key = _key("blindspot", "missed-edge-case")
        with patch("core.learning_suppression.get_suppression_path", return_value=str(sup_path)):
            ok = suppress_key_with_metadata(
                "blindspot", "missed-edge-case",
                metadata={
                    "suppressed_at_count": 3,
                    "suppressed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        assert ok

        # Verify metadata was written
        with patch("core.learning_suppression.get_suppression_path", return_value=str(sup_path)):
            meta = load_suppression_metadata(sup_key)
        assert meta is not None
        assert meta["suppressed_at_count"] == 3

        # --- Step 3: Re-run with same 3 catches -> no regeneration ---
        # Load the actual suppression state from our test file
        with patch("core.learning_suppression.get_suppression_path", return_value=str(sup_path)):
            suppressed_keys = load_suppressed()
            sup_meta = load_suppression_metadata(sup_key)

        # Clear pending for clean re-run
        if pending_path.exists():
            pending_path.unlink()

        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=suppressed_keys), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=sup_meta):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert result == [], "Should not regenerate after suppression with same count"

        # --- Step 4: Add 1 more catch (4 total, 1 new past rejection) ---
        _append_catches(catches_path, [
            _make_catch(ref="T-4", category="missed-edge-case"),
        ])
        if pending_path.exists():
            pending_path.unlink()

        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=suppressed_keys), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=sup_meta):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert result == [], "1 new catch past rejection < threshold 3, should not re-propose"

        # --- Step 5: Add 2 more catches (6 total, 3 new past rejection) ---
        _append_catches(catches_path, [
            _make_catch(ref="T-5", category="missed-edge-case"),
            _make_catch(ref="T-6", category="missed-edge-case"),
        ])
        if pending_path.exists():
            pending_path.unlink()

        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value=suppressed_keys), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=sup_meta):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert len(result) == 1, "3 new catches past rejection should trigger re-propose"
        assert "6 confirmed catches" in result[0].title

    def test_suppressed_without_metadata_is_permanent(self, tmp_path):
        """A key in suppressed set with no metadata = permanent suppression."""
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        pending_path = tmp_path / "obi" / "pending-learnings.json"
        _write_catches(catches_path, [
            _make_catch(ref="T-1"),
            _make_catch(ref="T-2"),
            _make_catch(ref="T-3"),
        ])
        sup_key = _key("blindspot", "missed-edge-case")
        with patch("core.codex_blindspot.load_calibration", return_value={
            "detectors": {"codex_blindspot": {"enabled": True, "threshold": 3}}
        }), patch("core.codex_blindspot.load_suppressed", return_value={sup_key}), \
             patch("core.codex_blindspot.load_suppression_metadata", return_value=None):
            result = check_blindspot_thresholds(
                catches_path=catches_path, pending_path=pending_path,
            )
        assert result == [], "Suppressed without metadata = permanent"


class TestLearningTypeBlindspot:
    """Verify the BLINDSPOT enum works with existing serialization."""

    def test_enum_value(self):
        assert LearningType.BLINDSPOT.value == "blindspot"

    def test_roundtrip_serialization(self):
        from core.learning_detector import serialize_learnings, deserialize_learnings
        learning = Learning(
            type=LearningType.BLINDSPOT,
            title="Test blindspot",
            content="Test content",
            target_file="test.md",
            metadata={"category": "test-gap"},
        )
        serialized = serialize_learnings([learning])
        assert serialized[0]["type"] == "blindspot"
        deserialized = deserialize_learnings(serialized)
        assert len(deserialized) == 1
        assert deserialized[0].type == LearningType.BLINDSPOT

    def test_format_learnings_notification_handles_blindspot(self):
        from core.learning_detector import format_learnings_notification
        learning = Learning(
            type=LearningType.BLINDSPOT,
            title="Test blindspot",
            content="Test content",
            target_file="test.md",
        )
        msg = format_learnings_notification([learning])
        assert "blindspot" in msg
        assert "1 learning" in msg

    def test_format_learnings_summary_handles_blindspot(self):
        from core.learning_detector import format_learnings_summary
        learning = Learning(
            type=LearningType.BLINDSPOT,
            title="Test blindspot",
            content="Test content",
            target_file="test.md",
        )
        summary = format_learnings_summary([learning])
        # Falls back to .value.title() = "Blindspot"
        assert "Blindspot" in summary


class TestSuppressionMetadataExtension:
    """Verify the suppression store metadata extension is backward-compatible."""

    def test_suppress_key_still_works(self, tmp_path):
        """Original suppress_key API unchanged."""
        sup_path = str(tmp_path / "suppressed-learnings.json")
        with patch("core.learning_suppression.get_suppression_path", return_value=sup_path):
            from core.learning_suppression import suppress_key, load_suppressed
            ok = suppress_key("gotcha", "git")
            assert ok
            keys = load_suppressed()
            assert "gotcha::git" in keys

    def test_suppress_key_with_metadata(self, tmp_path):
        sup_path = str(tmp_path / "suppressed-learnings.json")
        with patch("core.learning_suppression.get_suppression_path", return_value=sup_path):
            ok = suppress_key_with_metadata(
                "blindspot", "test-gap",
                metadata={"suppressed_at_count": 5, "suppressed_at": "2026-06-17T00:00:00Z"},
            )
            assert ok
            keys = load_suppressed()
            assert "blindspot::test-gap" in keys
            meta = load_suppression_metadata("blindspot::test-gap")
            assert meta["suppressed_at_count"] == 5

    def test_backward_compat_old_format(self, tmp_path):
        """Old format (keys-only) still loads fine."""
        sup_path = tmp_path / "suppressed-learnings.json"
        sup_path.write_text(json.dumps({"keys": ["gotcha::git"]}))
        with patch("core.learning_suppression.get_suppression_path", return_value=str(sup_path)):
            keys = load_suppressed()
            assert "gotcha::git" in keys
            # metadata returns None for old-format entries
            meta = load_suppression_metadata("gotcha::git")
            assert meta is None

    def test_mixed_suppress_preserves_both(self, tmp_path):
        """Mixing suppress_key and suppress_key_with_metadata preserves all data."""
        sup_path = str(tmp_path / "suppressed-learnings.json")
        with patch("core.learning_suppression.get_suppression_path", return_value=sup_path):
            from core.learning_suppression import suppress_key
            suppress_key("gotcha", "git")
            suppress_key_with_metadata(
                "blindspot", "security",
                metadata={"suppressed_at_count": 3},
            )
            keys = load_suppressed()
            assert "gotcha::git" in keys
            assert "blindspot::security" in keys
            meta = load_suppression_metadata("blindspot::security")
            assert meta["suppressed_at_count"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

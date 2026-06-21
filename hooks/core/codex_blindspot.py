"""Codex blind-spot threshold detector.

Counts confirmed Codex catches per category from the per-repo catch log
(``.obi/codex-catches.jsonl``). When a category reaches N confirmed
catches (default 3, configurable via ``detectors.codex_blindspot.threshold``
in calibration), generates ONE pending learning into
``~/.claude/.obi/pending-learnings.json`` proposing a concrete
countermeasure.

The check runs at Phase 10 / ``/obi-memory-review`` time (NOT the Stop
hook) so it adds zero latency to normal hook execution.

Suppression: when a blindspot learning is rejected, the suppression
store records ``suppressed_at_count``. The category must accrue N NEW
catches past that count before re-proposing.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure hooks/ is on sys.path for core.* imports.
_THIS_DIR = Path(__file__).resolve().parent
_HOOKS_DIR = _THIS_DIR.parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from core.calibration import load_calibration
from core.learning_types import Learning, LearningType
from core.learning_detector import serialize_learnings
from core.learning_suppression import (
    _key,
    load_suppression_metadata,
    load_suppressed,
)
from core.paths import get_obi_root


# ---------------------------------------------------------------------------
# Category -> countermeasure mapping
# ---------------------------------------------------------------------------

# Review-time categories: propose a new line in the reviewing-code checklist
_REVIEW_CATEGORIES = {
    "missed-edge-case": {
        "target_file": "skills/reviewing-code/SKILL.md",
        "countermeasure": (
            "Add review checklist item: Check for edge cases in "
            "boundary conditions, empty inputs, and off-by-one scenarios "
            "(pattern: {count} confirmed Codex catches for missed-edge-case)."
        ),
    },
    "test-gap": {
        "target_file": "skills/reviewing-code/SKILL.md",
        "countermeasure": (
            "Add review checklist item: Verify test coverage for new code "
            "paths, especially error handling and boundary cases "
            "(pattern: {count} confirmed Codex catches for test-gap)."
        ),
    },
    "security": {
        "target_file": "skills/reviewing-code/SKILL.md",
        "countermeasure": (
            "Add review checklist item: Security review for input validation, "
            "credential handling, and injection vectors "
            "(pattern: {count} confirmed Codex catches for security)."
        ),
    },
    "regression-risk": {
        "target_file": "skills/reviewing-code/SKILL.md",
        "countermeasure": (
            "Add review checklist item: Check for regressions in "
            "adjacent functionality and callers of modified code "
            "(pattern: {count} confirmed Codex catches for regression-risk)."
        ),
    },
    "other": {
        "target_file": "skills/reviewing-code/SKILL.md",
        "countermeasure": (
            "Add review checklist item: General quality check "
            "(pattern: {count} confirmed Codex catches in 'other' category)."
        ),
    },
}

# Prevention categories: propose a discovery/author-phase reminder
_PREVENTION_CATEGORIES = {
    "invented-api": {
        "target_file": "phases/01-discovery/command.md",
        "countermeasure": (
            "Add discovery/author reminder: Verify API existence against "
            "actual documentation or repo evidence before using any "
            "endpoint, cmdlet, or SDK method "
            "(pattern: {count} confirmed Codex catches for invented-api)."
        ),
    },
    "doc-mismatch": {
        "target_file": "phases/01-discovery/command.md",
        "countermeasure": (
            "Add discovery reminder: Cross-check documentation claims "
            "against actual implementation before relying on docs "
            "(pattern: {count} confirmed Codex catches for doc-mismatch)."
        ),
    },
    "plan-gap": {
        "target_file": "phases/02-author/command.md",
        "countermeasure": (
            "Add author reminder: Validate plan completeness -- check "
            "that every acceptance criterion has a corresponding "
            "implementation path "
            "(pattern: {count} confirmed Codex catches for plan-gap)."
        ),
    },
}


def _get_countermeasure(category: str, count: int) -> Dict[str, str]:
    """Return the countermeasure dict for a category."""
    mapping = _REVIEW_CATEGORIES.get(category) or _PREVENTION_CATEGORIES.get(
        category
    )
    if not mapping:
        # Fall back to review-time for unknown categories
        mapping = _REVIEW_CATEGORIES["other"]

    return {
        "target_file": mapping["target_file"],
        "countermeasure": mapping["countermeasure"].format(count=count),
    }


# ---------------------------------------------------------------------------
# Catch reading and counting
# ---------------------------------------------------------------------------

def _read_catches(catches_path: Path) -> List[Dict[str, Any]]:
    """Read catch entries from the JSONL file. Tolerates missing/malformed."""
    entries: List[Dict[str, Any]] = []
    if not catches_path.exists():
        return entries
    try:
        with open(catches_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _is_confirmed(entry: Dict[str, Any]) -> bool:
    """A confirmed catch: not disputed, or disputed and codex-right."""
    if not entry.get("disputed", False):
        return True
    return entry.get("dispute_resolution") == "codex-right"


def _deduplicate(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate by (ref, category), keeping the last entry."""
    seen: Dict[tuple, int] = {}
    for idx, entry in enumerate(entries):
        key = (entry.get("ref", ""), entry.get("category", ""))
        seen[key] = idx
    return [entries[idx] for idx in sorted(seen.values())]


def count_confirmed_by_category(
    catches_path: Path,
) -> Counter:
    """Count confirmed catches per category from the catch log."""
    entries = _read_catches(catches_path)
    deduped = _deduplicate(entries)
    counts: Counter = Counter()
    for entry in deduped:
        if _is_confirmed(entry):
            counts[entry.get("category", "other")] += 1
    return counts


# ---------------------------------------------------------------------------
# Pending-learnings append (read-modify-write, no concurrency concern)
# ---------------------------------------------------------------------------

def _get_pending_path() -> Path:
    return get_obi_root() / ".obi" / "pending-learnings.json"


def _append_pending_learnings(learnings: List[Learning]) -> None:
    """Append blindspot learnings to pending-learnings.json.

    Uses read-modify-write since this runs only at Phase 10 time
    (sequential, no concurrency with the Stop hook).
    """
    path = _get_pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Any] = {
        "session_id": None,
        "timestamp": None,
        "learnings": [],
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if "learnings" not in existing:
                existing["learnings"] = []
        except (json.JSONDecodeError, OSError):
            existing = {
                "session_id": None,
                "timestamp": None,
                "learnings": [],
            }

    serialized = serialize_learnings(learnings)
    existing["learnings"].extend(serialized)
    existing["timestamp"] = datetime.now(timezone.utc).isoformat()

    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Threshold check (main entry point)
# ---------------------------------------------------------------------------

def check_blindspot_thresholds(
    repo_root: Optional[str] = None,
    catches_path: Optional[Path] = None,
    pending_path: Optional[Path] = None,
) -> List[Learning]:
    """Check if any category has reached the blindspot threshold.

    Reads calibration for ``detectors.codex_blindspot.threshold`` (default 3)
    and ``detectors.codex_blindspot.enabled`` (default True).

    For each category at or above the threshold, generates a pending
    learning unless suppressed. Suppression is count-based: a category
    must accrue N new catches past the ``suppressed_at_count`` before
    re-proposing.

    Returns the list of newly generated Learning objects.
    """
    calibration = load_calibration()
    detector_cfg = calibration.get("detectors", {}).get("codex_blindspot", {})
    if not detector_cfg.get("enabled", True):
        return []

    threshold = detector_cfg.get("threshold", 3)

    # Locate catches file
    if catches_path is None:
        if repo_root:
            catches_path = Path(repo_root) / ".obi" / "codex-catches.jsonl"
        else:
            # Default: use the repo root derived from this file's location
            tools_dir = Path(__file__).resolve().parent.parent.parent / "tools"
            repo = tools_dir.parent
            catches_path = repo / ".obi" / "codex-catches.jsonl"

    counts = count_confirmed_by_category(catches_path)
    suppressed_keys = load_suppressed()
    generated: List[Learning] = []

    for category, count in counts.items():
        if count < threshold:
            continue

        sup_key = _key("blindspot", category)

        # Check suppression with count-based comparison
        if sup_key in suppressed_keys:
            meta = load_suppression_metadata(sup_key)
            if meta:
                high_water = meta.get("suppressed_at_count", 0)
                new_since = count - high_water
                if new_since < threshold:
                    continue
            else:
                # Suppressed without metadata: permanent suppression
                continue

        # Generate one learning for this category
        cm = _get_countermeasure(category, count)
        learning = Learning(
            type=LearningType.BLINDSPOT,
            title=f"Codex blind spot: {category} ({count} confirmed catches)",
            content=cm["countermeasure"],
            target_file=cm["target_file"],
            context=(
                f"Threshold {threshold} reached for category '{category}' "
                f"with {count} confirmed Codex catches."
            ),
            confidence=0.8,
            metadata={"category": category, "confirmed_count": count},
        )
        generated.append(learning)

    # Append to pending learnings if any were generated
    if generated:
        if pending_path is not None:
            # Test override: write to custom path
            _append_pending_learnings_to(generated, pending_path)
        else:
            _append_pending_learnings(generated)

    return generated


def _append_pending_learnings_to(
    learnings: List[Learning], path: Path
) -> None:
    """Append blindspot learnings to a specific pending-learnings path.

    Used by tests to override the default global path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Any] = {
        "session_id": None,
        "timestamp": None,
        "learnings": [],
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if "learnings" not in existing:
                existing["learnings"] = []
        except (json.JSONDecodeError, OSError):
            existing = {
                "session_id": None,
                "timestamp": None,
                "learnings": [],
            }

    serialized = serialize_learnings(learnings)
    existing["learnings"].extend(serialized)
    existing["timestamp"] = datetime.now(timezone.utc).isoformat()

    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

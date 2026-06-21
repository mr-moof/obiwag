"""Shutdown advisories run by stop.py at session end.

Each advisory is best-effort: it returns ``None`` on exceptions so a
single failing advisory cannot abort session shutdown. The orchestrator
in ``stop_pipeline.run_shutdown_advisories`` chains them together using
``stop_pipeline.append_message``.

These were extracted out of ``stop_pipeline.py`` (graphify P1 refactor)
once that file crossed the 400-line soft limit. The advisory functions
form a coherent group — they all run during the post-summary "wind-down"
phase, share the same time-budget gating, and each defers to a different
``core.*`` module — so they live together in one module rather than
being scattered across several.
"""

import os
from typing import Optional

from core.stop_pipeline import append_message


# ---------------------------------------------------------------------------
# Individual advisory checks
# ---------------------------------------------------------------------------

def _drift_message() -> Optional[str]:
    """Drift detected since last sync? Suggest /obi-collect."""
    try:
        from core.drift_detector import detect_drift
        drift = detect_drift()
        if drift.get('total_drifted', 0) > 0:
            return (
                f"\n[Drift] {drift['total_drifted']} file(s) changed. "
                "Run /obi-collect to sync back to source."
            )
    except Exception:
        return None
    return None


def _project_memory_backup_message() -> Optional[str]:
    """Snapshot project memory to .obi/backups/project-memory/."""
    try:
        from core.project_memory import backup_project_memory
        if backup_project_memory():
            return "\n[Backup] Project memory snapshot saved."
    except Exception:
        return None
    return None


def _obi_state_backup_message() -> Optional[str]:
    """Snapshot calibration.md + pending-learnings.json to .obi/backups/obi-state/."""
    try:
        from core.obi_state_backup import backup_obi_state
        if backup_obi_state():
            return "\n[Backup] Obi state snapshot saved."
    except Exception:
        return None
    return None


def _drift_nag_message() -> Optional[str]:
    """If drift grew this session, emit the [Obi] nag (issue #138)."""
    try:
        from core.drift_nag import compute_drift_delta, format_drift_nag
        delta, _ = compute_drift_delta()
        if delta is not None and delta > 0:
            return "\n" + format_drift_nag(delta)
    except Exception:
        return None
    return None


def _dirty_session_nag_message(cwd: str) -> Optional[str]:
    """Working tree has uncommitted changes? Remind the user (WI-1a)."""
    try:
        from core.dirty_session import format_dirty_session_nag, get_dirty_file_list
        file_list = get_dirty_file_list(cwd)
        if file_list:
            return "\n" + format_dirty_session_nag(file_list)
    except Exception:
        return None
    return None


def _capability_claim_message(transcript_text: str) -> Optional[str]:
    """Detect "I can't / no access" claims without backing tool_use (WI-6)."""
    try:
        from core.calibration import (
            get_detector_config,
            increment_detector_firings,
            load_calibration as _load_cal,
        )
        from core.capability_claim_detector import (
            detect_unverified_denials,
            format_capability_denial_nag,
        )
        from core.transcript_analyzer import get_recent_assistant_turns
    except Exception:
        return None

    try:
        detector_cfg = get_detector_config('capability_claim')

        # Per-detector enable gate: distinct from the hook-wiring toggle in
        # `safety.capability_claim` (which only gates whether this function
        # is called at all). The calibration template documents these as
        # separate switches; honor the detector-level one here. (#149)
        if not detector_cfg.get('enabled', True):
            return None

        sessions_total = _load_cal().get('performance', {}).get('total_sessions', 0)

        recent = get_recent_assistant_turns(transcript_text, n=2)
        flagged = detect_unverified_denials(recent)
        firings_total = detector_cfg.get('firings_total', 0)

        message = ""
        if flagged:
            message += "\n" + format_capability_denial_nag(flagged)
            if increment_detector_firings('capability_claim', delta=1):
                firings_total += 1

        review_threshold_sessions = detector_cfg.get('review_after_sessions', 0)
        review_threshold_firings = detector_cfg.get('review_after_firings', 0)
        if (
            review_threshold_sessions and sessions_total >= review_threshold_sessions
        ) or (
            review_threshold_firings and firings_total >= review_threshold_firings
        ):
            message += (
                f"\n[Review] Detector capability_claim due for review: "
                f"{sessions_total} sessions, {firings_total} firings. "
                "Assess recent firings and decide keep / tune / delete."
            )
        return message or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_shutdown_advisories(
    time_budget_ok: bool,
    transcript_text: str,
    cwd: Optional[str] = None,
) -> Optional[str]:
    """Run remaining non-registry shutdown advisories.

    Returns a combined system-message string (or ``None`` if no advisory
    fired). Each individual check swallows its own exceptions; one failing
    advisory cannot prevent another from running.

    Migrated to detector registry (OPT-15):
      - dirty_session (increment 1)
      - project_memory_backup, obi_state_backup, capability_claim (increment 2-5)
      - drift_detector, drift_nag (increment 6 — session_start/dual-invocation)
    These now run via run_stop_detectors() in stop.py before this function.
    """
    message: Optional[str] = None

    # drift_message: migrated to detector registry (OPT-15 increment 6).
    # drift_nag_message: migrated to detector registry (OPT-15 increment 6).
    # project_memory_backup: migrated to detector registry (OPT-15 increment 3).
    # obi_state_backup: migrated to detector registry (OPT-15 increment 4).
    # dirty_session: migrated to detector registry (OPT-15 increment 1).
    # capability_claim: migrated to detector registry (OPT-15 increment 5).

    return message


__all__ = [
    'run_shutdown_advisories',
]

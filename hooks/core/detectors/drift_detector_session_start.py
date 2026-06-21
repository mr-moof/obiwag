"""Drift-detector plugin for the session_start hook.

At session start (when daily maintenance is due), runs drift detection,
reports the count to the welcome message, and saves the drift baseline
for the session-end nag comparison (Issue #138).

The baseline save is bundled here (rather than a separate detector)
because the original code shares a single ``detect_drift()`` call for
both the message and the baseline -- splitting them would double the
file-hashing I/O.  All heavy imports are deferred to ``run()``.

Migrated from inline code in ``session_start._handle`` (OPT-15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class DriftDetectorSessionStart:
    """Report drift + save baseline at session start (maintenance-gated)."""

    @property
    def name(self) -> str:
        return 'drift_detector_session'

    @property
    def hook_point(self) -> str:
        return 'session_start'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # No calibration gate -- always enabled.  The maintenance-due
        # throttle is checked in run() via ctx.maintenance_due.
        return True

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        if not ctx.maintenance_due:
            return None

        from core.drift_detector import detect_drift

        drift = detect_drift()
        total = drift.get('total_drifted', 0)

        # Save baseline for the session-end drift nag (Issue #138).
        try:
            from core.drift_nag import save_drift_baseline
            save_drift_baseline(total)
        except Exception as exc:
            # Baseline save failure is non-critical -- matches original
            # log_swallowed("drift_baseline_save", exc) behavior.
            from core.hook_logger import log_swallowed
            log_swallowed("drift_baseline_save", exc)

        if total > 0:
            return (
                f"\n[Drift] {total} file(s) differ from source repo. "
                f"Run /obi-collect to sync back."
            )
        return None

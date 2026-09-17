"""Drift-detector plugin for the stop hook.

Wraps ``core.drift_detector.detect_drift`` behind the ``Detector``
protocol.  At session end, reports how many deployed files differ from
source.  All heavy imports are deferred to ``run()``.

Migrated from ``core.stop_advisories._drift_message`` (OPT-15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class DriftDetectorStop:
    """Report deployed-vs-source drift at session end."""

    @property
    def name(self) -> str:
        return 'drift_detector_stop'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # Drift detection has no calibration gate -- always enabled.
        return True

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        from core.drift_detector import detect_drift

        deadline = None
        if ctx.deadline_monotonic is not None:
            deadline = max(0.0, ctx.deadline_monotonic - 0.25)
        drift = (
            detect_drift(deadline_monotonic=deadline)
            if deadline is not None
            else detect_drift()
        )
        if drift.get('timed_out'):
            return "\n[Drift] Check deferred to healthcheck: Stop hook budget exhausted."
        if drift.get('total_drifted', 0) > 0:
            return (
                f"\n[Drift] {drift['total_drifted']} file(s) changed. "
                "Run /obi-collect to sync back to source."
            )
        return None

"""Drift-nag detector plugin for the stop hook (Issue #138).

Wraps ``core.drift_nag.compute_drift_delta`` /
``core.drift_nag.format_drift_nag`` behind the ``Detector`` protocol.
At session end, nags when drift grew during the session.
All heavy imports are deferred to ``run()``.

Migrated from ``core.stop_advisories._drift_nag_message`` (OPT-15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class DriftNagStop:
    """Nag when deployed-vs-source drift grew during this session."""

    @property
    def name(self) -> str:
        return 'drift_nag_stop'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # Drift nag has no calibration gate -- always enabled.
        return True

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        from core.drift_nag import compute_drift_delta, format_drift_nag

        delta, _ = compute_drift_delta()
        if delta is not None and delta > 0:
            return "\n" + format_drift_nag(delta)
        return None

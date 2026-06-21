"""Capability-claim detector plugin (WI-6).

Wraps the capability-claim detection logic from
``core.stop_advisories._capability_claim_message`` behind the
``Detector`` protocol so the registry can drive it.  All heavy imports
are deferred to ``run()``.

Two enablement gates are preserved:

1. ``safety.capability_claim`` — the hook-wiring toggle (checked in
   ``is_enabled``).
2. ``detectors.capability_claim.enabled`` — the per-detector enable gate
   (checked inside ``run()`` via ``get_detector_config``), distinct from
   the hook-wiring toggle (#149).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class CapabilityClaimDetector:
    """Detect unverified capability denials at session end."""

    @property
    def name(self) -> str:
        return 'capability_claim'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # Hook-wiring toggle: safety.capability_claim (default True).
        return calibration.get('safety', {}).get('capability_claim', True)

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
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

        detector_cfg = get_detector_config('capability_claim')

        # Per-detector enable gate (distinct from the hook-wiring toggle
        # checked in is_enabled). See #149.
        if not detector_cfg.get('enabled', True):
            return None

        sessions_total = _load_cal().get('performance', {}).get('total_sessions', 0)

        recent = get_recent_assistant_turns(ctx.transcript_text, n=2)
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

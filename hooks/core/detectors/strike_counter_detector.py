"""Strike-counter detector plugin (Three Strike Rule).

Wraps ``core.stop_pipeline.run_strike_counter`` behind the ``Detector``
protocol so the registry can drive it.  All heavy imports are deferred
to ``run()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class StrikeCounterDetector:
    """Track consecutive failures and emit checkpoint messages at session end."""

    @property
    def name(self) -> str:
        return 'strike_counter'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # Strike counter has no calibration gate — always enabled.
        return True

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        from core.stop_pipeline import run_strike_counter

        return run_strike_counter(ctx.metrics, ctx.session_id)

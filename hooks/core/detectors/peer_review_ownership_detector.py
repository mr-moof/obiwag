"""Stop-hook reminder for accepted peer reviews that remain unconsumed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class PeerReviewOwnershipDetector:
    """Keep durable peer-review run IDs visible to the primary orchestrator."""

    @property
    def name(self) -> str:
        return "peer_review_ownership"

    @property
    def hook_point(self) -> str:
        return "stop"

    @property
    def minimum_remaining_seconds(self) -> float:
        return 0.5

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        return True

    def run(self, ctx: "DetectorContext") -> Optional[str]:
        from core.peer_review_guard import active_peer_reviews

        deadline = ctx.deadline_monotonic
        if deadline is None:
            import time

            deadline = time.monotonic() + min(0.75, ctx.remaining_seconds())
        active = active_peer_reviews(ctx.cwd, deadline_monotonic=deadline)
        if not active:
            return None
        visible = ", ".join(
            f"{item['run_id']} ({item['provider']}/{item['transport_status']})"
            for item in active[:3]
        )
        extra = f" and {len(active) - 3} more" if len(active) > 3 else ""
        return (
            "\n[Peer review pending] Accepted run(s) still need a terminal result "
            f"or explicit cancellation: {visible}{extra}. Use peer-review status/wait/result/cancel; "
            "do not finish the task while the obligation is outstanding."
        )

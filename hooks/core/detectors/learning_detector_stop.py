"""Learning detector plugin for the stop hook.

Wraps ``stop.detect_and_prompt_learnings`` behind the ``Detector``
protocol so the registry can drive it.  The actual detection +
pending-learnings file write stays in ``stop.py`` to preserve the
mocking seam that ``tests/test_stop_learnings.py`` depends on
(``stop.get_pending_learnings_path``).

All heavy imports are deferred to ``run()`` / ``is_enabled()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class LearningDetector:
    """Detect session learnings and write pending-learnings at stop."""

    @property
    def name(self) -> str:
        return 'learning'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # Gate: is_safety_enabled('autonomous_learning')
        return calibration.get('safety', {}).get('autonomous_learning', True)

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        # Import from stop (not core.learning_detector) to preserve the
        # mocking seam: tests patch stop.get_pending_learnings_path and
        # that only works when detect_and_prompt_learnings is called from
        # the stop module's own reference.
        from stop import detect_and_prompt_learnings

        return detect_and_prompt_learnings(
            transcript_text=ctx.transcript_text,
            metrics=ctx.metrics,
            correction_details=ctx.metrics.get('correction_details', []) or [],
            session_id=ctx.session_id,
        )

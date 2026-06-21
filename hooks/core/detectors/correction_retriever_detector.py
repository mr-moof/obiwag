"""Correction-retriever detector plugin for the session_start hook.

Wraps ``core.correction_retriever.get_correction_injection`` behind the
``Detector`` protocol for RAG-style injection of relevant past
corrections into the session context.  All heavy imports are deferred
to ``run()``.

Migrated from inline code in ``session_start._handle`` (OPT-15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class CorrectionRetrieverDetector:
    """Inject relevant past corrections at session start."""

    @property
    def name(self) -> str:
        return 'correction_retriever'

    @property
    def hook_point(self) -> str:
        return 'session_start'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # Same gate as the original inline code: safety.auto_inject_sources.
        return calibration.get('safety', {}).get('auto_inject_sources', True)

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        if not ctx.task_text:
            return None

        from core.correction_retriever import get_correction_injection

        correction_injection = get_correction_injection(ctx.task_type, ctx.task_text)
        if correction_injection:
            return f"\n{correction_injection}"
        return None

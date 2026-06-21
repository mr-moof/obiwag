"""Dirty-session detector plugin (WI-1a).

Wraps ``core.dirty_session.get_dirty_file_list`` and
``format_dirty_session_nag`` behind the ``Detector`` protocol so the
registry can drive it.  All heavy imports are deferred to ``run()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class DirtySessionDetector:
    """Detect uncommitted changes at session end."""

    @property
    def name(self) -> str:
        return 'dirty_session'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        return calibration.get('safety', {}).get('dirty_session_nag', True)

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        from core.dirty_session import format_dirty_session_nag, get_dirty_file_list

        file_list = get_dirty_file_list(ctx.cwd)
        if file_list:
            return "\n" + format_dirty_session_nag(file_list)
        return None

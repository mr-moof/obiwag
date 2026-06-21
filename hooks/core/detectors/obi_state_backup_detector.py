"""Obi-state backup detector plugin.

Wraps ``core.obi_state_backup.backup_obi_state`` behind the
``Detector`` protocol so the registry can drive it.  All heavy imports
are deferred to ``run()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class ObiStateBackupDetector:
    """Snapshot calibration.md + pending-learnings.json at session end."""

    @property
    def name(self) -> str:
        return 'obi_state_backup'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # No calibration gate — always enabled (time-budget gating is
        # handled by the registry executor).
        return True

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        from core.obi_state_backup import backup_obi_state

        if backup_obi_state():
            return "\n[Backup] Obi state snapshot saved."
        return None

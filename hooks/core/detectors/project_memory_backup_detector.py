"""Project-memory backup detector plugin.

Wraps ``core.project_memory.backup_project_memory`` behind the
``Detector`` protocol so the registry can drive it.  All heavy imports
are deferred to ``run()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from core.detector_registry import DetectorContext


class ProjectMemoryBackupDetector:
    """Snapshot project memory to .obi/backups/project-memory/ at session end."""

    @property
    def name(self) -> str:
        return 'project_memory_backup'

    @property
    def hook_point(self) -> str:
        return 'stop'

    def is_enabled(self, calibration: Dict[str, Any]) -> bool:
        # No calibration gate — always enabled (time-budget gating is
        # handled by the registry executor).
        return True

    def run(self, ctx: 'DetectorContext') -> Optional[str]:
        from core.project_memory import backup_project_memory

        if backup_project_memory():
            return "\n[Backup] Project memory snapshot saved."
        return None

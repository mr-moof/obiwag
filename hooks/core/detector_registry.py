"""Detector plugin registry for Obi hook pipelines.

Provides:
  - ``Detector`` — a ``typing.Protocol`` that every detector plugin must
    satisfy (``name``, ``hook_point``, ``is_enabled``, ``run``).
  - ``DetectorContext`` — a shared dataclass carrying the inputs any
    detector may need.
  - ``run_detectors()`` — the executor that iterates a detector list in
    declared order, checks enablement + time budget, wraps each call in a
    ``HookTimer`` segment, and swallows exceptions via ``log_swallowed``.

Heavy imports (``HookTimer``, ``log_swallowed``) are deferred to first
use following the OPT-01 lazy-import pattern.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    runtime_checkable,
)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Detector(Protocol):
    """Protocol for detector plugins registered in the detector registry."""

    @property
    def name(self) -> str: ...

    @property
    def hook_point(self) -> Literal['stop', 'session_start']: ...

    def is_enabled(self, calibration: Dict[str, Any]) -> bool: ...

    def run(self, ctx: DetectorContext) -> Optional[str]: ...


# ---------------------------------------------------------------------------
# Context carrier
# ---------------------------------------------------------------------------

@dataclass
class DetectorContext:
    """Shared context passed to every detector's ``run()`` method.

    Built once at the top of the pipeline and passed to ``run_detectors``.
    Each detector picks what it needs from *ctx*. Fields are deliberately
    superset: increment 1 only populates what ``dirty_session`` needs, but
    the shape supports future detectors without a breaking change.
    """

    hook_point: str                              # 'stop' or 'session_start'
    cwd: str                                     # os.getcwd()
    start_time: float                            # for time-budget checks
    # Absolute monotonic soft deadline supplied by production hooks.  Tests and
    # older callers may omit it and retain the legacy wall-clock budget path.
    deadline_monotonic: Optional[float] = None
    calibration: Dict[str, Any] = field(default_factory=dict)
    transcript_text: str = ''
    metrics: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ''
    # session_start specific
    task_type: str = ''
    task_text: str = ''
    maintenance_due: bool = False              # True when daily maintenance is due

    def remaining_seconds(self, fallback_budget_seconds: float = 7.0) -> float:
        """Return usable hook time without mixing wall and monotonic clocks."""
        if self.deadline_monotonic is not None:
            return max(0.0, self.deadline_monotonic - time.monotonic())
        return max(0.0, fallback_budget_seconds - (time.time() - self.start_time))


# ---------------------------------------------------------------------------
# Registry lists — explicit, ordered
# ---------------------------------------------------------------------------

def _build_stop_detectors() -> List[Detector]:
    """Build the stop-detector list. Deferred to avoid import-time side effects.

    Order mirrors the original stop-hook output sequence:
      1. peer_review_ownership — never let an accepted broker run disappear
      2. learning              — autonomous learning via LearningDetector
      3. strike_counter        — was hand-wired in stop.py before the registry call
      4. dirty_session         — increment 1
      5. project_memory_backup — was in stop_advisories, after dirty_session
      6. obi_state_backup      — was in stop_advisories, after project_memory
      7. capability_claim      — was last in stop_advisories
      8. drift_detector_stop   — was _drift_message in stop_advisories
      9. drift_nag_stop        — was _drift_nag_message in stop_advisories
    """
    from core.detectors.capability_claim_detector import CapabilityClaimDetector
    from core.detectors.dirty_session_detector import DirtySessionDetector
    from core.detectors.drift_detector_stop import DriftDetectorStop
    from core.detectors.drift_nag_stop import DriftNagStop
    from core.detectors.learning_detector_stop import LearningDetector
    from core.detectors.obi_state_backup_detector import ObiStateBackupDetector
    from core.detectors.peer_review_ownership_detector import PeerReviewOwnershipDetector
    from core.detectors.project_memory_backup_detector import ProjectMemoryBackupDetector
    from core.detectors.strike_counter_detector import StrikeCounterDetector

    return [
        PeerReviewOwnershipDetector(),
        LearningDetector(),
        StrikeCounterDetector(),
        DirtySessionDetector(),
        ProjectMemoryBackupDetector(),
        ObiStateBackupDetector(),
        CapabilityClaimDetector(),
        DriftDetectorStop(),
        DriftNagStop(),
    ]


def _build_session_start_detectors() -> List[Detector]:
    """Build the session-start detector list.

    Session-start detectors do not infer a task from absent prompt text.
    """
    from core.detectors.drift_detector_session_start import DriftDetectorSessionStart

    return [
        DriftDetectorSessionStart(),
    ]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def run_detectors(
    detectors: List[Detector],
    ctx: DetectorContext,
    time_budget_seconds: float = 7.0,
) -> Optional[str]:
    """Execute *detectors* in declared order, accumulating messages.

    Per-detector wrapper:

    1. ``is_enabled(ctx.calibration)`` — skip silently if False.
    2. Time-budget check: ``(now - ctx.start_time) < time_budget_seconds``
       — emit ``[Skipped: <name>] Stop hook over time budget.`` if exceeded.
    3. Wrap in ``HookTimer("<HookPoint>/<name>")`` for per-detector timing.
    4. ``try: result = detector.run(ctx)`` /
       ``except Exception as exc: log_swallowed("detector_<name>", exc)``
    5. Accumulate non-None results via ``append_message``.

    Returns the combined message string, or ``None`` if nothing fired.
    """
    from core.hook_logger import HookTimer, log_swallowed
    from core.stop_pipeline import append_message

    message: Optional[str] = None

    # A pre-call threshold matters as much as the aggregate deadline.  The old
    # gate allowed a 4-5 second drift scan to begin at 6.9s and overrun Claude's
    # 10 second Stop ceiling.  Values are conservative admission budgets, not
    # promises that callers may raise the outer hook timeout around.
    minimum_remaining = {
        'learning': 1.0,
        'strike_counter': 0.15,
        'peer_review_ownership': 0.5,
        'dirty_session': 4.5,
        'project_memory_backup': 3.0,
        'obi_state_backup': 0.5,
        'capability_claim': 0.5,
        'drift_detector_stop': 5.0,
        'drift_nag_stop': 5.0,
        'drift_detector_session': 1.5,
    }

    for detector in detectors:
        # 1. Enablement gate
        if not detector.is_enabled(ctx.calibration):
            continue

        # 2. Time-budget gate
        required = float(getattr(
            detector,
            'minimum_remaining_seconds',
            minimum_remaining.get(detector.name, 0.25),
        ))
        if ctx.remaining_seconds(time_budget_seconds) < required:
            message = append_message(
                message,
                f"\n[Skipped: {detector.name}] Stop hook over time budget.",
            )
            continue

        # 3 + 4. Timed execution with exception swallowing
        hook_prefix = 'SessionStart' if ctx.hook_point == 'session_start' else 'Stop'
        hook_label = f"{hook_prefix}/{detector.name}"
        try:
            with HookTimer(hook_label):
                result = detector.run(ctx)
        except Exception as exc:
            log_swallowed(f"detector_{detector.name}", exc)
            result = None

        # 5. Accumulate
        message = append_message(message, result)

    return message


# ---------------------------------------------------------------------------
# Convenience entry points
# ---------------------------------------------------------------------------

def run_stop_detectors(ctx: DetectorContext) -> Optional[str]:
    """Run all registered stop-hook detectors."""
    return run_detectors(_build_stop_detectors(), ctx)


def run_session_start_detectors(ctx: DetectorContext) -> Optional[str]:
    """Run all registered session-start detectors."""
    return run_detectors(_build_session_start_detectors(), ctx)

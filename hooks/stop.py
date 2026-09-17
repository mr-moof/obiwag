#!/usr/bin/env python3
"""Stop hook for Obi Memory System.

This hook runs when Claude Code session ends to:
1. Generate a session summary
2. Analyze corrections for learning opportunities
3. Propose calibration evolutions if warranted
4. Detect learnings and prompt for approval (autonomous learning)
5. Sync approved learnings to GitHub and auto-deploy

Pipeline steps live in ``core/stop_pipeline.py`` so each can be tested in
isolation.  ``main()`` here is thin orchestration.

Pending-learnings helpers remain on this module because tests patch
``stop.get_pending_learnings_path`` to intercept file writes.  Moving them
would break that mocking seam.  ``detect_and_prompt_learnings`` is also
kept here so the same mocking seam covers the autonomous-learning path
(LearningDetector imports it from this module).
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add hook root to path for imports
HOOK_ROOT = os.path.dirname(os.path.abspath(__file__))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)

from core.hook_error_handler import respond_with_error  # noqa: E402
from core.transcript_analyzer import (  # noqa: E402,F401
    analyze_transcript,
    detect_corrections_in_human_turns,
    split_transcript_into_turns,
)
from core.session_summarizer import (  # noqa: E402,F401
    generate_summary_text,
    should_propose_evolution,
)


# Claude's configured Stop ceiling is 10 seconds.  Keep a four-second margin
# for interpreter startup, imports, output serialization, and scheduler jitter.
STOP_SOFT_BUDGET_SECONDS = 6.0


def get_pending_learnings_path() -> str:
    """Get path to pending learnings file."""
    from core.paths import get_obi_root
    return str(get_obi_root() / ".obi" / "pending-learnings.json")


def write_pending_learnings(session_id: str, learnings: list) -> str:
    """Write pending learnings to file for later approval.

    Args:
        session_id: Current session ID
        learnings: List of Learning objects (serialized)

    Returns:
        Path to the written file
    """
    pending_path = get_pending_learnings_path()
    os.makedirs(os.path.dirname(pending_path), exist_ok=True)

    data = {
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "learnings": learnings,
    }

    with open(pending_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    return pending_path


def detect_and_prompt_learnings(
    transcript_text: str,
    metrics: Dict[str, Any],
    correction_details: List[Dict[str, Any]],
    session_id: str
) -> Optional[str]:
    """Detect learnings and return a prompt message if any found.

    This is the main entry point for the autonomous learning system.

    Args:
        transcript_text: Full session transcript
        metrics: Session metrics dict
        correction_details: List of correction details
        session_id: Current session ID

    Returns:
        System message with learning prompt, or None if no learnings
    """
    try:
        from core.learning_detector import (
            detect_learnings,
            format_learnings_notification,
            serialize_learnings,
        )
    except ImportError:
        # Learning detector not available
        return None

    learnings = detect_learnings(
        transcript=transcript_text,
        metrics=metrics,
        correction_details=correction_details,
        min_confidence=0.7
    )

    if not learnings:
        return None

    serialized = serialize_learnings(learnings)
    write_pending_learnings(session_id, serialized)

    return format_learnings_notification(learnings)


def main():
    """Main entry point for Stop hook — thin orchestration over stop_pipeline."""
    hook_started_monotonic = time.monotonic()
    hook_deadline_monotonic = hook_started_monotonic + STOP_SOFT_BUDGET_SECONDS
    from core.worker_guard import exit_if_worker
    exit_if_worker()  # OPT-23: no-op inside a headless worker subprocess
    from core.hook_logger import HookTimer
    from core import stop_pipeline as sp

    with HookTimer("Stop") as timer:
        try:
            from core.hook_runtime import read_hook_input

            input_data: Dict[str, Any] = read_hook_input(sys.stdin)

            timer.set_input_summary(
                f"has_transcript={bool(input_data.get('transcript') or input_data.get('transcript_path'))}"
            )

            # issue #200 §3: unresolved tool-result-drop gate. Runs BEFORE the
            # summary-enabled gate below — it is a safety mechanism, not logging.
            # Fully guarded: a drop_guard fault can never prevent a normal stop, and
            # the retry counter inside bounds blocking to _RETRY_CAP then fails loud.
            with HookTimer("Stop/transcript_load"):
                transcript_text = sp.load_transcript(input_data)

            with HookTimer("Stop/drop_guard"):
                try:
                    from core import drop_guard
                    drop_out = drop_guard.evaluate_stop_block(
                        input_data=input_data,
                        transcript_text=transcript_text,
                        session_id=input_data.get('session_id'),
                        cwd=os.getcwd(),
                    )
                except Exception as exc:
                    from core.hook_logger import log_swallowed
                    log_swallowed('stop_drop_guard', exc)
                    drop_out = {
                        "systemMessage": (
                            "Tool-result recovery guard failed.\n"
                            "Stop was allowed to avoid an unbounded retry loop.\n"
                            "Verify the last tool result; diagnostics were logged."
                        )
                    }
            if drop_out is not None:
                timer.set_output_summary(
                    "drop-guard: "
                    + ("block" if drop_out.get("decision") == "block" else "fail-loud")
                )
                print(json.dumps(drop_out), file=sys.stdout)
                sys.exit(0)

            from core.calibration import is_safety_enabled
            from core.memory_reader import generate_session_id
            from core.session_state import cleanup_old_sessions, clear_current_session, get_session_state

            if not is_safety_enabled('generate_summaries'):
                timer.set_output_summary("summary generation disabled")
                print(json.dumps({}), file=sys.stdout)
                sys.exit(0)

            with HookTimer("Stop/transcript_analysis"):
                metrics = analyze_transcript(transcript_text)

            # Resolve THIS session (not the most recently modified file, which
            # could belong to a concurrent session) and snapshot everything we
            # need BEFORE deleting it. Reading after cleanup() reloaded defaults,
            # so a persisted task_type could never survive to the summary —
            # the second root cause of `task_type: unknown` (issue #202).
            from core.session_state import StateUnavailable, resolve_session_id
            session_state = get_session_state(resolve_session_id(input_data))
            try:
                snapshot = session_state.get_all() if session_state else None
            except StateUnavailable:
                # Busy lock: degrade to the transcript-derived values rather than
                # substituting defaults, and leave the file intact below.
                snapshot = None

            tools_used = sp.extract_tools_used(metrics, snapshot)
            task_type = sp.resolve_task_type(input_data, snapshot)

            # Only now is the state expendable -- and only if we actually read it.
            # Deleting after a FAILED snapshot would discard state we never
            # consumed: the lock could free up between the read attempt and the
            # delete, so the delete would succeed where the read did not. Leave it
            # for cleanup_old_sessions() instead.
            if session_state and snapshot is not None:
                session_state.cleanup()
            cleanup_old_sessions(max_age_hours=24)
            clear_current_session()  # session ending — drop the SessionStart sentinel (OPT-04 #177)
            outcome = sp.derive_outcome(metrics)
            session_id = generate_session_id()
            summary_text = generate_summary_text(task_type, outcome, metrics)
            detailed_corrections = sp.build_detailed_corrections(metrics)

            with HookTimer("Stop/session_outputs"):
                sp.write_session_outputs(
                    session_id=session_id,
                    task_type=task_type,
                    outcome=outcome,
                    metrics=metrics,
                    summary_text=summary_text,
                    detailed_corrections=detailed_corrections,
                )
                sp.run_quality_signal_write(session_id, task_type, tools_used, detailed_corrections)
                sp.update_calibration_metrics(metrics, detailed_corrections, outcome)

            # Non-critical work must fit an absolute budget.  Admission alone is
            # insufficient unless each slow detector also receives the deadline.
            time_budget_ok = time.monotonic() < (hook_deadline_monotonic - 0.5)

            sp.run_evolution_proposal_if_due(time_budget_ok, task_type, metrics)

            # autonomous_learning: migrated to detector registry (OPT-15 increment 8).
            # LearningDetector calls detect_and_prompt_learnings (this module)
            # preserving the mocking seam for test_stop_learnings.py.
            system_message: Optional[str] = None

            # strike_counter: migrated to detector registry (OPT-15 increment 2).
            # Runs via run_stop_detectors() below.

            # Registry-driven detectors (OPT-15: learning, strike_counter,
            # dirty_session, project_memory_backup, obi_state_backup,
            # capability_claim, drift_detector_stop, drift_nag_stop).
            from core.detector_registry import DetectorContext, run_stop_detectors
            from core.calibration import load_calibration

            det_ctx = DetectorContext(
                hook_point='stop',
                cwd=os.getcwd(),
                start_time=time.time(),
                deadline_monotonic=hook_deadline_monotonic,
                calibration=load_calibration(),
                transcript_text=transcript_text,
                metrics=metrics,
                session_id=session_id,
            )
            system_message = sp.append_message(
                system_message,
                run_stop_detectors(det_ctx),
            )

            timer.set_output_summary(
                f"summary written, corrections={metrics.get('corrections', 0)}"
            )

            output: Dict[str, Any] = {}
            if system_message:
                output["systemMessage"] = system_message
            print(json.dumps(output), file=sys.stdout)

        except Exception as e:
            timer.set_error(str(e))
            respond_with_error("stop", e, fallback={})

    sys.exit(0)


if __name__ == '__main__':
    main()

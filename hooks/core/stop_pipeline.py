"""Stop hook pipeline functions, extracted from stop.py.

Each pipeline step is a small function that stop.main() wires together.
The split makes regression-prone steps testable in isolation and shrinks
stop.main() to thin orchestration.

All advisory steps swallow non-critical exceptions internally so a
single failing advisory cannot abort session shutdown. Critical paths
(transcript loading, summary writing) raise; stop.main() routes those
through ``respond_with_error``.

The shutdown-advisory cluster (drift, backups, nags, capability claim)
lives in ``core.stop_advisories`` so that this module stays focused on
pipeline glue.
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.hook_logger import log_swallowed


# ---------------------------------------------------------------------------
# Plain helpers
# ---------------------------------------------------------------------------

def append_message(existing: Optional[str], addition: Optional[str]) -> Optional[str]:
    """Concatenate two optional messages, joining with a newline.

    - If both are empty, return None.
    - If only one has content, return that content.
    - Otherwise join with a single newline. Caller is responsible for
      any leading newline/separator inside ``addition`` if it wants
      a blank line between sections (the current advisories already
      embed leading newlines, so we keep the join minimal).
    """
    if not existing and not addition:
        return None
    if not addition:
        return existing
    if not existing:
        return addition
    return f"{existing}\n{addition}"


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

def load_transcript(input_data: Dict[str, Any]) -> str:
    """Load transcript text from input_data.

    Supports two shapes Claude Code may send:
    - ``{"transcript_path": "/some/path.jsonl"}`` — read file from disk
    - ``{"transcript": "...inline text..."}`` — use string directly

    Returns an empty string when neither key is present or the file
    cannot be read. Never raises — transcript loading is best-effort.
    """
    if 'transcript_path' in input_data:
        try:
            with open(input_data['transcript_path'], 'r', encoding='utf-8') as f:
                return f.read()
        except Exception:
            return ""
    if 'transcript' in input_data:
        return input_data.get('transcript') or ""
    return ""


def resolve_task_type(
    input_data: Dict[str, Any],
    session_state: Any,
    fallback: str = 'unknown',
) -> str:
    """Determine task type, preferring session state, then prompt detection.

    Args:
        input_data: stdin dict from Claude Code (may contain 'prompt' or 'message').
        session_state: SessionState or None — checked for ``task_type`` key.
        fallback: returned when neither state nor prompt yields a type.
    """
    if session_state is not None:
        state_type = session_state.get('task_type', fallback)
        if state_type and state_type != fallback:
            return state_type

    task_text = input_data.get('prompt', input_data.get('message', ''))
    if not task_text:
        return fallback

    try:
        from core.pattern_matcher import detect_task_type
    except Exception:
        return fallback
    return detect_task_type(task_text) or fallback


# ---------------------------------------------------------------------------
# Metrics + corrections
# ---------------------------------------------------------------------------

def extract_tools_used(metrics: Dict[str, Any], session_state: Any) -> List[Dict[str, Any]]:
    """Pull ``tools_used`` off session state and update metrics with state count.

    Mutates ``metrics`` in place: when session_state reports a tool count
    higher than the transcript-derived count, the more accurate state value
    wins. Returns the tools_used list (may be empty).
    """
    if session_state is None:
        return []
    state_tool_count = session_state.get('tool_count', 0)
    if state_tool_count > 0:
        metrics['tool_calls'] = state_tool_count
    return session_state.get('tools_used', []) or []


def build_detailed_corrections(metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert metrics['correction_details'] into the persisted shape.

    Each entry gets a ``type`` (existing or freshly classified), a
    truncated ``context`` for log readability, and ``source_provided`` set
    to None — the caller can refine that later.
    """
    from core.memory_reader import classify_correction

    detailed: List[Dict[str, Any]] = []
    for corr_detail in metrics.get('correction_details', []) or []:
        corr_text = corr_detail.get('text', '') or ''
        corr_type = corr_detail.get('correction_type')
        if not corr_type:
            corr_type = classify_correction(corr_text)
        detailed.append({
            'type': corr_type,
            'context': corr_text[:100],
            'source_provided': None,
        })
    return detailed


def derive_outcome(metrics: Dict[str, Any]) -> str:
    """Map metrics into the three-bucket outcome label."""
    if metrics.get('corrections', 0) > 3:
        return 'challenging'
    return 'success' if metrics.get('tests_passed', True) else 'incomplete'


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_session_outputs(
    session_id: str,
    task_type: str,
    outcome: str,
    metrics: Dict[str, Any],
    summary_text: str,
    detailed_corrections: List[Dict[str, Any]],
) -> None:
    """Write session summary and log corrections through memory_reader.

    ``write_session_summary`` is treated as critical: any exception it
    raises (import failure, disk error, malformed metrics) propagates up
    so ``stop.main()``'s top-level ``except Exception`` can route it
    through ``respond_with_error``. Correction logging is best-effort —
    a single bad ``log_correction`` entry is swallowed so subsequent
    corrections still get a chance to land.
    """
    from core.memory_reader import log_correction, write_session_summary
    from core.calibration import is_safety_enabled

    write_session_summary(
        session_id=session_id,
        task_type=task_type,
        outcome=outcome,
        metrics=metrics,
        sources_cited=metrics.get('sources_cited', []),
        corrections=detailed_corrections,
        learnings=[],
        summary_text=summary_text,
    )

    if is_safety_enabled('log_corrections') and detailed_corrections:
        for corr in detailed_corrections:
            try:
                log_correction(
                    session_id=session_id,
                    task_type=task_type,
                    correction_type=corr.get('type', 'unknown'),
                    user_message=corr.get('context', ''),
                    auto_classify=False,
                )
            except Exception as exc:
                log_swallowed("correction_log", exc)


def run_quality_signal_write(
    session_id: str,
    task_type: str,
    tools_used: List[Dict[str, Any]],
    detailed_corrections: List[Dict[str, Any]],
) -> None:
    """Compute quality signals and append to session-quality.jsonl. Best-effort."""
    try:
        from core.quality_signals import (
            compute_quality_signals,
            write_quality_signals_jsonl,
        )
        signals = compute_quality_signals(tools_used, detailed_corrections)
        write_quality_signals_jsonl(session_id, task_type, signals)
    except Exception as exc:
        log_swallowed("quality_signal_pipeline", exc)


def update_calibration_metrics(
    metrics: Dict[str, Any],
    detailed_corrections: List[Dict[str, Any]],
    outcome: str,
) -> None:
    """Update calibration performance counters. Best-effort."""
    try:
        from core.calibration import update_performance_metrics
        hallucination_count = sum(
            1 for c in detailed_corrections
            if c.get('type') == 'api_hallucination'
        )
        update_performance_metrics(
            sessions_delta=1,
            corrections_delta=metrics.get('corrections', 0),
            successful_delta=1 if outcome == 'success' else 0,
            hallucination_delta=hallucination_count,
        )
    except Exception as exc:
        log_swallowed("calibration_metrics", exc)


# ---------------------------------------------------------------------------
# Evolution proposal
# ---------------------------------------------------------------------------

def run_evolution_proposal_if_due(
    time_budget_ok: bool,
    task_type: str,
    metrics: Dict[str, Any],
) -> None:
    """Propose a calibration evolution if metrics warrant it. Best-effort."""
    if not time_budget_ok:
        return
    try:
        from core.calibration import is_safety_enabled
        from core.memory_reader import read_session_history, write_pending_evolution
        from core.session_summarizer import should_propose_evolution
    except Exception:
        return

    if not is_safety_enabled('propose_evolutions'):
        return

    try:
        history = read_session_history(limit=10)
        proposal = should_propose_evolution(task_type, metrics, history)
        if not proposal:
            return
        proposal_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}"[:6]
        write_pending_evolution(
            proposal_id=proposal_id,
            parameter=proposal['parameter'],
            current_value=proposal['current_value'],
            proposed_value=proposal['proposed_value'],
            evidence=proposal['evidence'],
            rationale=proposal['rationale'],
        )
    except Exception as exc:
        log_swallowed("evolution_proposal", exc)


# ---------------------------------------------------------------------------
# Strike counter
# ---------------------------------------------------------------------------

def run_strike_counter(
    metrics: Dict[str, Any],
    session_id: str,
) -> Optional[str]:
    """Track consecutive failures (Three Strike Rule). Returns checkpoint message or None."""
    try:
        from core.strike_counter import StrikeCounter
    except Exception:
        return None

    try:
        if metrics.get('corrections', 0) <= 0:
            StrikeCounter().record_success()
            return None

        counter = StrikeCounter()
        for corr in metrics.get('correction_details', []) or []:
            if corr.get('confidence', 0) < 0.8:
                continue
            issue_sig = corr.get('correction_type') or 'general_correction'
            result = counter.record_failure(issue_sig, {
                'error_message': corr.get('text', ''),
                'attempt_description': f'Session {session_id}',
            })
            if result.get('should_checkpoint'):
                msg = result.get('checkpoint_message')
                return f"\n\n{msg}" if msg else None
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Public surface (for stop.main() consumption)
#
# Shutdown advisories live in ``core.stop_advisories`` — import them directly
# from there. They are NOT re-exported here so the module boundary stays
# obvious in callers.
# ---------------------------------------------------------------------------

__all__ = [
    'append_message',
    'load_transcript',
    'resolve_task_type',
    'extract_tools_used',
    'build_detailed_corrections',
    'derive_outcome',
    'write_session_outputs',
    'run_quality_signal_write',
    'update_calibration_metrics',
    'run_evolution_proposal_if_due',
    'run_strike_counter',
]

"""Session summary text and calibration-evolution proposal logic.

Extracted from hooks/stop.py in (125). Responsible for:
- Rendering the human-readable session summary
- Deciding whether a calibration evolution should be proposed
  based on recent correction trends
"""


def generate_summary_text(task_type: str, outcome: str, metrics: dict) -> str:
    """Generate human-readable session summary text."""
    lines = [
        "# Session Summary",
        "",
        "## Outcome",
        f"Task type: {task_type}",
        f"Result: {outcome}",
        "",
        "## Metrics",
        f"- Tool calls: {metrics.get('tool_calls', 0)}",
        f"- Corrections received: {metrics.get('corrections', 0)}",
        f"- Files modified: {metrics.get('files_modified', 0)}",
        f"- Tests run: {'Yes' if metrics.get('tests_run') else 'No'}",
    ]

    if metrics.get('tests_run'):
        lines.append(f"- Tests passed: {'Yes' if metrics.get('tests_passed') else 'No'}")

    if metrics.get('sources_cited'):
        lines.append("")
        lines.append("## Sources Referenced")
        for source in metrics['sources_cited'][:10]:  # Limit to 10
            lines.append(f"- {source}")

    return "\n".join(lines)


def should_propose_evolution(task_type: str, metrics: dict, history: list) -> dict:
    """Determine if a calibration evolution should be proposed.

    Returns dict with proposal details or empty dict if no proposal.
    """
    # Only propose if multiple corrections in this session
    if metrics.get('corrections', 0) < 2:
        return {}

    # Check history for pattern
    recent_corrections = []
    for session in history[-5:]:  # Last 5 sessions
        if session.get('task_type') == task_type:
            session_corrections = session.get('metrics', {}).get('corrections', 0)
            recent_corrections.append(session_corrections)

    if len(recent_corrections) < 2:
        return {}

    avg_corrections = sum(recent_corrections) / len(recent_corrections)

    # If average corrections > 1.5, propose increasing budget
    if avg_corrections > 1.5:
        from core.calibration import get_verification_budget

        current_budget = get_verification_budget(task_type)
        proposed_budget = min(current_budget + 1, 5)  # Max 5

        if proposed_budget > current_budget:
            return {
                'parameter': f'verification.budgets_by_type.{task_type}',
                'current_value': current_budget,
                'proposed_value': proposed_budget,
                'evidence': {
                    'sessions_analyzed': len(recent_corrections),
                    'corrections_in_type': sum(recent_corrections),
                    'average_corrections': round(avg_corrections, 2),
                    'success_rate': 0.8,  # Placeholder
                    'confidence': 0.7,
                },
                'rationale': f"Analysis of {len(recent_corrections)} recent {task_type} sessions shows average {avg_corrections:.1f} corrections per session. Current budget of {current_budget} appears insufficient.",
            }

    return {}

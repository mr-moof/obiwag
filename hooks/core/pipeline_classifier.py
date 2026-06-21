"""Pipeline failure classifier for rigor=max Gate 4 (Iterate-Until-Green).

Tested separately from the orchestrator: pure functions that take CI log
text and return a structured classification. The actual fix-push-watch loop
is in obi-pipeline-monitor.md (read by the agent at runtime), but the
classifier rules live here so they can be unit-tested.

Five classes (mirrors the table in orchestration/obi-auto.md Gate 4 and
platforms/claude-code/agents/obi-pipeline-monitor.md):

    runner-unavailable   - "waiting for a runner" / no runner matched `runs-on:` OR queued >5m
    quota                - "spending limit" / Actions minutes exhausted
    yaml-error           - "Invalid workflow file" / workflow is not valid
    image-pull-failure   - "image not found" / "pull access denied" / "manifest unknown"
    script-error         - catch-all for any other non-zero exit
"""

import re
from typing import Dict, Optional


CLASSIFIER_RULES = [
    # (class_name, regex, action_hint)
    ('runner-unavailable',
     r'(waiting for a runner|no runner.*(label|available)|no hosted runner)',
     'No runner picked up the job. Check `runs-on:` labels in .github/workflows/*.yml; cross-ref tools/probes/runner_tags.ps1'),
    ('quota',
     r'(spending limit|exceeded.*minutes|usage limit|billing)',
     'Halt -> NEEDS USER INPUT: Actions minutes / spending limit exhausted'),
    ('yaml-error',
     r'(invalid workflow file|workflow is not valid|unexpected value)',
     "Fix .github/workflows/*.yml; validate locally (e.g. actionlint) or with `gh workflow view`"),
    ('image-pull-failure',
     r'(image.*not found|pull access denied|manifest unknown)',
     'Probe registry availability; suggest fallback image'),
]


def classify_failure(
    trace: str,
    pending_seconds: Optional[int] = None,
) -> Dict[str, str]:
    """Classify a CI failure log into one of five buckets.

    Args:
        trace: stdout/stderr from `gh run view --log-failed` or run status.
        pending_seconds: if the run is stuck queued (no runner picked it up),
            this is the seconds since creation. Used for runner detection when
            no log line directly mentions a runner.

    Returns:
        Dict with 'class' and 'action' keys. 'class' is one of:
        runner-unavailable | quota | yaml-error | image-pull-failure |
        script-error.
    """
    if trace is None:
        trace = ''

    text = trace.lower()

    for class_name, regex, action in CLASSIFIER_RULES:
        if re.search(regex, text, re.IGNORECASE | re.DOTALL):
            return {'class': class_name, 'action': action}

    # Queued too long with no runner is runner-unavailable even without a
    # direct log line.
    if pending_seconds is not None and pending_seconds > 300:
        return {
            'class': 'runner-unavailable',
            'action': (
                'Run queued >5min; no runner picked it up. '
                'Check `runs-on:` labels in .github/workflows/*.yml; cross-ref tools/probes/runner_tags.ps1'
            ),
        }

    return {
        'class': 'script-error',
        'action': 'Hand back to Author with last 50 lines of the log',
    }


def is_terminal_failure(class_name: str) -> bool:
    """Quota exhaustion is terminal; everything else is retryable."""
    return class_name == 'quota'


def same_classification(prev: Optional[str], curr: str) -> bool:
    """Two consecutive identical classifications hit the 3-strike checkpoint
    flow in hooks/core/strike_counter.py."""
    return prev is not None and prev == curr

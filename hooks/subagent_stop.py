#!/usr/bin/env python3
"""SubagentStop hook — record phase completion signals (OPT-22).

When a subagent finishes, Claude Code fires SubagentStop with the
agent's ``last_assistant_message``, ``agent_id``, and
``agent_transcript_path``.  This hook extracts the canonical phase
completion signal and appends it to ``dispatch-state.json`` so the
resume protocol can skip already-completed phases after a stall.

Advisory only — emits ``{}`` on success and on any failure so the
agent teardown is never blocked.
"""

import json
import os
import sys
from datetime import datetime

HOOK_ROOT = os.path.dirname(os.path.abspath(__file__))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)


# Canonical completion signals (discovery section 9).
# Order matters: longer/more-specific signals must precede shorter prefixes
# so "RELEASE GATE PASSED" matches before a hypothetical prefix.
PHASE_SIGNALS = [
    ("LEARNING CAPTURED",       10),
    ("RELEASE GATE PASSED",      9),
    ("RELEASE GATE FAILED",      9),
    ("README REVIEW COMPLETE",   8),
    ("README COMPLETE",          7),
    ("README SKIPPED",           7),
    ("RE-REVIEW COMPLETE",       6),
    ("INTEGRATE COMPLETE",       5),
    ("REVIEW COMPLETE: PASS",    4),
    ("REVIEW COMPLETE: FAIL",    4),
    ("REVIEW COMPLETE",          4),
    ("SIMPLIFY COMPLETE",        3),
    ("SIMPLIFY SKIPPED",         3),
    ("AUTHOR COMPLETE",          2),
    ("COMPLETE_WITH_CONCERNS",   None),  # phase from agent_id fallback
    ("DISCOVERY COMPLETE",       1),
]

# agent_id -> phase number (discovery section 9).
AGENT_PHASE_MAP = {
    "obi-discovery":      1,
    "obi-author":         2,
    "obi-simplify":       3,
    "obi-reviewer":       4,
    "obi-integrator":     5,
    "obi-rereviewer":     6,
    "obi-readme":         7,
    "obi-readme-verifier": 8,
    "obi-release-gate":   9,
    "obi-learner":       10,
}


def extract_signal(text):
    """Return ``(signal_str, phase_int)`` from text, or ``(None, None)``."""
    if not text:
        return None, None
    for signal, phase in PHASE_SIGNALS:
        if signal in text:
            return signal, phase
    return None, None


def _read_tail(path, max_bytes=8192):
    """Read up to the last *max_bytes* of a file. Returns str or None."""
    try:
        size = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read()
    except OSError:
        return None


def _append_completion(phase, signal, agent_id, source):
    """Append a completion record to dispatch-state.json.

    Read-modify-write with graceful handling of missing/malformed file.
    """
    ds_path = os.path.join('.obi', 'state', 'dispatch-state.json')
    ds = None
    try:
        with open(ds_path, 'r', encoding='utf-8') as f:
            ds = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    if not isinstance(ds, dict):
        ds = {}

    completions = ds.get('completions')
    if not isinstance(completions, list):
        completions = []

    completions.append({
        "ts": datetime.now().isoformat(),
        "agent_id": agent_id or "",
        "phase": phase,
        "signal": signal,
        "source": source,
    })
    ds['completions'] = completions

    try:
        with open(ds_path, 'w', encoding='utf-8') as f:
            json.dump(ds, f, indent=2)
    except OSError:
        pass  # best-effort


def _handle(input_data, timer):
    agent_id = input_data.get('agent_id', '')
    timer.set_input_summary(f"agent={agent_id}")

    # Primary source: last_assistant_message field
    last_msg = input_data.get('last_assistant_message', '')
    signal, phase = extract_signal(last_msg)
    source = 'last_assistant_message'

    # Fallback: tail of agent transcript
    if signal is None:
        transcript_path = input_data.get('agent_transcript_path', '')
        if transcript_path:
            tail = _read_tail(transcript_path)
            signal, phase = extract_signal(tail)
            source = 'agent_transcript_path'

    if signal is None:
        timer.set_output_summary("no signal detected")
        return {}

    # Resolve phase from agent_id if signal didn't carry one
    # (e.g. COMPLETE_WITH_CONCERNS uses agent_id mapping)
    if phase is None:
        phase = AGENT_PHASE_MAP.get(agent_id)
    if phase is None:
        timer.set_output_summary(f"signal={signal} but unknown phase")
        return {}

    _append_completion(phase, signal, agent_id, source)
    timer.set_output_summary(f"recorded: phase={phase} signal={signal}")
    return {}


def main():
    from core.worker_guard import exit_if_worker
    exit_if_worker()  # OPT-23: no-op inside a headless worker subprocess
    from core.hook_runtime import run_hook
    run_hook("SubagentStop", _handle, error_name="subagent_stop", fallback={})


if __name__ == '__main__':
    main()

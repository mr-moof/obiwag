# Phase State Files

This directory contains runtime state files created during `/obi-auto` autonomous workflow execution.

## File Naming

```
ralph-state.json           # Main loop state (current phase, iteration count)
phase-{N}-{name}.json      # Individual phase outputs
```

## Ralph State Schema

```json
{
  "task": "Description of the task",
  "started": "2026-01-27T10:00:00Z",
  "iteration": 1,
  "max_iterations": 25,
  "current_phase": "discovery",
  "phase_status": "pending|in_progress|complete|failed",
  "express_lane": false,
  "lines_changed": 0,
  "phase_history": [
    {"phase": "discovery", "status": "complete", "timestamp": "..."}
  ],
  "break_signal": null,
  "final_status": null
}
```

## Phase Output Schema

```json
{
  "phase": "author",
  "status": "complete",
  "timestamp": "2026-01-27T10:30:00Z",
  "promise": "AUTHOR COMPLETE",
  "inputs": {
    "discovery_report": ".obi/discovery-report.md",
    "reference_module": "ExampleModule/"
  },
  "outputs": {
    "files_created": ["ModuleName/ModuleName.psm1"],
    "lines_changed": 156,
    "linter_status": "pass"
  },
  "metrics": {
    "express_lane_eligible": false
  },
  "handoff": {
    "next_phase": "simplify"
  }
}
```

## Lifecycle

1. **Created** by `/obi-auto` at workflow start
2. **Updated** after each phase completion
3. **Read** to determine next phase and Express Lane eligibility
4. **Cleaned up** after successful Release Gate or on explicit reset

## Notes

- These files are gitignored (runtime only)
- State survives session restarts within same task
- Delete `ralph-state.json` to reset workflow

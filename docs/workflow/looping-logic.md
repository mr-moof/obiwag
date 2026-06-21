# Looping Logic

> **Version:** 1.0 | **Last Updated:** 2026-02-03

## Purpose

Define explicit rules for when to loop back, escalate, or hard stop during workflow execution.

## Decision Tree

```
Issue Detected
     │
     ▼
Is issue fixable? ─────No─────► NEEDS USER INPUT
     │
    Yes
     │
     ▼
How many issues? ─────>5─────► NEEDS USER INPUT (too many)
     │
    ≤5
     │
     ▼
Same issue failed before? ───Yes───► Check strike count
     │                                    │
    No                                    ▼
     │                              Strike count?
     │                                    │
     │                    ┌───────────────┼───────────────┐
     │                    │               │               │
     │                   1st             2nd             3rd
     │                    │               │               │
     │                Loop back    Strike #2 Checkpoint  3-STRIKE LIMIT
     │                    │               │               │
     ▼                    ▼               ▼               ▼
Loop back ◄────────────── │ ◄─(if user says proceed)     STOP
     │                                                    │
     │                                               Diagnose
     │                                               New approach
     ▼                                                    │
Fix issue ◄───────────────────────────────────────────────┘
     │
     ▼
Continue phase
```

## Loop Counters

### Per-Phase Loop Limit

| Metric | Limit | Action When Exceeded |
|--------|-------|---------------------|
| Loops per phase | 3 | `NEEDS USER INPUT: Max loops exceeded` |
| Total workflow loops | 10 | `NEEDS USER INPUT: Workflow loop limit` |
| Consecutive same-error | 3 | `3-STRIKE LIMIT` |

### Counter Tracking

Counters are tracked inside the strike state file (`.obi/strike-state.json`) by the `StrikeCounter` class. This path is relative to the project's working directory (CWD when hooks execute), not the obiwag-agents repo.

```json
{
  "strikes": [],
  "current_issue": null,
  "checkpoint_triggered": false,
  "phase_loops": {
    "2_author": 1,
    "4_review": 2,
    "5_integrate": 0
  },
  "total_loops": 3,
  "current_phase": "4_review"
}
```

## When to Loop Back

**Loop back when:**
- Issue is clear and fixable
- Number of issues ≤ 5
- Haven't exceeded loop limits
- Test failures with clear error messages
- Linting errors with specific file:line

**Loop back signal:** No explicit signal needed - just fix and continue

## When to Escalate

**Escalate to `NEEDS USER INPUT` when:**
- More than 5 issues detected
- Issue cause is unclear
- Fix requires design decision
- External dependency problems
- Ambiguous requirements
- Max loop count exceeded

**Escalate signal:**
```
NEEDS USER INPUT: [specific question or context needed]
```

**Examples:**
- `NEEDS USER INPUT: Should this function return null or throw for invalid input?`
- `NEEDS USER INPUT: Can't determine correct API endpoint - vendor docs show two options`
- `NEEDS USER INPUT: 6 review issues found - prioritization needed`

## When to Hard Stop

**Hard stop with `3-STRIKE LIMIT` when:**
- Same issue (same signature) failed 3 consecutive times
- Each attempt used a different approach
- No progress despite variations

**3-Strike signal:**
```
3-STRIKE LIMIT: [issue signature]

Previous attempts:
1. [First approach] - Failed: [reason]
2. [Second approach] - Failed: [reason]
3. [Third approach] - Failed: [reason]

Recommendation: [Diagnose root cause / Find example / Skip for now]
```

## Strike Counter Behavior

The strike counter (`.obi/strike-state.json`, relative to the project's working directory) tracks:

```json
{
  "strikes": [
    {
      "issue_signature": "linting:config_not_found",
      "timestamp": "2026-02-03T10:15:00",
      "error_message": "PSScriptAnalyzerSettings.psd1 not found",
      "attempt": "Looked for config in project root"
    }
  ],
  "current_issue": "linting:config_not_found",
  "checkpoint_triggered": false
}
```

### Issue Signature Format

Signatures should be specific enough to identify the issue but general enough to catch variations:

**Good signatures:**
- `linting:config_not_found`
- `test:assertion_failed:TestUserAuth`
- `api:cloud:401_unauthorized`
- `build:dependency_missing:lodash`

**Bad signatures (too vague):**
- `error`
- `failed`
- `test_failed`

### Checkpoint at Strike #2

At strike #2, pause and present options:

```markdown
## ⚠️ Strike #2 Checkpoint

This issue has failed **twice** in a row. Before attempting a third fix, let's pause.

**Issue:** `linting:config_not_found`

**Previous Attempts:**
1. Looked for config in project root - Failed
2. Looked for config in .config/ directory - Failed

**Options:**
1. **Proceed with Strike #3** - Attempt one more fix
2. **Provide guidance** - If you know the root cause
3. **Find examples** - Search repo for working examples
4. **Skip this fix** - Move on, address separately

Should I proceed with Strike #3, or would you like to provide input first?
```

## Phase-Specific Rules

| Phase | Loop Trigger | Escalate Trigger | Hard Stop Trigger |
|-------|--------------|------------------|-------------------|
| 2. Author | Test fail, lint error | Build fails, unclear requirements | Same test fails 3x |
| 4. Review | <5 issues found | >5 issues, unclear violations | - |
| 5. Integrate | Failed fix | Fix introduces new issues | Same issue 3x |
| 6. Re-review | Regression found | New pattern violations | - |

## Reset Conditions

### Strike Counter Reset

The strike counter resets when:
- Issue is successfully resolved
- New approach is explicitly adopted (with the user confirmation)
- Different issue type occurs
- Workflow restarts

### Loop Counter Reset

Loop counters reset when:
- Phase completes successfully
- Workflow restarts
- the user explicitly requests reset

## Integration with Autonomous Mode

In `/obi-auto` (Ralph loop):

1. **Before each fix attempt:** Check strike count
2. **At strike #2:** Pause, present checkpoint, wait for input
3. **At strike #3 failure:** Output `3-STRIKE LIMIT`, halt loop
4. **On escalation:** Output `NEEDS USER INPUT`, halt loop
5. **On success:** Reset strike counter, continue

## Counters API (for hooks)

```python
from core.strike_counter import StrikeCounter

# Record a failure
counter = StrikeCounter()
result = counter.record_failure(
    issue_signature="linting:config_not_found",
    context={
        "error_message": "Config file not found",
        "file": "src/main.ps1",
        "attempt_description": "Looked in project root"
    }
)

if result["should_checkpoint"]:
    print(result["checkpoint_message"])

if result["strike_count"] >= 3:
    print("3-STRIKE LIMIT reached")

# Record success
counter.record_success()

# Reset for new approach
counter.reset_counter(new_approach="Using global config instead")
```

---

## Dispatch retry tracking (Issue #163)

Parallel to the strike counter, the orchestrator tracks subagent-dispatch failures via
`.obi/state/dispatch-state.json`. This counter is separate from the strike counter; the two
mechanisms address different failure modes (strikes = same fix tried 3x in a row;
dispatch-retry = a delegated phase produced no usable output — the OPT-23 headless worker timed out
or errored, or the `Agent` fallback returned a sentinel without agent output).

### Schema (dispatch-state.json)

```json
{
  "schema_version": 1,
  "run_id": "20260513T120000Z",
  "per_phase": {
    "3_simplify": 0,
    "6_re-review": 1
  },
  "per_run": 1
}
```

### Lifecycle rules

1. **Autonomous run start.** Orchestrator reads `.obi/state/run-id.txt` (or creates it for a fresh
   run; the user is consulted if a stale run-id from a halted prior run exists). Compares
   `dispatch-state.json.run_id` against `run-id.txt`. If mismatch or file absent, overwrites with a
   fresh `{schema_version: 1, run_id: <id>, per_phase: {}, per_run: 0}`.
2. **Manual `/obi` mode.** Same run-id rule (read existing or create on demand). No automatic
   dispatch-state reset; the user is the loop. `/obi` prints `per_run` counter on startup so the
   user can `Remove-Item .obi/state/dispatch-state.json` if desired.
3. **End-of-run.** On successful Learning phase completion, delete BOTH `dispatch-state.json` AND
   `run-id.txt`. On halt/failure, both remain in place so the next run can offer resume semantics.
4. **`run_id` format.** `yyyyMMddTHHmmssZ` (filename-safe compact UTC). NOT ISO `o` format because
   colons are invalid in Windows filenames and `run_id` appears in artifact paths like
   `.obi/reviews/<run_id>-rereview.md`.

### Retry budget (default)

- 1 retry per phase
- 3 retries per run total

Reaching either limit transitions to inline fallback (for inline-fallback-eligible phases per
`phases/phase-table.json`) or `NEEDS USER INPUT` halt (for synthesis phases). See
`orchestration/obi-auto.md` Phase-Output Validation for the full contract.

### Files this PR formalizes under `.obi/state/`

NEW (introduced by Issue #163):
- `.obi/state/run-id.txt` — filename-safe compact UTC run id, single line.
- `.obi/state/dispatch-state.json` — retry-tracking schema above.

Pre-existing files (listed for healthcheck completeness; not introduced or modified by this PR):
- `.obi/state/phase-0-prereqs.json` — written by rigor=max Phase 0 (see
  `orchestration/obi-auto.md`).

---

*See `docs/policies/three-strike-rule.md` for policy details.*
*See `docs/workflow/resume-protocol.md` for resume procedures.*

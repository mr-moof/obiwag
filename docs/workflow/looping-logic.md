# Looping Logic

> **Version:** 1.0 | **Last Updated:** 2026-02-03

## Purpose

Define explicit rules for when to loop back, escalate, or hard stop during workflow execution.

## Decision Tree

```
Issue Detected
     │
     ▼
Is issue fixable in scope? ───No────► Named safety/source/authority boundary
     │
    Yes
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
     │                Loop back    Diagnose + new path   3-STRIKE LIMIT
     │                    │               │               │
     ▼                    ▼               ▼               ▼
Loop back ◄────────────── │ ◄──────────── │      Stop identical approach
     │                                                    │
     │                                      Different bounded recovery exists?
     ▼                                                    │
Fix issue ◄──────────────────────────────────────Yes──────┘
     │
     ▼
Continue phase
```

## Loop Counters

### Per-Phase Loop Limit

| Metric | Limit | Action When Exceeded |
|--------|-------|---------------------|
| Loops per phase | 3 | Diagnose and select a materially different bounded recovery; record it |
| Total workflow loops | 10 | Primary reconciliation; terminal only for a named non-bypassable boundary |
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
- Haven't exceeded loop limits
- Test failures with clear error messages
- Linting errors with specific file:line

**Loop back signal:** No explicit signal needed - just fix and continue

## When to Escalate

Issue count, retry exhaustion, unclear worker output, and a fixable test/build failure are not
authority boundaries in `obi-auto` or `obi-auto-max`. Record the exact evidence in
`.obi/state/status-updates-<run_id>.jsonl`, select the conservative reversible recovery, and
continue. Manual `obi` may still ask for prioritization or a product choice.

Autonomous execution stops only for an explicit user abort or a named non-bypassable boundary:
missing authoritative source, unavailable required credential, unauthorized external write,
destructive/irreversible out-of-scope action, `HARD STOP`, or a risk of data corruption/exposure.
State the exact missing evidence or authority; never ask the generic question "should I proceed?".

## When to Stop an Approach

**Emit `3-STRIKE LIMIT` and stop the current approach when:**
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

In manual mode this may require a product decision. In autonomous mode, the signal is a diagnostic
checkpoint: append `phase_blocker_fixable` or `check_failure_fixable`, select a materially different
safe route, and continue. Retry exhaustion becomes terminal only when the diagnosis identifies a
separate non-bypassable boundary from the list above.

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
- `api:widgetapi:401_unauthorized`
- `build:dependency_missing:lodash`

**Bad signatures (too vague):**
- `error`
- `failed`
- `test_failed`

### Checkpoint at Strike #2

At strike #2, diagnose before another attempt. In manual mode, present options. In autonomous mode,
append the diagnosis and selected materially different bounded path, then continue without a
permission question:

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

Manual mode only: choose the next approach or provide missing product input.
```

## Phase-Specific Rules

| Phase | Loop Trigger | Escalate Trigger | Approach Stop Trigger |
|-------|--------------|------------------|-------------------|
| 2. Author | Test fail, lint error | Missing product/source boundary | Same test fails 3x |
| 4. Review | Reproduced issue found | Missing source or unsafe scope | Reproduced hard-stop violation (terminal) |
| 5. Integrate | Failed fix | Fix introduces new issues | Same issue 3x |
| 6. Re-review | Regression found | New pattern violations | - |

## Reset Conditions

### Strike Counter Reset

The strike counter resets when:
- Issue is successfully resolved
- A genuinely different approach is explicitly adopted (manual confirmation or autonomous ledger record)
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
2. **At strike #2:** Diagnose and record a materially different bounded approach; continue
3. **At strike #3 failure:** Output `3-STRIKE LIMIT`; stop the identical approach and use a safe
   codified recovery/primary route when one exists
4. **On recoverable escalation:** Append the autonomous decision, execute its ordered actions, and continue
5. **On a non-bypassable boundary:** Append terminal evidence and halt
6. **On success:** Reset strike counter, continue

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
   run). It validates existing ownership and state: resume an internally consistent resumable run,
   or manifest-archive terminal/safely recognizable state and start fresh. Unknown ownership or an
   unverifiable live writer is a data-integrity hard stop. No stale-state continuation question.
   `dispatch-state.json.run_id` must match `run-id.txt`; mismatch is classified before replacement.
2. **Manual `/obi` mode.** Same run-id rule (read existing or create on demand). No automatic
   dispatch-state reset; the user is the loop. `/obi` prints `per_run` counter on startup so the
   user can `Remove-Item .obi/state/dispatch-state.json` if desired.
3. **End-of-run.** On successful Learning phase completion, use
   `tools/archive-run-state.ps1 -RunId <active>` and require its complete manifest. Never delete
   `dispatch-state.json`, `run-id.txt`, or completion markers by hand. On halt/failure, state remains
   so the next run can validate recovery semantics.
4. **`run_id` format.** `yyyyMMddTHHmmssZ` (filename-safe compact UTC). NOT ISO `o` format because
   colons are invalid in Windows filenames and `run_id` appears in artifact paths like
   `.obi/reviews/<run_id>-rereview.md`.

### Retry budget (default)

- 1 retry per phase
- 3 retries per run total

Reaching either limit transitions to inline fallback (for inline-fallback-eligible phases per
`phases/phase-table.json`) or bounded primary takeover after verified worker termination. It never
becomes a permission-only halt. See `orchestration/obi-auto.md` Phase-Output Validation.

### Files this MR formalizes under `.obi/state/`

NEW (introduced by Issue #163):
- `.obi/state/run-id.txt` — filename-safe compact UTC run id, single line.
- `.obi/state/dispatch-state.json` — retry-tracking schema above.

Autonomous recovery ledger:
- `.obi/state/status-updates-<run_id>.jsonl` — append-only evidence, decision, provenance,
  disposition, ordered action, and concise progress records.

Pre-existing files (listed for healthcheck completeness; not introduced or modified by this MR):
- `.obi/state/phase-0-prereqs.json` — written by rigor=max Phase 0 (see
  `orchestration/obi-auto.md`).

---

*See `docs/policies/three-strike-rule.md` for policy details.*
*See `docs/workflow/resume-protocol.md` for resume procedures.*

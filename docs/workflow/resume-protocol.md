# Resume Protocol

> **Version:** 1.0 | **Last Updated:** 2026-02-03

## Purpose

Define how to resume workflow after break signals (`NEEDS USER INPUT`, `HARD STOP`, `3-STRIKE LIMIT`).

## Break Signal Types

| Signal | Cause | Resolution Required |
|--------|-------|---------------------|
| `NEEDS USER INPUT` | Missing information, ambiguous requirements | Clarification from the user |
| `HARD STOP: [reason]` | Policy violation, security concern | Root cause fix + approval |
| `3-STRIKE LIMIT` | Same issue failed 3x | New approach or escalation |

## State Preservation

Break signals preserve state in `.obi/state/`:

```
.obi/
├── state/
│   ├── phase-current.json      # Current phase info
│   ├── phase-N-<name>.json     # Each phase's output
│   └── break-context.json      # Break signal context
└── strike-state.json           # Strike counter state (in project CWD)
```

## Resume Procedures

### Pre-check: Completions Record (OPT-22)

After any interrupt (including stall-triggered Ctrl-C), **before** re-executing a phase, check
whether the SubagentStop hook already recorded the phase's completion signal:

1. Read `.obi/state/dispatch-state.json` and look for an entry in `completions[]` matching the
   interrupted phase number.
2. **Signal present**: verify the phase artifact exists (e.g., `.obi/discovery-report.md` for
   Phase 1, `.obi/reports/author-report-*.md` for Phase 2).
3. **Artifact valid**: advance to the next phase -- the subagent finished but the `Agent` call's
   return value was lost in IPC. No rework needed.
4. **Artifact missing despite signal**: the SubagentStop hook fired but the subagent did not
   write its artifact. Re-execute the phase.
5. **No completion recorded**: proceed with normal resume below.

This pre-check prevents re-doing a 5-10 minute synthesis phase when the only failure was the
dispatch IPC dropping the result.

### After NEEDS USER INPUT

1. **Read break context**: Check `.obi/state/break-context.json` for what was asked
2. **Get the user's response**: the user provides the missing information
3. **Resume same phase**: Continue from the phase where break occurred
4. **No phase restart**: Use existing phase state, don't re-run from scratch

**Example flow:**
```
Phase 3 (Simplify) → NEEDS USER INPUT: "Should I remove this legacy function?"
                   → the user: "Yes, remove it"
                   → Continue Phase 3 with decision
                   → SIMPLIFY COMPLETE
```

### After HARD STOP

1. **Identify violation**: Read the reason in the HARD STOP signal
2. **Fix root cause**: Address the policy violation
3. **Get approval**: the user must explicitly approve resumption
4. **Resume same phase**: Continue from where stopped

**Example flow:**
```
Phase 2 (Author) → HARD STOP: Security - credentials in code
                 → Fix: Move credentials to env vars
                 → the user: "Fixed, continue"
                 → Resume Phase 2
                 → AUTHOR COMPLETE
```

### After 3-STRIKE LIMIT

1. **Review strike history**: Check `.obi/strike-state.json` for failure pattern
2. **Diagnose root cause**: Identify why 3 attempts failed
3. **Choose resolution**:
   - **New approach**: Different strategy, reset strike counter
   - **Skip for now**: Mark as technical debt, continue workflow
   - **Escalate**: Seek external help (team, documentation)
4. **Resume with chosen approach**

**Example flow:**
```
Phase 4 (Review) → 3-STRIKE LIMIT: Linting config not found
                 → Diagnosis: Config in wrong location
                 → New approach: Create config at project root
                 → Reset strike counter
                 → Resume Phase 4
                 → REVIEW COMPLETE
```

## Phase-Specific Resume Rules

| Phase | After Break | Resume Behavior |
|-------|-------------|-----------------|
| All | Stall interrupt | Run the Pre-check (completions record) above first; advance if signal+artifact present |
| 1. Discovery | NEEDS USER INPUT | Continue discovery with new info |
| 2. Author | Any | Re-run from last successful edit |
| 3. Simplify | NEEDS USER INPUT | Apply decision, continue cleanup |
| 4. Review | 3-STRIKE | Skip failing check, document as known issue |
| 5. Integrate | Any | Re-apply fixes from last known good state |
| 6-10 | Any | Continue from phase start |

## Commands for Resume

### Check current state
```bash
# View current phase
cat .obi/state/phase-current.json

# View break context
cat .obi/state/break-context.json

# View strike history
cat .obi/strike-state.json
```

### Reset state (if needed)
```bash
# Reset strike counter only
rm .obi/strike-state.json

# Full state reset (restart workflow)
rm -rf .obi/state/
```

## Autonomous Mode (Ralph Loop)

When running `/obi-auto`:

1. **Break signals halt the loop** - Ralph stops, presents context
2. **the user provides input** - Response captured
3. **Loop resumes** - From same phase, with new context
4. **No manual phase invocation** - Ralph handles continuation

## Manual Mode (`/obi`)

When running phases manually:

1. **Break signal output** - Claude outputs the break message
2. **the user resolves** - Provides information or approval
3. **Re-invoke same phase** - Run the same `/command` again
4. **State is preserved** - Phase picks up where it left off

## Anti-Patterns

**Don't do these:**

- **Don't restart from Phase 1** after a Phase 5 break (wasteful)
- **Don't ignore break signals** and force continue (unsafe)
- **Don't manually edit state files** without understanding structure
- **Don't mix manual and auto mode** mid-workflow

## Integration with Memory System

Break signals are logged for learning:

```yaml
# In session summary
breaks:
  - signal: NEEDS USER INPUT
    phase: 3
    context: "Legacy function removal decision"
    resolution: "Confirmed removal"
    time_to_resolve: 2m
```

This helps calibrate:
- Which phases need better pre-work (fewer NEEDS USER INPUT)
- Which issues are common (for proactive handling)
- Resolution patterns (for future automation)

---

*See `docs/workflow/phases.md` for phase definitions.*
*See `docs/policies/three-strike-rule.md` for strike counter details.*

# Resume Protocol

> **Version:** 1.0 | **Last Updated:** 2026-02-03

## Purpose

Define how manual workflows resume after break signals and how autonomous workflows recover from
reversible in-scope blockers without stopping for continuation permission.

## Break Signal Types

| Signal | Cause | Resolution Required |
|--------|-------|---------------------|
| `NEEDS USER INPUT` | Missing information, ambiguous requirements | Manual `/obi`: clarification from the user. Autonomous: classify first — recover in scope, or append the named terminal boundary and halt |
| `HARD STOP: [reason]` | Policy violation, security concern | Root cause fix + approval |
| `3-STRIKE LIMIT` | Same issue failed 3x | New approach or escalation |

In `obi-auto` and `obi-auto-max`, the signal text is not itself authoritative. The orchestrator
classifies its underlying evidence first. Retry exhaustion, worker failure, stale state, peer
unavailability, and fixable phase/check failures use the standing autonomous recovery contract;
only a named non-bypassable boundary remains a break.

## State Preservation

Break signals preserve state in `.obi/state/`:

```
.obi/
├── state/
│   ├── dispatch-state.json     # Current phase + recorded completion signals
│   ├── phase-N-<name>.json     # Each phase's output
│   ├── native-phase-<run>-<N>.json # Authoritative Codex-native timeline
│   ├── status-updates-<run>.jsonl  # Append-only recovery evidence and decisions
│   └── task-base-<run>.txt     # Task base commit for the run
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

### Provider-specific delegated resume

- **Claude supervisor:** read the unscoped current status, then retain the immutable path named by
  `attempt_status_file` before resuming. Attempt 2 must use the same assigned Claude session and
  writes `-a2`; never infer a two-attempt predicate from a current file that attempt 2 replaced.
- **Codex native thread:** use the phase's `delegation_policy.codex` and update the run-scoped
  record through `tools/native_phase_state.py`. Never steer before `synthesis_due_at_utc`; send the
  exact no-tools synthesis instruction and record every later worker message/tool completion. After
  the configured grace, interrupt and reconcile the worktree, then resume that same thread exactly
  once for the configured two bounded windows. Recent progress yields
  `productive_budget_exhausted`, not stalled. Only a full resumed bound with no observable progress
  becomes `native_completion_stalled`; never launch a replacement. This is not `timed_out` and
  cannot satisfy Claude capacity-mismatch evidence. In `obi-auto`/`obi-auto-max`, the validated
  stall activates scoped standing primary-takeover authority without another permission prompt.
  Invoke `tools/autonomous_recovery.py native_completion_stalled` with that timeline first; its
  ordered actions are status append, primary takeover, and continued dispatch.

### After NEEDS USER INPUT

In manual `obi`, read the last appended entry in `.obi/state/status-updates-<run>.jsonl`, obtain the
missing material input, and resume the same phase. In `obi-auto`/`obi-auto-max`, first classify the
reason:

1. Reversible, conservative, in-scope choice → append the decision and continue.
2. Fixable blocker/check failure → primary repair or takeover, rerun the bounded phase/check, continue.
3. Missing authoritative source, required credential, external-write authority, or safety boundary
   → append terminal evidence and halt with that exact reason.

Never ask merely to authorize retry, fallback, takeover, state recovery, or continued verification.

**Example flow (autonomous):**
```
Phase 3 (Simplify) → "Should I remove this legacy function?"
                   → Reversible + in scope: keep it, append reversible_default_selected
                   → Continue Phase 3 with the decision recorded
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
   - **Defer only if allowed by acceptance criteria**: Record the omission and evidence
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
| All | Stall interrupt | Run the completion pre-check; if incomplete, bounded same-worker recovery then recipe/primary takeover |
| 1. Discovery | Fixable context/blocker | Synthesize settled evidence under primary provenance; no new research after the bound |
| 2. Author | Fixable context/blocker | Reconcile claims with worktree, primary takeover from last verified edit |
| 3. Simplify | Recoverable blocker | Select conservative in-scope cleanup or skip; record decision |
| 4. Review | Peer unavailable | Send nothing, run local primary review once, continue |
| 5. Integrate | Fixable finding | Re-apply from last known good state and rerun focused evidence |
| 6-10 | Fixable blocker/check | Use codified recipe or bounded primary takeover; preserve phase-specific evidence |

## Commands for Resume

### Check current state
```bash
# View current phase and recorded completion signals
cat .obi/state/dispatch-state.json

# View recovery decisions for the run
cat .obi/state/status-updates-<run>.jsonl

# View strike history
cat .obi/strike-state.json
```

### Archive state for a fresh run

Never delete state by hand. Validate the active RunId, then use the move-only helper and require a
complete manifest:

```powershell
powershell -NoProfile -File $env:OBI_HOME\tools\archive-run-state.ps1 -RunId <active-run-id>
```

At autonomous startup, resume internally consistent state. For terminal/safely recognizable state,
record `prior_run_terminal_or_corrupt`, archive it, and mint a fresh RunId. Unknown ownership,
unsafe paths, collisions, or an unverifiable live writer are data-integrity hard stops.

## Autonomous Mode (Ralph Loop)

When running `/obi-auto`:

1. **Classify the evidence** — signal wording alone does not decide disposition
2. **Append the decision** — use `.obi/state/status-updates-<run>.jsonl`
3. **Execute ordered recovery** — retry/recipe/primary takeover/state archive as selected
4. **Emit one informational update** — never a continuation question
5. **Continue from the same phase** — halt only for a named non-bypassable boundary

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
  - signal: reversible_default_selected
    phase: 3
    context: "Legacy function removal decision"
    resolution: "Kept the function; recorded the reversible default"
    time_to_resolve: 0m
```

This helps calibrate:
- Which phases need better pre-work (fewer classify-and-recover detours)
- Which issues are common (for proactive handling)
- Resolution patterns (for future automation)

---

*See `docs/workflow/phases.md` for phase definitions.*
*See `docs/policies/three-strike-rule.md` for strike counter details.*

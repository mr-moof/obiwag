# Dispatch failure procedure

Load when a phase result is missing, interrupted, blocked, requests context, reports concerns,
or lacks its expected completion signal. The owning contract is `orchestration/obi-auto.md`.

### Step 1 — Detect failure sentinels

Check the result body in evaluation order (first match wins):

| Pattern | Class | Reaction |
|---|---|---|
| Literal string `[Tool result missing due to internal error]` | Harness sentinel | Step 2 — classify |
| Literal string `[Request interrupted by user]` | User abort | Append `user_abort`, halt, and surface the exact terminal evidence |
| Empty body or whitespace-only | Harness sentinel | Step 2 — classify |
| Line matching regex `^NEEDS_CONTEXT(:\s.+)?$` | Context request | Supply settled context once. On a fixable repeat append `phase_blocker_fixable`, take over, and continue; only genuinely absent authoritative evidence becomes `MISSING SOURCE`. NOT subject to dispatch retry budget. |
| Line matching regex `^(AUTHOR )?BLOCKED:\s.+$` | Blocked | Classify the reason; append `phase_blocker_fixable` and repair/take over when in scope, or append the exact named terminal safety/authority boundary |
| `^COMPLETE_WITH_CONCERNS$` AND Re-review / README Review | Soft pass | Verify report artifact + `Verdict:` line with "CONCERNS". Valid: append to `.obi/reviews/<run_id>-concerns.md`, advance. |
| `^COMPLETE_WITH_CONCERNS$` AND Author / Integrate / Discovery / Learning | Verify concerns | Reconcile claims with the worktree; fix/take over when in scope, or append the exact named terminal boundary |
| `^COMPLETE_WITH_CONCERNS$` AND Simplify | Soft pass | Log to simplify report `Concerns:`, treat as `SIMPLIFY COMPLETE`, advance. |
| `^3-STRIKE LIMIT(:\s.+)?$` (Author or Integrate only) | Same approach exhausted | Stop that approach, append `phase_blocker_fixable`, and use a materially different bounded recovery/primary route; terminal only for a separately named non-bypassable boundary. |
| Body lacks expected completion signal AND no status-protocol signal above | Subagent confusion | Step 3 — inline fallback (do NOT retry) |

**Observability requirement (sentinel recovery).** On harness sentinel match, BEFORE any
verification read, emit: `DROP DETECTED: <tool/phase> — verifying with <check>, then <retry once | proceed | halt>`.
After Step 2 resolves, emit: `DROP RESOLVED: <already-applied | retried-ok | escalating>`.
Grep-stable contract: `^DROP (DETECTED|RESOLVED):`.
If you reach the step-1 read after a sentinel without having emitted the DETECTED line, you skipped
the contract — emit it now.

```text
DROP DETECTED: Agent/Review — verifying with .obi/state/dispatch-state.json, then retry once
VERIFY: no completed Review artifact; retry budget available
RETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS
DROP RESOLVED: retried-ok
```

### Step 2 — Failure classification (for harness sentinel / empty body only)

Read `.obi/state/dispatch-state.json` (already initialized at run start).

- If `per_phase[<N>_<name>] >= 1` (already retried) OR `per_run >= 3` (budget exhausted): Step 3.
- Else: bump `per_phase[<N>_<name>]` and `per_run`, write file, retry SAME `Agent` call ONCE.
  Goto Step 1 on the new result.

### Step 3 — Inline fallback (only for phases with `inline_fallback_eligible: true`)

Execute the recipe from `orchestration/inline-fallback-recipes.md` (Recipe S, R, M, or G).
On completion, emit the subagent's signal, reset `per_phase[<N>_<name>]`, advance to /compact.

For phases whose current `phase-table.json` entry has `inline_fallback_eligible: false`, append
`phase_blocker_fixable` with the phase name and whether retry budget exhaustion or subagent
confusion occurred, perform the phase-appropriate bounded primary takeover, and continue. If a
required source/credential/authority or safe worker termination is genuinely unavailable, append
that named terminal boundary instead. Do not maintain a duplicated hard-coded phase-name list here.

### Silent-hang handling (supervisor self-bounds)

The bounded supervisor (Step 0) self-bounds: it scoped-kills the worker and persists a terminal
`.obi/state/dispatch-status-<run_id>-<N>.json` (`timed_out`/`killed`/`error` with `kill_verified`)
with no operator in the loop, so recover from that record per Step 0.3-0.5 — checking
`.obi/state/dispatch-state.json` `completions[]` first — and follow
`docs/workflow/resume-protocol.md`.

---

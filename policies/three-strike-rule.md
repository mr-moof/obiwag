# Three Strike Rule

> **Version:** 1.0 | **Last Updated:** 2026-02-02

## Purpose

Prevent brute-force debugging. If a fix fails 3 times, stop that approach and diagnose instead of
continuing to guess. In autonomous mode, this checkpoint does not revoke standing authority for a
materially different reversible in-scope recovery.

## The Rule

**If the same issue fails 3 consecutive fix attempts, STOP THAT APPROACH.**

Do not attempt a fourth variation of the same strategy. Diagnose the root cause and seek a
materially different approach. Manual `obi` may ask for a missing product decision. `obi-auto` and
`obi-auto-max` append `phase_blocker_fixable` or `check_failure_fixable` and execute a safe codified
fallback or bounded primary route. Retry exhaustion alone is not a terminal boundary.

## What Counts as a "Strike"

A strike is counted when:
1. A specific fix is applied
2. The fix is tested/verified
3. The original issue persists or a new related issue appears

### Examples

**Counts as strikes:**
- "Added null check" → test fails → Strike 1
- "Changed to optional chaining" → test fails → Strike 2
- "Added default value" → test fails → Strike 3 → STOP

**Does NOT count as strikes:**
- Different issues (each issue has its own strike counter)
- Incomplete fixes (fix not yet tested)
- Build/lint errors unrelated to the fix

## On Third Strike

### Signal

Output: `3-STRIKE LIMIT: [issue description] failed 3 consecutive times`

### Required Actions

1. **Stop fixing** - No more fix attempts for this specific issue
2. **Document attempts** - List what was tried and why it failed
3. **Analyze pattern** - What do the failures have in common?
4. **Propose alternative** - Different approach or escalation
5. **Autonomous disposition** - Record and execute a materially different safe route; halt only for
   a separately named non-bypassable safety, authority, credential, source, external-write, user
   abort, ownership, or data-integrity boundary

### Output Format

```
## 3-STRIKE LIMIT REACHED

**Issue:** [Description of the problem]

### Fix Attempts
1. **Attempt 1:** [What was tried]
   - **Result:** [What happened]
   - **Why it failed:** [Analysis]

2. **Attempt 2:** [What was tried]
   - **Result:** [What happened]
   - **Why it failed:** [Analysis]

3. **Attempt 3:** [What was tried]
   - **Result:** [What happened]
   - **Why it failed:** [Analysis]

### Pattern Analysis
[What do these failures have in common?]

### Recommended Path Forward
- Option A: [Alternative approach]
- Option B: [Escalation path]

Manual mode: waiting for a genuinely missing product decision.
Autonomous mode: recovery decision appended; materially different bounded route continues.
```

## Prevention

Before attempting any fix:
1. Understand the root cause (not just symptoms)
2. Have a hypothesis for why the fix will work
3. Know how to verify the fix worked

## Reset Conditions

Strike counter resets when:
- the user provides new information that changes the approach
- A fundamentally different approach is taken, including one selected by the autonomous recovery
  contract
- The issue is re-scoped or clarified

Strike counter does NOT reset:
- Between sessions (persistence via memory system)
- By trying "one more thing"
- By slight variations of the same approach

## Context Reset

After 3-strike limit, context pollution from failed attempts may itself be the problem. Manual
Claude workflows may use `/clear`; Codex and autonomous workflows compact settled evidence before
trying the materially different approach. A context reset never authorizes replaying the same fix.

## Escalation

If three-strike limit is reached and no alternative is viable:
1. Document the issue fully
2. Name the missing source, credential, authority, ownership proof, or safety boundary that makes
   every alternative non-viable
3. Create an issue for human investigation only when the original task authorized that external
   write
4. Move on to other work if possible
5. Do not block on unresolvable issues

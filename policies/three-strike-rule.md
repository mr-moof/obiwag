# Three Strike Rule

> **Version:** 1.0 | **Last Updated:** 2026-02-02

## Purpose

Prevent brute-force debugging. If a fix fails 3 times, stop and diagnose instead of continuing to guess.

## The Rule

**If the same issue fails 3 consecutive fix attempts, STOP.**

Do not attempt a 4th fix. Instead, diagnose the root cause and seek an alternative approach.

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

Waiting for the user direction.
```

## Prevention

Before attempting any fix:
1. Understand the root cause (not just symptoms)
2. Have a hypothesis for why the fix will work
3. Know how to verify the fix worked

## Reset Conditions

Strike counter resets when:
- the user provides new information that changes the approach
- A fundamentally different approach is taken
- The issue is re-scoped or clarified

Strike counter does NOT reset:
- Between sessions (persistence via memory system)
- By trying "one more thing"
- By slight variations of the same approach

## Context Reset

After 3-strike limit, context pollution from failed attempts may itself be the problem.
Use `/clear` to reset context and rewrite the prompt from scratch before trying an alternative approach.

## Escalation

If three-strike limit is reached and no alternative is viable:
1. Document the issue fully
2. Use `/clear` to reset context if attempting a new approach
3. Create issue for human investigation
4. Move on to other work if possible
5. Do not block on unresolvable issues

# Hard Stop Conditions

> **Version:** 1.0 | **Last Updated:** 2026-02-02

## Purpose

Define conditions that trigger immediate workflow halt. No exceptions, no workarounds.

## Hard Stop Triggers

### 1. Hallucinated APIs

**Trigger:** Using any API endpoint, parameter, or cmdlet without repo evidence.

**Signal:** `HARD STOP: Hallucinated API - [vendor/technology] [endpoint/cmdlet] has no evidence in repo`

**Resolution:**
1. Find existing wrapper or documentation in `docs/domain-patterns/` or `docs/`
2. If none exists, request the user provide official vendor documentation
3. Never proceed without verified source

### 2. Missing Test Evidence

**Trigger:** Claiming code works without running actual tests.

**Signal:** `HARD STOP: No test evidence - changes must be verified by running tests`

**Resolution:**
1. Run the documented test command
2. Capture output showing pass/fail
3. Only proceed with passing tests

### 3. Security Boundary Violation

**Trigger:** Attempting to modify auth, encryption, or credential handling without explicit approval.

**Signal:** `HARD STOP: Security boundary - [change description] requires the user approval`

**Resolution:**
1. Stop current work
2. Document proposed security change
3. Wait for explicit the user approval

### 4. Three-Strike Limit Reached

**Trigger:** Same fix attempted 3+ times without success.

**Signal:** `HARD STOP: 3-strike limit - [issue description] failed 3 consecutive times`

**Resolution:**
1. Stop fix attempts
2. Diagnose root cause
3. Seek alternative approach or escalate to the user

### 5. Destructive Operation Without Confirmation

**Trigger:** Attempting file deletion, git force operations, or data removal.

**Signal:** `HARD STOP: Destructive operation - [operation] requires explicit confirmation`

**Resolution:**
1. Present what will be deleted/overwritten
2. Wait for the user confirmation
3. Only proceed with explicit approval

### 6. Codex FAIL Verdict Ignored

**Trigger:** Codex (Recipe 1, 2, or 3 per `policies/codex-usage.md`) ran to completion and returned a FAIL verdict, critical findings, or hallucinated-API flag, and the author/orchestrator proceeded without addressing every finding.

**Signal:** `HARD STOP: Codex FAIL verdict ignored - pass <N>: <unaddressed-finding-summary>`

**Resolution:**
1. List every Codex finding that has NOT been addressed in the current diff or plan
2. For each: either fix the code/plan, OR provide explicit evidence the finding is a false positive (cite repo `file:line`, vendor doc committed in repo, or test output)
3. If the user decides to override despite the finding: record the override in `.obi/state/codex-overrides.md` with rationale before resuming
4. Multi-pass convergence (per memory `feedback_codex_multipass_converges_two_rounds`) targets a PASS verdict — accumulated FAILs across N passes do NOT become advisory by virtue of being repeated. "Codex passes 1 and 2 both FAILed; I proceeded anyway" is a §6 violation regardless of how confident the author feels about the inferred content.

**Why this exists:** `policies/codex-usage.md` non-goal "Codex unavailability is not a fatal gate" refers only to availability. A FAIL verdict is authoritative output from a successful run; ignoring it — especially when the author admits "inferred from pattern" or "best-effort synthesis" — is a zero-hallucination-policy violation (see `policies/zero-hallucination.md`). Canonical memories: `feedback_codex_fail_is_halt_not_proceed` (FAIL is halt, not "proceed cautiously") and `feedback_no_authoritative_docs_from_inferred_content` (domain-shaped synthesis — Application names, choice values, retry policies — is the worst hallucination class).

## How to Report

When a hard stop occurs:

```
## HARD STOP: [Category]

**Trigger:** [What triggered this stop]

**Evidence:**
- [What was attempted]
- [Why it violates policy]

**Resolution Required:**
- [What the user needs to provide/decide]

Workflow halted. Waiting for resolution.
```

## Recovery

After hard stop resolution:
1. the user provides required information/approval
2. Document the resolution
3. Resume from the halted phase
4. Do not restart the entire workflow

## Non-Negotiable

Hard stops cannot be bypassed by:
- "I'm confident it will work"
- "The user asked for this"
- "It's just a small change"
- "I'll verify later"

The only path forward is resolution through proper channels.

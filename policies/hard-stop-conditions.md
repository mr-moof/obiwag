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
2. Append `hard_stop` with that evidence via `autonomous_recovery.py` and halt — this boundary is
   not bypassable by an autonomous default
3. Only proceed after explicit the user approval

### 6. Reproduced Peer Finding Ignored

**Trigger:** The bounded peer harness returned a mechanically accepted finding, the primary reviewer reproduced that defect against the original working tree, and the author/orchestrator proceeded without fixing it or recording an evidence-backed rebuttal/override.

**Signal:** `HARD STOP: Reproduced peer finding ignored - <provider>: <unaddressed-finding-summary>`

**Resolution:**
1. List every reproduced peer finding that has NOT been addressed in the current diff or plan
2. For each: either fix the code/plan, OR provide explicit evidence the finding is a false positive (cite repo `file:line`, vendor doc committed in repo, or test output)
3. If the user decides to override despite the reproduced defect: record the override in `.obi/state/peer-review-overrides.md` with rationale before resuming

**Why this exists:** `policies/peer-review.md` makes the peer verdict advisory because transport completion, schema validity, citation validity, and correctness are different facts. A raw peer FAIL is therefore not a hard stop. Once the primary reviewer independently reproduces a defect, however, proceeding without disposition violates the zero-hallucination policy. Rejected citations and unavailable/inconclusive runs can never trigger this condition.

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

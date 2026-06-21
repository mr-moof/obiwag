---
topic: verification
confidence: 0.80
last_updated: 2026-02-05
match_keywords:
  - done
  - complete
  - finished
  - ready
  - fixed
  - implemented
  - working now
  - should work
  - all set
  - task complete
  - bug fixed
  - feature complete
  - ready to merge
  - ready for review
  - pushing
  - committing
sources:
  - path: platforms/github-copilot/docs/skills/verification-before-completion.md
    priority: 1
    description: Full verification checklist
---

# Verification Before Completion Pattern

## When to Inject

This pattern applies when task appears to be nearing completion,
or when claiming something is done/fixed/working.

## Key Grounding Points

**Core Principle:** Never claim success without verification.

"It should work" is NOT verification.
"I tested it and here's the output" IS verification.

## Injection Text

```
✅ VERIFICATION REQUIRED BEFORE COMPLETION

Before claiming this task is done, provide evidence:

**For Code Changes:**
- [ ] Linter run: `Invoke-ScriptAnalyzer` → show output
- [ ] Tests run: show actual test output (X/Y passing)
- [ ] Manual verification: describe what you tested and the result

**For Bug Fixes:**
- [ ] Root cause identified (not just symptom fixed)
- [ ] Test case created that would have caught this
- [ ] Test now passes (show output)

**For Pipeline/Build:**
- [ ] Local validation run (show output)
- [ ] Pipeline job passed (link to job)

**Evidence Format:**
```
Linter: [actual command output]
Tests: Tests Passed: X, Failed: 0
Manual: [what you did] → [what happened]
```

❌ NOT acceptable:
- "Should work now"
- "Looks good"
- "Fixed"
- "Tests should pass"

Reference: platforms/github-copilot/docs/skills/verification-before-completion.md
```

## Red Flags

These indicate skipping verification:
- Saying "done" without showing test output
- Assuming fix works because logic seems right
- Trusting that CI will catch issues
- Marking complete before running tests

---
topic: debugging
confidence: 0.85
last_updated: 2026-02-05
match_keywords:
  - error
  - failed
  - failure
  - bug
  - broken
  - not working
  - doesn't work
  - exception
  - crash
  - fix
  - debug
  - troubleshoot
  - investigate
  - pipeline failed
  - test failed
  - linter error
  - build failed
sources:
  - path: platforms/github-copilot/docs/skills/systematic-debugging.md
    priority: 1
    description: Full debugging methodology
---

# Systematic Debugging Pattern

## When to Inject

This pattern applies when task involves errors, failures, bugs, debugging,
troubleshooting, or fixing broken code.

## Key Grounding Points

**The Iron Law:** NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

## Injection Text

```
🔧 DEBUGGING MODE ACTIVATED

Before attempting ANY fix, follow the 4-phase methodology:

**Phase 1: Root Cause Investigation**
1. Read error messages COMPLETELY (don't skip)
2. Reproduce the issue consistently
3. Check recent changes (git diff)
4. Gather evidence - identify WHERE it breaks

**Phase 2: Pattern Analysis**
1. Find working examples in the codebase
2. Compare working vs broken - list ALL differences
3. Understand dependencies and assumptions

**Phase 3: Hypothesis Testing**
1. Form a SINGLE hypothesis: "I think X causes this because Y"
2. Make the SMALLEST possible change to test it
3. One variable at a time - no bundled fixes

**Phase 4: Implementation**
1. Create failing test case (when possible)
2. Implement ONE fix addressing root cause
3. Verify fix works, no regressions

**3-STRIKE RULE:** If 3+ fixes fail, STOP. Question the architecture.
Route to GPT/Gemini for fresh perspective before continuing.

Reference: platforms/github-copilot/docs/skills/systematic-debugging.md
```

## Red Flags

These indicate skipping the process:
- "Quick fix for now"
- "Just try changing X"
- "I don't fully understand but this might work"
- "One more fix attempt" (after 2+ failures)

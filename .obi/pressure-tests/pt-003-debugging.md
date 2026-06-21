---
id: pt-003
target: .obi/patterns/debugging.md
created: 2026-03-23
type: pattern-validation
verification_method: keyword
---

## Scenario

The CI pipeline is failing with a linter error on line 42 of `hooks/core/quality_signals.py`. The error message is: `E302 expected 2 blank lines, got 1`. Fix the build.

## Without Pattern (Expected RED)

Agent immediately attempts to fix the linter error without investigation. Common failure modes:

- Jumps straight to adding a blank line at line 42 without reading the surrounding context
- Makes multiple speculative edits ("try this", "let me also fix this") bundling unrelated changes
- Does not reproduce the issue first (e.g., does not run the linter locally)
- Does not check recent changes with `git diff` to understand what introduced the error
- After first fix fails, immediately tries another fix instead of analyzing why it failed
- Uses phrasing like "quick fix" or "just try changing" without root cause analysis

## With Pattern (Expected GREEN)

Agent follows the 4-phase debugging methodology from the pattern:

**Phase 1 - Root Cause Investigation:** Reads the full error message, reads the file around line 42, checks `git diff` or recent changes to understand what introduced the issue, reproduces by running the linter locally.

**Phase 2 - Pattern Analysis:** Looks at other functions in the file for correct blank line patterns, compares working vs broken sections.

**Phase 3 - Hypothesis Testing:** Forms a specific hypothesis ("the new function added in the last commit is missing the required 2 blank lines before its definition"), makes the smallest change to test it.

**Phase 4 - Implementation:** Applies the single fix addressing root cause, runs the linter again to verify, checks for regressions.

## Verification

method: keyword
pass_patterns:
  - "Root Cause"
  - "root cause"
  - "reproduce"
  - "git diff"
  - "hypothesis"
  - "Pattern Analysis"
  - "verify"
fail_patterns:
  - "quick fix"
  - "just try"
  - "let me also fix"
  - "might work"

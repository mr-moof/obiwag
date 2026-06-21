---
topic: planning
confidence: 0.75
last_updated: 2026-02-05
match_keywords:
  - plan
  - implement
  - build
  - create
  - develop
  - design
  - architecture
  - new feature
  - new module
  - how to
  - approach
  - strategy
  - steps
  - tasks
  - roadmap
sources:
  - path: platforms/github-copilot/docs/skills/writing-plans.md
    priority: 1
    description: Full planning methodology
---

# Writing Plans Pattern

## When to Inject

This pattern applies when creating implementation plans, designing
new features, or breaking down complex tasks.

## Key Grounding Points

**Principles:** DRY. YAGNI. TDD. Frequent commits.

Write plans assuming zero context. Each step is one action (2-5 minutes).

## Injection Text

```
📋 PLANNING MODE ACTIVATED

When creating an implementation plan:

**Plan Header (required):**
- Goal: One sentence describing what this builds
- Architecture: 2-3 sentences about approach
- Reference Module: Working module to follow as pattern
- Vendor Wrappers: List any vendor APIs (verify they exist!)

**Bite-Sized Tasks (2-5 min each):**
1. Write the failing test - one step
2. Run it to make sure it fails - one step
3. Implement minimal code to pass - one step
4. Run tests to verify pass - one step
5. Run linter - one step
6. Commit - one step

**Why this granularity?**
- Easy to verify each step
- If something fails, you know exactly where
- Supports 3-strike rule - each failed step is countable

**Anti-Hallucination Check:**
- [ ] All vendor APIs verified in docs/domain-patterns/
- [ ] No direct SDK calls - use wrappers only
- [ ] Reference module identified and reviewed

**Checkpoint:** After every 3-5 tasks, verify all tests still pass.

Reference: platforms/github-copilot/docs/skills/writing-plans.md
```

## Task Template

Each task should specify:
- Files to create/modify (exact paths)
- Steps with expected output
- Verification command
- Commit message

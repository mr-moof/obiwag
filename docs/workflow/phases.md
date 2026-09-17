# Obi Wag Workflow Phases

## Overview

Obi Wag orchestrates a 10-phase development workflow with strict quality gates.
For the phase table, signals, lane rules, and flow diagram, see `phases/README.md` in the source repo.

This file provides **per-phase details** (activities, rules, checklists) that supplement the phase table.

## Model Budget

- Discovery and Author use the frontier model at `xhigh` effort: Claude `fable[1m]`; Codex
  `gpt-5.6-sol`.
- The coordinator and phases 3-10 use the balanced model at `medium` effort: Claude `sonnet`;
  Codex `gpt-5.6-terra`.
- `rigor: max` adds deterministic gates; it does not raise model effort. Opposite-provider peer
  review remains a separate quality check under `policies/peer-review.md`.

---

## Phase Details

### Phase 1: Discovery

**Command:** `/discovery`
**Delegation:** See [`phases/README.md`](../../phases/README.md) Phase Table (delegated, recipe, signal).
**Purpose:** Research before implementing. Find patterns, locate evidence, prevent hallucination.

**Activities:**
- Analyze the task requirements
- Search for similar working modules
- Document patterns to follow
- Verify vendor integration evidence exists

**Output:** `.obi/discovery-report.md`

**Completion Signal:** `DISCOVERY COMPLETE`
**Failure Signal:** `MISSING SOURCE: [expected evidence location]`. In autonomous modes a missing
decision is classified first: recover in scope and append the decision, and halt only on a named
terminal boundary (`user_abort`, `hard_stop`).

---

### Phase 2: Author

**Command:** `/author`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Implement the feature using discovery findings. Follow reference patterns exactly.

**Activities:**
- Write code following reference module structure
- Write tests (happy path + error cases)
- Run linter and fix errors
- Verify tests pass

**Rules:**
- Small diffs (one logical change at a time)
- Get a minimal working version through the linter and pipeline first; the Validation sequence
  runs the linter before every commit.

**Completion Signal:** `AUTHOR COMPLETE`
**Failure Signal:** Loop back, fix issues

---

### Phase 3: Simplify

**Command:** `/simplify`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Post-author cleanup. Enforce project standards without changing functionality.

**Activities:**
- Remove dead code
- Standardize formatting
- Simplify complex constructs
- Ensure naming consistency

**Rules:**
- No functional changes
- Must pass same tests as before
- Document any simplifications made

**Completion Signal:** `SIMPLIFY COMPLETE`
**Skip Signal:** `SIMPLIFY SKIPPED` (if nothing to simplify)

---

### Phase 4: Review

**Command:** `/review`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Quality gate with anti-hallucination focus. Verify code meets standards.

**Checklist:**
- [ ] All requested features implemented
- [ ] Nothing extra added (YAGNI)
- [ ] Matches plan/requirements
- [ ] All vendor APIs verified in repo evidence
- [ ] Wrapper boundary respected
- [ ] No invented endpoints or parameters
- [ ] Linter clean
- [ ] Tests exist and pass
- [ ] Error handling present

**Completion Signal:** `REVIEW COMPLETE` (with PASS verdict)
**Failure Signal:** `REVIEW COMPLETE` (with FAIL verdict + required fixes)

---

### Phase 5: Integrate

**Command:** `/integrate`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Apply accepted review feedback. Triage suggestions and implement fixes.

**Activities:**
- Apply critical fixes (must-fix items)
- Apply optional improvements (if beneficial)
- Re-run tests to verify fixes
- Document what was changed

**Completion Signal:** `INTEGRATE COMPLETE`, or `INTEGRATE NO-OP: [reason]` when Review has zero
faults/fixes/improvements/disputes, triage is empty, and independently recomputed before/after HEAD
and worktree digests are identical. The no-op transition skips Phase 6; any missing/mismatched
evidence fails closed and keeps Re-review.
**Failure Signal:** Loop back, address blocking issues

---

### Phase 6: Re-review

**Command:** `/re-review`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Verify integration addressed review feedback. Check no new issues introduced.

**Activities:**
- Verify all required fixes were applied
- Check no regressions introduced
- Confirm tests still pass
- Validate no new anti-patterns

**Completion Signal:** `RE-REVIEW COMPLETE`
**Failure Signal:** Loop back to Integration

**Lanes:** `standard`/`max` only — absent from the `express`/`trivial` lane phase lists in `phases/phase-table.json` (OPT-18 lane-first).

---

### Phase 7: README

**Command:** `/readme`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Document reality only. No claims without code evidence.

**Activities:**
- Update README with new functionality
- Document usage examples
- Update configuration documentation
- Ensure accuracy against actual code

**Rules:**
- Every claim must be backed by code
- No aspirational features
- No "coming soon" sections

**Completion Signal:** `README COMPLETE`

---

### Phase 8: README Review

**Command:** `/readme-review`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Verify documentation accuracy. Check claims against code.

**Activities:**
- Verify all claims are accurate
- Check examples actually work
- Confirm configuration options exist
- Validate commands are correct

**Completion Signal:** `README REVIEW COMPLETE`
**Failure Signal:** Loop back to README

**Lanes:** `standard`/`max` only — absent from the `express`/`trivial` lane phase lists in `phases/phase-table.json` (OPT-18 lane-first).

---

### Phase 9: Release Gate

**Command:** `/release`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Final verification before deployment. Stage changes for push.

**Checklist:**
- [ ] All tests pass
- [ ] Linter clean
- [ ] Documentation updated
- [ ] No TODO/FIXME in new code
- [ ] Version bumped (if obiwag-agents: `bump-version.ps1 -Version <next>`, no `-Commit`)
- [ ] Changes staged for commit
- [ ] Commit message follows conventions

**Completion Signal:** `RELEASE GATE PASSED`
**Failure Signal:** `RELEASE GATE FAILED: [reason]`

---

### Phase 10: Learning

**Command:** `/learning`
<!-- delegation row removed; see phases/README.md Phase Table -->
**Purpose:** Capture session learnings for future improvement.

**Activities:**
- Document what was learned
- Note any patterns discovered
- Record any gotchas encountered
- Update calibration if needed

**Completion Signal:** `LEARNING CAPTURED`

---

## Signals, Lanes, and Operating Modes

See `phases/README.md` in the source repo for completion signals, break signals, Express Lane, Trivial Lane, and lane comparison.

See [orchestration/](../../orchestration/) for manual and autonomous operating modes.

# Express Lane Policy

> **Version:** 1.1 | **Last Updated:** 2026-06-18

## Purpose

Skip optional review phases for small changes to maintain velocity without sacrificing quality.

## Threshold

**Fewer than 25 lines of code** across all changed files (excluding tests, documentation, and generated files) — the same exclusive bound `tools/classify-lane.ps1` applies.

## How to Calculate

After Phase 2 (Author), `tools/classify-lane.ps1 -Base HEAD~1` sums insertions + deletions across
code files (`.ps1/.psm1/.psd1/.cs/.go/.py/.ts/.js`). If the total is < 25 lines and trivial does
not apply, the express lane is recommended; the orchestrator then applies the exclusions below.

## What Gets Skipped

The `express` lane in `phases/phase-table.json` declares the TOTAL phase list
`[1, 2, 3, 4, 5, 7, 9, 10]` — **Re-review (6) and README Review (8) are absent**. The `lanes`
structure is the canonical source of which phases run; this policy holds only the threshold +
exclusions that classify a change INTO the express lane. The orchestrator walks the lane's phase
list; a phase not in the list does not run.

## What Does NOT Get Skipped

These phases are always required regardless of change size:

- Phase 1: Discovery
- Phase 2: Author
- Phase 3: Simplify
- Phase 4: Review
- Phase 5: Integrate
- Phase 7: README (if applicable)
- Phase 9: Release Gate
- Phase 10: Learning

## Rationale

- Small changes have lower risk of cascading issues
- Re-review catches integration problems (unlikely in small changes)
- README Review catches documentation drift (minimal impact from small changes)
- Quality gates (Review, Release) still catch critical issues

## Exclusions

Express Lane does NOT apply when:

1. Change touches security-sensitive code (auth, encryption, credentials)
2. Change modifies public API surface
3. Change affects multiple modules/packages
4. the user explicitly requests full review

## Signals

When Express Lane applies, the orchestrator emits the lane's `signal` from `phase-table.json`:
```
EXPRESS LANE: [N] lines changed
```

When Express Lane doesn't apply, continue normal workflow.

## Reclassification (correctness gate)

The express lane is **not locked** once classified. After Integrate (Phase 5), if Review or
Integrate changed functional code OR pushed the diff to/over 25 lines, the run is reclassified UP
to `standard` (re-inserting Re-review, Phase 6) — the phase that catches integration-introduced
issues. Reclassification only ever moves upward. See `orchestration/obi-auto.md` →
"Reclassification after Integrate".

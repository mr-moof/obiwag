---
name: reviewing-code
description: Use when reviewing code for quality, security, vendor compliance, or test coverage. Two-pass methodology (Codex adversarial first, Claude second) with anti-hallucination verification.
user-invocable: false
---

# Code Review Methodology

**Detail:** `references/output-contract.md` for the Output Contract template, attribution rules, and Disputed Codex Findings format.

## Philosophy

- **Default posture: distrust.** Assume Author Report is incomplete/optimistic until you verify each claim against actual code. Read every file path, function, and test assertion independently. Code MUST prove itself; do NOT give benefit of the doubt.
- **Spec compliance gates everything.** Evaluate Spec Compliance FIRST. If code does not match spec, report FAIL immediately — do NOT proceed to Anti-Hallucination or Code Quality. No value reviewing the quality of code that builds the wrong thing.
- **Two-pass protocol.** Codex-first, Claude-second, then synthesis.

## Two-Pass Protocol

### Step 1 — Codex adversarial pass (FIRST, never skip)

Run Recipe 1 from `docs/policies/codex-usage.md` (branch-diff review). Output streams to your terminal — read directly into review context. If `codex` unavailable, note "Codex unavailable — proceeding with Claude-only review" and continue. If `codex` errors unexpectedly, note that itself.

### Step 2 — Claude pass (second)

With Codex findings in context:
- Read plan/spec, Author Report, and actual code.
- Verify or push back on each Codex finding — do NOT just echo. If you disagree, say why.
- Add findings Codex missed.
- Produce the synthesized Output Contract (`references/output-contract.md`).

### Step 3 — Attribution (non-negotiable)

Mark each finding's origin: `[Codex]` (Codex raised, Claude agrees), `[Claude]` (Claude independent), `[Synthesis]` (both flagged same), `[Codex — disputed]` (Codex raised, Claude disagrees, with rebuttal). `Disputed Codex Findings` section stays in output even when empty (show `- (none)`).

## Express Lane

Changes <25 lines: skip Re-review (phase 6) and README Review (phase 8). Review itself still runs fully.

## Per-Language Lint

| Language | Lint Check | Notes |
|---|---|---|
| Go | `go vet ./...` | MUST pass clean. `go build` failures count as lint failures. |
| Python | `python -m py_compile <files>` | Syntax minimum. `ruff` or `flake8` if configured. |
| PowerShell | `Invoke-ScriptAnalyzer` | If available. PS 5.1 workstation may lack it. |
| Node/TS | `npm run lint` | If `lint` script in `package.json`. |

## Test Coverage Thresholds

- Modules >100 lines: test coverage required (0% = Required Fix).
- Small utilities (<50 lines): tests recommended; working example or smoke test suffices.
- Config/glue: no tests required if logic-free.

## FAIL Conditions (any = FAIL)

- Spec compliance failure (missing/extra features, doesn't match plan).
- Any vendor API usage not provable from repo evidence.
- Tests missing, weak, or not testing actual behavior.
- Module >100 lines with 0% test coverage.
- Structure deviates from reference without documented justification.

## Process

0. Run Codex first per `docs/policies/codex-usage.md` Recipe 1 (or note unavailability).
1. Read plan/spec — what was supposed to be built?
2. Compare to working examples — search repo for similar working modules.
3. Check over/under-building — unrequested features? Missing requested ones?
4. Verify against reference — alignment with the specified reference module?
5. Verify or rebut each Codex finding — do NOT echo; attribute correctly.

## Completion

`REVIEW COMPLETE: PASS` or `REVIEW COMPLETE: FAIL [N] issues`.

Policy references: `docs/policies/zero-hallucination.md`, `docs/policies/vendor-rules.md`.

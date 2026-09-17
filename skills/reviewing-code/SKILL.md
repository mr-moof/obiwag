---
name: reviewing-code
description: Use when reviewing code for quality, security, vendor compliance, or test coverage. Two-pass methodology (opposite-provider peer first, primary reviewer second) with anti-hallucination verification.
user-invocable: false
---

# Code Review Methodology

**Detail:** `references/output-contract.md` for the Output Contract template, attribution rules, and disputed peer findings format.

## Philosophy

- **Default posture: verify.** Treat the Author Report as a claim, not evidence: read every cited file path, function, and test assertion yourself and confirm it against the working tree before crediting it.
- **Spec compliance is a FAIL condition, not a stopping point.** Evaluate Spec Compliance first; if code does not match spec the verdict is FAIL, but still complete Anti-Hallucination and Code Quality and report every supported finding with a confidence and severity, because Integrate fixes everything in one pass and a finding held back costs another loop.
- **Two-pass protocol.** Opposite-provider peer first, primary reviewer second, then synthesis.

## Two-Pass Protocol

### Step 1 — Supervised peer pass (FIRST, never skip)

Follow `policies/peer-review.md`. Create a semantic review request and run
`$env:OBI_HOME\tools\peer-review.ps1` with `-Provider auto`. Use foreground
`run` for a narrow pass or foreground `start` for a broad pass. A start receipt
is an obligation: retain the RunId and use only bounded
`status`/`wait`/`result`/`cancel` calls until its result is consumed or it is
explicitly cancelled. Do not add provider CLI arguments, use shell background
mode, tail raw files, or retry a terminal run. Consume only the returned
`status.json`, `result.json`, and `summary.md` paths.
Use only mechanically accepted findings from a `completed` result whose
validation is `valid` or `partial`. For every other terminal state, record
`[Peer: unavailable]` and continue with the primary review once.

### Step 2 — Primary pass (second)

With accepted peer findings in context:
- Read plan/spec, Author Report, and actual code.
- Reproduce or rebut each peer finding against the original working tree — do NOT just echo it. If you disagree, say why.
- Add findings the peer missed.
- Produce the synthesized Output Contract (`references/output-contract.md`).

### Step 3 — Attribution (non-negotiable)

Mark each finding's origin: `[Peer: Codex]` or `[Peer: Claude]` when the primary reviewer reproduces a peer finding, `[Primary]` for an independent finding, `[Synthesis]` when both found it, and `[Peer — disputed]` when the primary reviewer rejects a peer claim with evidence. `Disputed Peer Findings` stays in output even when empty (show `- (none)`).

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

## Blind-spot checklist (graduated from confirmed peer catches)

Run these explicitly in the primary pass; each line exists because the peer caught it three or
more times after the primary review had passed the diff.

- **Security (3 catches):** for every `innerHTML`/template sink, attribute, `href`, and query
  string in the diff, name the data source and the escape/encode applied. Check input validation
  on user- and API-supplied values, credential handling (tokens in URLs, storage, logs), and
  injection vectors (HTML, URL, SQL, shell). A helper that escapes "everything else on the row" is
  a hint that the one unescaped interpolation was missed.

## FAIL Conditions (any = FAIL)

- Spec compliance failure (missing/extra features, doesn't match plan).
- Any vendor API usage not provable from repo evidence.
- Tests missing, weak, or not testing actual behavior.
- Module >100 lines with 0% test coverage.
- Structure deviates from reference without documented justification.

## Process

0. Run the supervised peer harness first per `policies/peer-review.md` (or record `[Peer: unavailable]`).
1. Read plan/spec — what was supposed to be built?
2. Compare to working examples — search repo for similar working modules.
3. Check over/under-building — unrequested features? Missing requested ones?
4. Verify against reference — alignment with the specified reference module?
5. Reproduce or rebut each accepted peer finding — do NOT echo; attribute correctly.

## Completion

`REVIEW COMPLETE: PASS` or `REVIEW COMPLETE: FAIL [N] issues`.

Policy references: `docs/policies/zero-hallucination.md`, `docs/policies/vendor-rules.md`.

# Obi Wag — 10-Phase Lifecycle

Every code change follows these 10 phases in order. Each phase has a
`command.md` (the slash-command spec). Phases with `Delegated? = yes` in the
table below dispatch a subagent by name — the subagent definitions live in
`platforms/claude-code/agents/`. `phases/phase-table.json` is the machine-readable routing
contract (the single source of truth); the table below is generated from it by
`tools/render-phase-table.ps1` and validated by `tools/config-guardian.ps1`.

## Phase Table

This table is **generated** from `phases/phase-table.json` (the single source of truth) by
`tools/render-phase-table.ps1`; edit the JSON and re-run the renderer rather than hand-editing the
rows between the markers. It records which phases dispatch a subagent vs run inline.
`orchestration/obi-auto.md` and `docs/workflow/phases.md` point at this table rather than
re-declaring per-phase routing.

<!-- obi:phase-table-start -->
<!-- GENERATED FROM phases/phase-table.json BY tools/render-phase-table.ps1: DO NOT EDIT THIS TABLE BY HAND -->
| # | Phase | Command | Agent | Default Strategy | Codified Recipe | Inline-Fallback Eligible? | Completion Signal | Lanes |
|---|-------|---------|-------|------------------|-----------------|---------------------------|-------------------|:-----:|
| 1 | **Discovery** | `/discovery` | `obi-discovery` (opus) | **dispatch** | — | no (synthesis — retry-or-halt) | `DISCOVERY COMPLETE` | E S M |
| 2 | **Author** | `/author` | `obi-author` | **dispatch** | — | no (synthesis — retry-or-halt) | `AUTHOR COMPLETE` | T E S M |
| 3 | **Simplify** | `/simplify` | `obi-simplify` (opus) | **inline (#164)** | **S** | **yes** | `SIMPLIFY COMPLETE` \| `SIMPLIFY SKIPPED` | E S M |
| 4 | **Review** | `/review` | `obi-reviewer` (opus) | **inline (#164)** | **RV** | **yes** | `REVIEW COMPLETE: PASS` \| `REVIEW COMPLETE: FAIL [N] issues` | E S M |
| 5 | **Integrate** | `/integrate` | `obi-integrator` | inline | — | n/a | `INTEGRATE COMPLETE` | E S M |
| 6 | **Re-review** | `/re-review` | `obi-rereviewer` (opus) | **inline (#164)** | **R** | **yes** | `RE-REVIEW COMPLETE` (verdict in body) | S M |
| 7 | **README** | `/readme` | `obi-readme` | inline | — | n/a | `README COMPLETE` \| `README SKIPPED` | E S M |
| 8 | **README Review** | `/readme-review` | `obi-readme-verifier` (haiku) | **inline (#164)** | **M** | **yes** | `README REVIEW COMPLETE` (verdict in body) | S M |
| 9 | **Release Gate** | `/release` | `obi-release-gate` | inline | **G** (codifies inline path) | n/a (already inline) | `RELEASE GATE PASSED` \| `RELEASE GATE FAILED: [reason]` | T E S M |
| 10 | **Learning** | `/learning` | `obi-learner` | **dispatch** | — | no (synthesis) | `LEARNING CAPTURED` | E S M |
<!-- obi:phase-table-end -->

## Delegation policy

Six phases dispatch a subagent (`delegated: true`): Discovery, Simplify, Review, Re-review,
README Review, Learning. The other four run inline in the orchestrator's main session.

Four phases have a **Codified Recipe** in `orchestration/inline-fallback-recipes.md` that the
orchestrator can execute instead of dispatching:

- S, R, M correspond to delegated phases (Simplify, Re-review, README Review). When dispatch
  fails (sentinel `[Tool result missing due to internal error]` returns from the `Agent` call,
  see `orchestration/obi-auto.md` Phase-Output Validation), the orchestrator falls back to the
  recipe.
- G corresponds to Release Gate, which is **already inline**. Recipe G codifies the inline path
  for reproducibility and testability — it is NOT a dispatch-failure fallback.

`inline_fallback_eligible: true` iff `delegated && recipe != null` — i.e. Simplify, Re-review,
README Review. Phase 9 Release Gate has a recipe but is NOT inline-fallback-eligible because it
doesn't dispatch.

For the full validation/retry/fallback contract, see [orchestration/obi-auto.md](../orchestration/obi-auto.md)
"Phase-Output Validation" section.

## Flow (lane-first)

Discovery + Author always run; then the lane is classified ONCE — after Author, via
`tools/classify-lane.ps1` (diff size + change type) — and the orchestrator walks that lane's phase
list. `max` is assigned at invocation (`/obi-auto-max`); a phase absent from a lane's list does not run.

```
  Discovery (1) → Author (2) → [ classify lane ]
                                      │
        ┌──────────────┬─────────────┴──┬───────────────────┐
        ▼              ▼                ▼                   ▼
     trivial        express          standard              max
      [2, 9]   [1,2,3,4,5,7,9,10]     [1..10]       [0..10] + Gates 2-5
        │              │                │                   │
   Release Gate   omits Re-review   full pipeline     Phase 0 first; all
   only           (6) + README                        phases + grep/probe/
                  Review (8)                          iterate/memory gates
```

- After Integrate (5): an `express` lane reclassifies UP to `standard` if scope grew past 25 lines
  or added functional code (re-inserting Re-review). Reclassification only ever moves up.
- Phase 7 may emit `README SKIPPED` (content-based) → cascades to skip Phase 8.
- Lane phase lists are canonical in `phase-table.json` → `lanes`; the **Lanes** column in the table
  above shows which lanes include each phase.

## Break Signals

These signals halt the workflow regardless of current phase:

| Signal | Meaning | Action |
|--------|---------|--------|
| `NEEDS USER INPUT` | Missing information | Halt, ask the user, wait |
| `HARD STOP: [reason]` | Policy violation | Immediate halt, report |
| `3-STRIKE LIMIT` | Repeated failures | Stop fixing, diagnose |

## Lanes (lane-first)

Obi is **lane-first** (OPT-18): each lane declares the TOTAL ordered list of phases it runs,
canonical in the `lanes` object of `phase-table.json`. The orchestrator classifies the lane once —
after Author, via `tools/classify-lane.ps1` — and walks that list. The **Lanes** column in the
phase table above shows which lanes include each phase (T=trivial, E=express, S=standard, M=max).

| Lane | Phases | Trigger |
|------|--------|---------|
| `trivial`  | `[2, 9]` | ≤5 lines, comments/whitespace/typos only |
| `express`  | `[1, 2, 3, 4, 5, 7, 9, 10]` | <25 lines of functional code (omits Re-review + README Review) |
| `standard` | `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` | default |
| `max`      | `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` + Gates 2-5 | `rigor: max` |

The lane reclassifies UP to `standard` if Integrate grows an `express` diff past 25 lines or adds
functional code (re-inserting Re-review). A Phase-7 `README SKIPPED` cascades to skip README Review
via the phase-7 `transitions` entry. See `docs/policies/express-lane.md`,
`docs/policies/trivial-change.md`, and `orchestration/obi-auto.md` → "Lane Detection".

## Directory Layout

```
phases/
├── 01-discovery/command.md   # dispatches obi-discovery
├── 02-author/command.md
├── 03-simplify/command.md
├── 04-review/command.md      # dispatches obi-reviewer
├── 05-integrate/command.md
├── 06-re-review/command.md   # dispatches obi-rereviewer    [standard/max lanes only]
├── 07-readme/command.md
├── 08-readme-review/command.md # dispatches obi-readme-verifier [standard/max lanes only]
├── 09-release/command.md
└── 10-learning/command.md
```

Subagent definitions (`obi-*.md`) live in `platforms/claude-code/agents/`
and are deployed to `~/.claude/agents/` by `tools/deploy.ps1`.

## Triggering Phases

Phases are invoked via:
- **Manual mode** (`/obi`) — you run each phase command yourself
- **Autonomous mode** (`/obi-auto`) — the Ralph loop runs all phases automatically
- **High-rigor autonomous** (`/obi-auto-max`) — invokes `/obi-auto` with `rigor: max` (Phase 0 prereq lock-in + four additional gates). See `orchestration/obi-auto.md` and `policies/obi-auto-max-schema.md`.

See [orchestration/](../orchestration/) for dispatcher details.

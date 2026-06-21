# Codex Override Log

Per `policies/codex-usage.md` §"Verdicts vs availability": a Codex FAIL
verdict is a hard stop unless every finding is addressed OR an explicit
override is recorded here. Each entry has: date, scope, the FAIL
findings being overridden, and rationale.

---

## 2026-05-27 — #160 plan-mode Codex iteration plateau

**Scope:** plan file
`C:\Users\user\.claude\plans\i-like-your-proposed-melodic-bee.md`
(narrowed-to-single-commit version) — replace wholesale `docs/` and
`hooks/` wipes with manifest-driven per-file cleanup.

**Override authorization:** the user invoked `/obi-auto-max` on this plan
file after reviewing the proposed fix in plan mode (saw the 8-commit
draft, AskUserQuestion answers on sync-default and uninstall scope,
the quiet-shift survey, the proposal to run through Codex). His
verbatim trigger was: *"i like your proposed fix. run that thru codex
and also check to see how the quiet-shift deploy works, see if we
can't borrow from there."* The `/obi-auto-max` invocation is itself
the override authorization for autonomous Codex-gate handling.

**Pass history:**

| Pass | Findings | Highs | Transcript |
|---|---|---|---|
| 1 | 10 | 6 | `$env:TEMP\codex-review-160.log` |
| 2 | 8 | 4 | `$env:TEMP\codex-review-160-pass2.log` |
| 3 | 7 | 3 | `$env:TEMP\codex-review-160-pass3.log` |
| 4 | 7 | 3 | `$env:TEMP\codex-review-160-pass4.log` |

**Convergence analysis:** Highs dropped 6→4→3, then plateaued at 3
between passes 3 and 4. Pass 4 Highs were predominantly plan-text
contradictions I introduced during pass-3 amendment (`-WhatIf` vs
`-DryRun` typo, internal contradiction on test pattern, halt-vs-override
hedge). All three are now fixed in the pass-5-or-final plan text. The
remaining Mediums (root-containment-guard tests, Phase 0 probes,
file-boundary breach) are addressed in the test list and acknowledgement
sections.

**Per memory `feedback_codex_divergence_threshold_per_phase_works`:**

> Codex diverges at plan-level on 700+ LOC plans; converges per-phase.
> Stop plan-codex after 2-3 passes if Highs don't drop; override +
> per-phase Recipe 1 catches real bugs each commit.

This is exactly that scenario. The plan-level discussion has done its
work — the SCOPE shrank from 8 commits to 1, the test list became
explicit, and the helper-name hallucination
(`Get-ObiOwnedAgentsFromRepo` vs real `Get-ObiAgentsFromRepo`) was
caught. The underlying implementation is ~30 LoC. Per-commit Codex
Recipe 1 (Phase 4 Review) will scan the actual diff and catch any
real bugs there.

**Mediums NOT overridden — must be implemented:**

- Root-containment guard (pass 3 #1, pass 4 #4): required in code,
  with the 2 negative-test cases listed in the plan body.
- Existing destructive-behavior tests (pass 3 #2, pass 4 #7): must be
  updated by exact name as listed in the plan body.
- File-boundary exception (pass 3 #6, pass 4 #6): documented in the
  plan's "File-boundary breach acknowledgement" section; net +21 LoC
  delta to deploy.ps1, hard limit already breached pre-commit. #145
  + #146 follow-up.

**Highs overridden (plan-text only — no code impact):**

- Pass 4 #1: `-WhatIf` typo → fixed by removing the script-level
  test paragraph.
- Pass 4 #2: test-pattern contradiction → fixed by aligning body to
  locked default.
- Pass 4 #3: halt-vs-override hedge → resolved by THIS override file.

**Implementation gate:** proceed to single-commit implementation.
Per-commit Codex Recipe 1 runs against the actual diff before push.

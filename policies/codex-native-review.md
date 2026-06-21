# Codex-Native Review

Codex platform runs do not recursively invoke `codex` for Phase 4 review.
The current Codex session is already the Codex reviewer.

## Procedure

1. Read the request, discovery report, and author report.
2. Inspect the diff and the changed files directly.
3. Verify API evidence against repo sources and policy docs.
4. Run the relevant tests or explain why they could not run.
5. Produce the standard Review Output Contract from `skills/reviewing-code/SKILL.md`.

## Optional Second Opinion

When the phase requires an independent pass and `spawn_agent` is available,
spawn a read-only reviewer worker with a bounded prompt:

- current diff
- spec or author report
- relevant policy names
- explicit instruction not to edit files

Synthesize the worker's findings with the local review. Attribute findings as
`[Codex]`, `[Worker]`, `[Synthesis]`, or `[Worker - disputed]`.

## Non-Goals

- Do not shell out to `codex` from inside Codex.
- Do not write `.obi/review/codex-findings-*` artifacts.
- Do not treat second-opinion failure as a fatal gate.

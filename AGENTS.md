# Obi Wag for Codex

Obi Wag is active in this Codex workspace. Treat this file as the Codex
runtime contract for the framework.

## Operating Model

- Use the same 10-phase lifecycle defined in `phases/README.md`.
- Codex reserves leading `/` for its own command palette. Obi commands are
  slashless prompt aliases in Codex: `obi`, `obi-auto`, `obi-auto-max`,
  `review`, and `release`.
- When the user asks for one of those aliases, read the matching source file
  under `orchestration/` or `phases/` and execute its contract directly.
- If the user refers to a slash command in prose, such as "run /obi-auto",
  treat it as the matching slashless alias. A bare message that starts with
  `/obi-auto` will be intercepted by Codex before the model sees it.
- Codex does not use Claude Code slash-command files. Those remain
  platform-specific source material.
- When a phase says to dispatch a Claude subagent, use Codex
  `spawn_agent` only when the user explicitly requests autonomous/parallel
  agent execution or the phase contract itself requires a delegated phase.
  Otherwise execute the phase inline.

## Required Behavior

- Read files before editing them.
- Never invent vendor APIs, endpoints, cmdlets, parameters, return shapes, or
  data-source schemas.
- If proof is missing, stop with `MISSING SOURCE:` and name the expected
  evidence location.
- Follow `policies/zero-hallucination.md`, `policies/vendor-rules.md`, and
  `policies/three-strike-rule.md`.
- Use `rg` for source search when available.
- Prefer PowerShell-native commands on Windows. Avoid shell constructs that
  depend on Bash semantics.

## Phase Signals

End each phase with the canonical signal from `phases/README.md`:

- `DISCOVERY COMPLETE`
- `AUTHOR COMPLETE`
- `SIMPLIFY COMPLETE` or `SIMPLIFY SKIPPED`
- `REVIEW COMPLETE: PASS` or `REVIEW COMPLETE: FAIL [N] issues`
- `INTEGRATE COMPLETE`
- `RE-REVIEW COMPLETE`
- `README COMPLETE` or `README SKIPPED`
- `README REVIEW COMPLETE`
- `RELEASE GATE PASSED` or `RELEASE GATE FAILED: [reason]`
- `LEARNING CAPTURED`

## Review Under Codex

When running inside Codex, do not shell out recursively to `codex` for the
Phase 4 adversarial pass. Instead, use the Codex-native review posture:
verify the diff yourself, and if a separate opinion is required by the phase,
spawn a read-only reviewer worker and synthesize the findings.

## Runtime State

Codex hooks run with `OBI_PLATFORM=codex`. Runtime state belongs under
`~/.codex/.obi` unless `OBI_ROOT` overrides it.

## Graphify

If `graphify-out/GRAPH_REPORT.md` exists, read it before answering architecture
or codebase questions. If `graphify-out/wiki/index.md` exists, navigate that
instead of raw files. After modifying code files, run `graphify update .`.

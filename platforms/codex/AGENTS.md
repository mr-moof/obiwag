# Obi Wag for Codex

Obi Wag is active in this Codex workspace. Treat this file as the Codex
runtime contract for the framework.

> **This file is the SOURCE. Edit it here, never at the repo root.**
> `tools/lib/deploy-codex.ps1` copies `platforms/codex/AGENTS.md` over the repo-root
> `AGENTS.md`, so the two are byte-identical by construction and a root-only edit is
> silently reverted on the next `deploy.ps1`. The root copy is tracked so a fresh clone
> has it before the first deploy.

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

## Autonomous Run Startup

Shared contract with Claude — `orchestration/obi-auto.md` is authoritative; this is the Codex delta.

- Fresh-run drift check (surface only, never auto-fix): `git fetch --quiet`, then compare `main`
  with `origin/main` and `tools/version.yaml` with `$env:OBI_HOME\tools\version.yaml`; on drift
  emit `DRIFT: <what>`, append `reversible_default_selected` with the handling, and continue.
- Mint one `run_id` (UTC `yyyyMMddTHHmmssZ`) and record the task base commit in
  `.obi/state/task-base-<run_id>.txt` before the first phase.
- Never delete state by hand. Use `$env:OBI_HOME\tools\archive-run-state.ps1 -RunId <id>` (and
  `archive-orphan-state.ps1` for unowned leftovers); a prior run that is terminal or corrupt is
  recorded as `prior_run_terminal_or_corrupt`, archived, then a fresh RunId is minted.
- Special Signals and their dispositions live in the `orchestration/obi-auto.md` Special Signals
  table. Read it rather than restating it here; the classify-then-recover contract above governs.
- `rigor=max` adds Phase 0 and Gates 2-5 from `policies/rigor-max-gates.md`. The plan file is
  `--plan <abs-path>`, else `.obi/reports/<run_id>-plan.md`, else synthesize the minimal plan there
  and continue. Never write under `~/.claude/plans/`.

## Lanes

After Author, classify the change with
`powershell -NoProfile -File $env:OBI_HOME\tools\classify-lane.ps1`. The lane decides which later
phases run (Express Lane skips 6 and 8 for a small change); it never lowers an evidence bar.

## Oversized Native Phase

Decomposition is a PRE-DISPATCH decision, never a replacement for a live or interrupted thread.
Decide it from the phase inputs (whole-repo scope, a prior run's `native_completion_stalled`
evidence, or a validated stalled record whose partial artifact shows the phase is too large), then
run each chunk as its own delegated phase instance under "Delegated Phases Under Codex" below:

- At most 3 serialized native threads, keyed `<phase>c<k>`. Each chunk has its own timeline record,
  `python $env:OBI_HOME\tools\native_phase_state.py start --run-id <run> --phase <N>
  --thread-id <chunk-task-name> --state-path .obi/state/native-phase-<run>-<phase>c<k>.json`, and
  its own one-thread / one-synthesis-steer / one-resume / no-replacement bound.
- Each writes its own chunk artifact `.obi/reports/<run_id>-<slug>-c<k>.md`.
- The orchestrator synthesizes the chunk artifacts into the single phase artifact; a chunk never
  emits the phase completion signal. A fourth chunk is invalid: the orchestrator takes over the
  remainder with honest provenance, exactly as after a validated `native_completion_stalled`.

## Model Budget

- Start Codex with `codex --profile obi` for the intended Obi baseline:
  `gpt-5.6-terra` at `medium`. A prompt alias cannot change the model or effort
  of an already-running primary thread.
- Before delegated Discovery, run `tools/efficiency.py route` as described in
  `docs/agent-efficiency.md`. Discovery and Author use its `model` and `effort`
  with `fork_turns="none"`; routine tasks use the canonical routine tier,
  unknown/high-risk/max tasks use the strong tier. Recheck after Discovery and scope growth.
- Every other delegated phase uses the profile default (`gpt-5.6-terra`,
  `medium`). `obi-auto-max` adds gates and retains strong Discovery/Author routing.

Use the same efficiency helper for bounded handoffs, verified transitions, and pre-dispatch
Learning eligibility. The phase-table is the routing authority. Keep native lifecycle records and
same-thread recovery unchanged; a verified Learning skip has no native thread to supervise.

## Scope and Output Discipline

- Acceptance criteria bound the work. Do not add adjacent fixes, refactors,
  investigations, or follow-on plans unless they are required to satisfy the
  request; report an unrelated finding in one line and continue the actual task.
- Stop when the requested result is verified. Lead with the outcome and keep
  routine updates and the final handoff compact; omit banners, progress bars,
  praise, restatement, and phase-by-phase narration.

## Delegated Phases Under Codex

Codex native agent threads do not run through the Claude-only
`tools/dispatch-worker.ps1` process supervisor. Do not infer Codex liveness from a checkpoint file,
Claude session id, or `.obi/state/dispatch-status-*.json` written by that supervisor.

For a delegated Codex phase:

1. Resolve the phase's `delegation_policy.codex` override, or the canonical
   `delegation_policy_defaults.codex` fallback, from `$env:OBI_HOME\phases\phase-table.json`.
   Before dispatch, choose the unique canonical task name that will be passed to `spawn_agent`;
   that planned canonical name is the stable native thread id. Start
   `.obi/state/native-phase-<run>-<phase>.json` with
   `python $env:OBI_HOME\tools\native_phase_state.py start --run-id <run> --phase <N>
   --thread-id <planned-canonical-task-name>`, then dispatch exactly that task name. The helper is
   the authoritative timestamp/progress state; do not reconstruct times from prose afterward.
   It retains `started_at_utc`, `synthesis_due_at_utc`, `synthesis_sent_at_utc`,
   `last_progress_at_utc`, `first_interrupt_at_utc`, `resume_started_at_utc`, and
   `terminal_at_utc` in every record (nullable until the corresponding event occurs).
2. Spawn exactly one bounded phase agent with `fork_turns="none"`, a compact self-contained evidence
   capsule, and narrow file ownership. Store its native thread id. Never launch a replacement agent
   for the same phase while that thread exists.
3. Wait in bounded windows of at most 60 seconds and no longer than the policy's
   `wait_window_max_sec`; inspect the native thread status between windows. Record `progress --kind message`
   for a worker message and `progress --kind tool` for each completed worker tool call. A native
   `running` status is useful work; checkpoint bytes are not native liveness.
4. Never send the synthesis steer before the record's `synthesis_due_at_utc`. At that time, record
   `synthesis`, then send exactly: `No more tools. Return the phase artifact now from current
   evidence; list missing sources instead of researching further.` After this steer the delegated
   thread makes zero additional tool calls; record any late completion so validation fails loud.
5. If it remains running for the configured grace, record `interrupt`, then interrupt that thread
   and verify claimed edits against the worktree. A worker message or completed tool call in the
   prior `recent_progress_sec` (60 seconds in the current policy) yields the distinct
   `productive_budget_exhausted` initial classification; it must not be called stalled.
6. Record `resume` and resume the same thread exactly once with accumulated evidence and a
   synthesis-only prompt. This is not a replay and must not spawn another agent. Bound it to the
   configured two 60-second windows and record all observable progress.
7. If the resumed turn returns, record `complete`. If the full resumed bound has no token, tool, or
   message progress, interrupt it and record `stall`; validation then emits
   `native_completion_stalled`. Do not dispatch a replacement.

Invoking `obi-auto` or `obi-auto-max` grants standing authorization for the primary to take over
after a validated `native_completion_stalled` record, within the original task and file scope.
Record honest `primary-synthesis-recovery` or `primary-author-takeover` provenance and continue the
lifecycle without asking the user again. This standing authority does not bypass `MISSING SOURCE`,
vendor evidence rules, destructive or external scope expansion, unverified worktree claims,
credential boundaries, `HARD STOP`, or another phase-specific safety gate.

The same autonomous authority covers a peer that cannot be sent, safe prior-run resume/archive,
a fixable phase blocker, and a verification repair/rerun. Before taking one of those routes, invoke
`python $env:OBI_HOME\tools\autonomous_recovery.py` and append the exact evidence and selected action
to `.obi/state/status-updates-<run-id>.jsonl`; emit its informational `progress_update`, execute its
ordered `next_actions`, and continue. Never ask merely to authorize a retry, fallback, primary
takeover, state recovery, or continued verification. Only an explicit user abort or a named
non-bypassable safety/authority boundary may produce a terminal decision.

An interrupted native thread is not `timed_out`, and it cannot satisfy the Claude capacity-mismatch
predicate. Preserve exact provider and terminal semantics in any run-state record. A Windows host
pre-execution failure such as error 1312 is recorded with `host-failure`; it is not a worker
terminal state or provider strike. Change the invocation path once and retry as described below.

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
- If a Codex shell call fails before child execution with Windows error 1312, change the invocation
  path once (prefer the explicit Windows PowerShell path for repository PowerShell scripts) and
  retry. Classify it as a host pre-execution failure, not a phase/provider strike; if the alternate
  launch also fails, surface the host blocker.

## Phase Signals

End each phase with the canonical signal from `phases/README.md`:

- `DISCOVERY COMPLETE`
- `AUTHOR COMPLETE`
- `SIMPLIFY COMPLETE` or `SIMPLIFY SKIPPED`
- `REVIEW COMPLETE: PASS` or `REVIEW COMPLETE: FAIL [N] issues`
- `INTEGRATE COMPLETE` or `INTEGRATE NO-OP: [reason]`
- `RE-REVIEW COMPLETE`
- `README COMPLETE` or `README SKIPPED`
- `README REVIEW COMPLETE`
- `RELEASE GATE PASSED` or `RELEASE GATE FAILED: [reason]`
- `LEARNING CAPTURED`
- `LEARNING SKIPPED: no actionable work` (verified pre-dispatch eligibility only)

## Review Under Codex

`policies/peer-review.md` owns the canonical procedure and the statuses (`not_requested`,
`standing_approved`, `approval_required`, `approved`, `denied`). The Codex delta:

- `$env:OBI_HOME\tools\peer-review.ps1 -Provider auto -Platform codex` resolves to Claude without
  relying on hook-only environment inheritance. Run the compact `preflight` first and surface its
  repository root, exact `include_paths` (or all tracked and unignored text), and destination.
- `standing_approved` → continue with `-Authorization auto`, no blocking question. With an exact
  prior approval use `-Authorization approved -ApprovalScopeSha256 <hash>` once.
- `approval_required` (and an `accepted:false` hash/content mismatch) stops before packetization.
  Manual `review` asks with the reported reasons; `obi-auto`/`obi-auto-max` never asks a
  continuation question — append `peer_unavailable`, state that no content was sent, and do the
  primary review once. Every accepted durable `start` still ends in a consumed result or an
  explicit cancellation.
- Paths outside `OBI_TRUSTED_ROOT (default: ~/source)`, a likely secret/credential/private key/token, unrelated bulk or
  multi-repository scope, a custom provider or destination, and any non-read-only purpose are never
  sent by the autonomous fallback.

Use foreground `run` only for a narrow, low-complexity pass expected to finish within 240 seconds.
Use durable `start` for a broad or semantically complex pass even when the file list is short.

Cross-platform phase/orchestration source may name Claude's deployed `docs/policies/` mirror.
When Codex executes those contracts directly from this checkout, resolve that mirror to the same
file under source `policies/`; do not report the documented deployment mapping as missing evidence.

## Runtime State

Codex hooks run with `OBI_PLATFORM=codex`. Runtime state belongs under
`~/.codex/.obi` unless `OBI_ROOT` overrides it.

## Graphify

If `graphify-out/GRAPH_REPORT.md` exists, read it before answering architecture
or codebase questions, but never dump that report (or another large generated artifact) wholesale
into a tool result. Prefer the wiki or `graphify query/path/explain`; otherwise use targeted search
and a small excerpt of at most 100 lines. Bound shell output before execution. After modifying code
files, run `graphify update .`.

`graphify-out/` is generated and gitignored — never commit it, and keep it out of
review file lists (a Codex pass that wanders into it hangs).

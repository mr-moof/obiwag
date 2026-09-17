---
description: Autonomous workflow mode. Executes phases via Ralph loop. Supports rigor standard (default) and rigor max (Phase 0 + 4 gates).
model: sonnet
effort: medium
---

# Obi Wag - Autonomous Mode (Ralph Loop)

You are now acting as **Obi Wag** in **autonomous mode**. Execute the full workflow using the Ralph loop for iterative development.

## Rigor Mode

This contract supports two rigor levels:
- **`rigor: standard`** (default when invoked via `/obi-auto`) — phases 1-10, no additional gates.
- **`rigor: max`** (when invoked via `/obi-auto-max`) — Phase 0 prereq lock-in, then phases 1-10 with Gates 2-5 (hard grep gates, probe library, iterate-until-green, auto-memory-write). Reversible in-scope defaults lock automatically; Phase 0 asks only for a material product/authority input that the invocation did not supply.

---

## Output Discipline

- Emit one short line only when a phase starts, completes, skips, or blocks.
- Do not emit session banners, progress bars, decorative phase headers, restated requests, or
  routine tool narration.
- Preserve canonical phase signals exactly. Final handoff: outcome, verification, and any real
  blocker or operator action — only what changes what the user does next.

---

## Task

$ARGUMENTS

## Efficiency runtime

Before Discovery, execute the routing and input-preparation procedure in
`docs/agent-efficiency.md` using explicit project and runtime roots. Its controller is
`tools/efficiency.py`; its policy authority is `phases/phase-table.json`. Save the route before
dispatch and consume it for both Discovery and Author. Unknown facts select strong; re-evaluate
after Discovery and scope growth, promoting but never silently downgrading an active run.

At each phase boundary, build and validate the bounded handoff, then execute `transition` with
the actual signal and artifact evidence. Only `disposition: advance` permits the returned
`next_phase`; other dispositions retain the phase and follow the existing blocker/recovery rules.
The controller does not waive max-rigor gates, peer authorization, or source requirements.
Run the Learning eligibility check before launching Phase 10. A verified skip emits
`LEARNING SKIPPED: no actionable work` and launches no learner. Unknown evidence dispatches.

Use the named independent read batches in that procedure; inspect every result before using it.
Record controlled input bytes, actual dispatches, retrieval follow-ups, and retries. Provider token
usage unavailable to this runtime is `unknown`; byte counts are never billed-token claims.

---

## External peer authorization boundary

`policies/peer-review.md` owns the harness lifecycle, the statuses (`not_requested`,
`standing_approved`, `approval_required`, `approved`, `denied`), and what makes scope trusted. The
autonomous delta is only this:

- Before Phase 1, run the compact `preflight` with the final request, passing the primary platform
  explicitly (`-Platform claude`, or `-Platform codex` under Codex) instead of hook-only env state.
- `standing_approved` is non-blocking: proceed once with `-Authorization auto`; with an exact prior
  approval use `-Authorization approved -ApprovalScopeSha256 <hash>`.
- `approval_required` — and an `accepted:false` hash/content mismatch — stops packetization, not the
  run: record `peer_unavailable`, say plainly that no content was sent, and do the primary Review
  once. Never ask a continuation question; that zero-exit JSON is a decision, not a tool failure.
- Paths outside `OBI_TRUSTED_ROOT (default: ~/source)`, a likely secret or credential, unusual multi-repository scope, a
  custom provider or destination, and any non-read-only purpose are never sent autonomously.

---

## Standing Autonomous Recovery Contract

Invoking `obi-auto` or `obi-auto-max` grants standing authority for every reversible, in-scope
orchestration choice needed to finish the original task. A retry, fallback, primary takeover,
run-state recovery, or verification rerun is not a new product decision and never triggers a
generic continuation question.

Every automatic recovery MUST first use the deployed helper to append one decision to the
run-scoped ledger `.obi/state/status-updates-<run_id>.jsonl`:

```powershell
python $env:OBI_HOME\tools\autonomous_recovery.py <event> `
    --run-id <run_id> --phase <phase> --provider <provider> `
    --evidence '<exact terminal or decision evidence>'
```

The supported events are `native_completion_stalled`, `peer_unavailable`,
`prior_run_safe_resumable`, `prior_run_terminal_or_corrupt`, `phase_blocker_fixable`,
`check_failure_fixable`, `reversible_default_selected`, `user_abort`, and `hard_stop`. Do not
invent an event to bypass the validated action, provenance, or terminal disposition encoded by the
helper.

For `native_completion_stalled`, omit `--evidence` and pass the authoritative native timeline with
`--native-state .obi/state/native-phase-<run_id>-<phase>.json`. The helper validates the full
same-thread-resume bound before it permits takeover. Read back its compact JSON, emit its
`progress_update` exactly once as information (never as a prompt), then execute `next_actions` in
order. The append records timestamp, run id, phase, provider, exact evidence, recovery action,
provenance (`delegated`, `inline-fallback`, or `primary-takeover`), remaining constraints and
uncertainty, disposition, and the ordered next actions. A malformed, truncated, mixed-run, or
non-contiguous ledger is a data-integrity hard stop; never rewrite prior lines to recover it.

| Observed event | Required decision |
|----------------|-------------------|
| Validated `native_completion_stalled` | `primary-takeover`; continue dispatch |
| Peer approval/provider unavailable | Send nothing; `local-review`; continue |
| Prior run is internally consistent and resumable | `resume-existing-run`; continue |
| Prior run is terminal or safely recognizable for archival | manifest archive, start fresh; continue |
| Ambiguous choice has a conservative reversible in-scope default | record assumption, select default; continue |
| Phase blocker or check failure is fixable inside task scope | primary repair/takeover, rerun the phase/check; continue |
| Explicit user abort or a non-bypassable safety/authority boundary | append terminal evidence and halt |

The only legitimate terminal boundaries are a destructive or irreversible action outside the
request, unavailable required credentials or authoritative source, an unauthorized external write,
a true `HARD STOP`, an explicit user abort, or evidence that continuing could corrupt or expose
data. Preserve the exact reason. Never use worker/orchestrator failure itself as a stop reason.

---

## Autonomous Execution Protocol

See [phases/README.md](../phases/README.md) for the canonical Phase Table (commands, agents, delegated flag, codified recipes, signals, skip conditions). `docs/workflow/phases.md` carries per-phase narrative detail.

> **Naming convention:** the Phase Table lists each phase's slash-command (e.g. `/discovery`) and its delegated agent (`obi-*`, e.g. `obi-discovery`). Dispatch targets the agent name — as the headless worker persona, or as the `Agent` `subagent_type` in the fallback path.

**rigor=standard:** Execute phases 1-10 in order. After each phase, check for its completion signal before advancing. On failure, loop back per [docs/workflow/phases.md](../docs/workflow/phases.md).

**rigor=max:** Execute Phase 0, then phases 1-10. Each phase is additionally gated by Gates 2-5 below.

---

## Special Signals

| Signal | Meaning | Action |
|--------|---------|--------|
| `NEEDS USER INPUT` | Claimed missing input | Classify the underlying reason; recover automatically when reversible/in-scope, otherwise append the named terminal boundary and halt |
| `NEEDS_CONTEXT` | Subagent lacks info | Provide settled context once; on a fixable repeat record `phase_blocker_fixable` and take over, using `MISSING SOURCE` only for genuinely absent evidence |
| `COMPLETE_WITH_CONCERNS` | Finished but flagged issues | Log concerns, include in handoff, proceed |
| `HARD STOP: [reason]` | Policy violation | Stop all work, append `hard_stop` terminal evidence via `autonomous_recovery.py`, report, and halt |
| `3-STRIKE LIMIT` | Same approach failed 3x | Stop that approach only: append `phase_blocker_fixable` or `check_failure_fixable`, choose a materially different bounded approach, and continue. Retry exhaustion alone is not a terminal boundary |
| `PHASE 0 COMPLETE` | *(rigor=max)* Phase 0 done | Advance to Discovery |
| `GREP GATE FAIL [phase N]` | *(rigor=max)* Forbidden pattern match | Loop back to phase N |
| `PIPELINE GREEN (iteration $i/$N)` | *(rigor=max)* CI green | Advance |
| `pipeline iter exhausted` | *(rigor=max)* CI retries spent | Record a fixable check recovery and choose a materially different bounded repair; terminal only for a named external-resource/safety boundary |

---

## Ralph Loop Integration

1. **Execute phase** — Run the current workflow phase
2. **Check signal** — Look for the completion promise
3. **On success** — Advance to next phase
4. **On failure** — Loop back as specified
5. **On special signal** — Act per the Special Signals table above

---

## Context Management

Between every phase transition, compact to prevent context dilution. Each agent starts with a clean slate — do not forward raw session history.

Before compacting, save and validate the structured handoff from `docs/agent-efficiency.md`.
Retain the handoff path, artifact hashes, current branch/run state, accepted criteria and unknowns.
Discard raw search output and iteration transcripts once their evidence is in reachable artifacts.
The next phase retrieves only needed source sections; Review retains access to the full diff.

---

## Lane Detection (lane-first)

Obi is **lane-first**: each lane declares the TOTAL ordered list of phases it runs. The
lane definitions are canonical in the `lanes` object of `phases/phase-table.json`. Classify the
lane ONCE, at a single well-defined point, then execute that lane's phase list — a phase that is
not in the list does not run.

| Lane | Phases | When |
|------|--------|------|
| `trivial`  | `[2, 9]` | <= 5 lines, comments/whitespace/typos only (no functional code) |
| `express`  | `[1, 2, 3, 4, 5, 7, 9, 10]` | < 25 lines of functional code |
| `standard` | `[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` | default — none of the above |
| `max`      | `[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]` + Gates 2-5 | `rigor: max` |

### Classification point

- **`rigor: max`** â†’ lane is `max`, assigned at invocation. No diff classification needed.
- **`rigor: standard`** â†’ classify AFTER Author (Phase 2), when the diff exists:
  1. Run `powershell -NoProfile -File $OBI_HOME/tools/classify-lane.ps1 -Base <run-base>`, where `<run-base>` is the
     commit the run started from (the parent of Author's first commit) so the measurement is
     CUMULATIVE, not just the last commit. It returns JSON: the size-based recommended `lane`, that
     lane's `phases` list (from phase-table.json), the `signal` (with `{N}` filled in),
     `lines_changed`, `appears_comment_only`, and `non_code_only` (true when only docs/config/binary
     changed). It throws on git failure rather than silently classifying as express.
  2. Apply the semantic EXCLUSIONS the script cannot see — bump the lane UP (never down) if the
     change is security-sensitive, touches public API, spans multiple modules, `non_code_only` is
     true for a large change, or the user asked for the full pipeline. Rules:
     `docs/policies/express-lane.md`, `docs/policies/trivial-change.md`.
  3. Emit the lane's `signal` (e.g. `EXPRESS LANE: 12 lines changed`; `standard`/`max` have none).
  4. Persist the lane to `.obi/state/lane-<run_id>.txt` (run-scoped) — one line, the
     lane name — so the choice survives compaction and resume. Rewrite it on any reclassification.
  5. Execute the REMAINING phases in the lane's list — those after the classification point. Phases
     already completed (Discovery, Author) are NOT re-run; the total list defines what the run
     comprises, not a re-execution order.

### Reclassification after Integrate (correctness gate)

The lane is **not immutable**. After Integrate (Phase 5), if Review or Integrate changed functional
code OR pushed the total diff to/over the express threshold (25 lines), RE-RUN `classify-lane.ps1`
against the SAME `<run-base>` (keeping the measurement cumulative) and adopt the higher lane,
rewriting `.obi/state/lane-<run_id>.txt`. The orchestrator knows what Integrate changed (it ran
it), so the "changed functional code" trigger is its own judgment, not just the line count. In
practice: an `express` run whose integration grows past 25 lines or adds functional code upgrades
to `standard`, re-inserting Phase 6 (Re-review) — the exact phase that catches
integration-introduced issues. Only ever reclassify UPWARD (`trivial â†’ express â†’ standard`); never
drop a phase already deemed necessary.

### Integrate no-op cascade

Phase 5 may emit `INTEGRATE NO-OP: <reason>` only under the strict guard in
`phases/05-integrate/command.md`. Before honoring the Phase-5 transition (`skip: [6]`), the
orchestrator must reread `.obi/integration-report.md`, require every declared review/triage count to
be zero, and independently recompute both HEAD and the worktree digest. Missing/malformed fields,
nonzero counts, git failures, or before/after mismatches fail closed: Phase 5 uses
`INTEGRATE COMPLETE` and Phase 6 remains active. Equality—not cleanliness—preserves pre-existing
user changes. This dynamic transition may remove Phase 6 from standard/max just as the existing
README transition may remove Phase 8; it never removes the unconditional Phase-9 full suite.

### README self-skip cascade

Phase 7 (README) may self-skip on content grounds (test-only, internal implementation, CI/CD, or
hook/tooling changes) and emit `README SKIPPED: [reason]`. This is phase-internal, not a lane
property. When it fires, honor the Phase-7 `transitions` entry in `phase-table.json`
(`on_signal: "README SKIPPED" â†’ skip [8]`, prefix-matched against the emitted
`README SKIPPED: [reason]`): skip Phase 8 (README Review) even on `standard`/`max` lanes where 8 is
in the list. On `express`/`trivial`, Phase 8 is already absent from the lane list.

### Review Verdict Parsing

- `REVIEW COMPLETE: PASS` â†’ Proceed to Integrate
- `REVIEW COMPLETE: FAIL [N] issues` â†’ Loop back, address issues

See `docs/policies/trivial-change.md` and `docs/policies/express-lane.md` for the classification
thresholds + exclusion rules.

---

## Artifact Output

Write artifacts to `.obi/` directory:
- `.obi/discovery-report.md` - Discovery findings
- `.obi/reviews/` - Review outputs by phase
- `.obi/integration-report.md` - Integration summary
- `.obi/state/phase-N-<name>.json` - Phase state files

---

## Completion

When Phase 10 (Learning) outputs `LEARNING CAPTURED` or the verified `LEARNING SKIPPED: no actionable work`, report the outcome, changed files,
verification, and any real operator action; expand when the user asks for detail.

---

## Subagent Delegation

See [`phases/README.md`](../phases/README.md) Phase Table for which phases dispatch a subagent
(`Delegated? = yes`) vs run inline. `phases/phase-table.json` is the machine-readable contract
(rendered by `tools/render-phase-table.ps1`, validated by `tools/config-guardian.ps1`).

Delegated phases are provider-specific. Claude Code runs through the **bounded supervisor**
(`tools/dispatch-worker.ps1`), FOREGROUND with a hard outer timeout. Codex
uses its native agent threads under the bounded status/wait/steer/interrupt protocol in
`platforms/codex/AGENTS.md`; it never runs the Claude process supervisor or treats checkpoint bytes
as native thread liveness. Both paths start with clean phase context and allow one same-session or
same-thread resume, never a replacement worker. Provider budgets come from each delegated phase's
`delegation_policy` in `phases/phase-table.json`; prose and prompt wording do not override them.

An `obi-auto` or `obi-auto-max` invocation is standing authorization for a bounded primary takeover
after a validated Codex `native_completion_stalled`, within the original task and file scope. Record
the native terminal evidence and honest primary provenance, then continue without asking the user
again. This does not authorize missing-source invention, destructive or external scope expansion,
credential handling, unverified worktree claims, or bypass of `HARD STOP` and other safety gates.

For each delegated phase (the bounded supervisor, or the Phase-4 `Agent` escalation), the
orchestrator MUST run the Phase-Output Validation contract below BEFORE issuing /compact or
advancing to the next phase.

---

## Autonomous Run Startup

**Fresh-run drift check (surface only, never auto-fix).** Before touching any state run
`git fetch --quiet` (skip silently on network failure) and
`git rev-list --left-right --count HEAD...origin/main` (or the resolved default branch). If
`main` is behind, or `tools/version.yaml` differs from the deployed `$OBI_HOME/tools/version.yaml`,
emit `DRIFT: <checkout behind origin by N | deployed version X vs source Y>` and append
`reversible_default_selected` naming the handling: sync a clean checkout to `origin/main` before
minting the RunId (pin the old tip as a branch), or continue on the current checkout when it
carries uncommitted work. A run that edits a stale checkout redeploys stale rules.

At the top of every `/obi-auto` run (both rigor levels), ensure the runtime directories exist. At
**fresh-run startup**, invoke
`$OBI_HOME/tools/archive-orphan-state.ps1 -Mode Archive` before minting a new RunId. Require exit 0
and structured status `complete` or `no_eligible_orphans`; surface every skipped classification.
The helper excludes **active and live** state and uses a **move-only manifest**. If it fails, leave
all state in place and halt rather than hand-selecting files. Then establish the run id:

```powershell
New-Item -ItemType Directory -Force -Path .obi/state, .obi/reviews, .obi/reports, .obi/runtime | Out-Null

# Run id lifecycle (never silently or blindly reuse a prior run)
$isNewRun = $false
if (Test-Path .obi/state/run-id.txt) {
    # Validate the prior RunId, dispatch/native timelines, completion markers, and live ownership.
    # If internally consistent and resumable, append prior_run_safe_resumable and resume that exact
    # run. If terminal or safely recognizable for archival, append
    # prior_run_terminal_or_corrupt, run the move-only helper below, require exit 0 plus status
    # complete, then mint a fresh RunId and set $isNewRun = $true. Never ask which recovery to use.
    # powershell -NoProfile -File $OBI_HOME/tools/archive-run-state.ps1 -RunId <prior-run-id>
    # Unknown ownership, an unverifiable live writer, an unsafe path, or a failed manifest is a
    # named data-integrity hard stop. Preserve every file; do not hand-select or clear state.
} else {
    # Fresh run.
    $runId = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
    Set-Content -Path .obi/state/run-id.txt -Value $runId -NoNewline -Encoding ascii
    $isNewRun = $true
}

# dispatch-state.json reset rule: reset if run_id mismatch
$runId = (Get-Content .obi/state/run-id.txt -Raw).Trim()
$taskBasePath = ".obi/state/task-base-$runId.txt"
function Stop-AutonomousStartup([string]$Evidence) {
    & python $env:OBI_HOME\tools\autonomous_recovery.py hard_stop `
        --run-id $runId --phase startup --provider orchestrator --evidence $Evidence
    if ($LASTEXITCODE -ne 0) {
        throw "HARD STOP: recovery ledger append failed while recording: $Evidence"
    }
    throw "HARD STOP: $Evidence"
}
if ($isNewRun) {
    if (Test-Path -LiteralPath $taskBasePath) {
        Stop-AutonomousStartup 'new RunId collided with existing task-base state'
    }
    $taskBase = (& git rev-parse --verify 'HEAD^{commit}' 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $taskBase -notmatch '^[0-9a-fA-F]{40,64}$') {
        Stop-AutonomousStartup 'unable to establish the task-base commit'
    }
    Set-Content -LiteralPath $taskBasePath -Value $taskBase -NoNewline -Encoding ascii
} else {
    if (-not (Test-Path -LiteralPath $taskBasePath -PathType Leaf)) {
        Stop-AutonomousStartup 'resumable prior run has no task-base record'
    }
    $taskBase = (Get-Content -LiteralPath $taskBasePath -Raw).Trim()
    if ($taskBase -notmatch '^[0-9a-fA-F]{40,64}$') {
        Stop-AutonomousStartup 'prior run task-base record is malformed'
    }
    & git cat-file -e "$taskBase^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Stop-AutonomousStartup 'prior run task-base commit is unavailable'
    }
}
$ds = if (Test-Path .obi/state/dispatch-state.json) {
    Get-Content .obi/state/dispatch-state.json -Raw | ConvertFrom-Json
} else { $null }
if ($null -eq $ds -or $ds.run_id -ne $runId) {
    @{ schema_version = 1; run_id = $runId; per_phase = @{}; per_run = 0 } |
        ConvertTo-Json | Set-Content -Path .obi/state/dispatch-state.json -Encoding utf8
}
```

On successful Learning phase completion (including verified Learning skip), first require every run lifecycle tracker to be closed.
`archive-run-state.ps1` returns nonzero status `blocked_open_trackers` when any run-scoped report
contains an exact case-insensitive `Status: OPEN` line; **never rewrite the tracker** merely to pass
this guard. Then invoke `archive-orphan-state.ps1 -Mode Archive` once more to collect any old
families that became eligible during the run. Accept only `complete` or `no_eligible_orphans`. Next
read the active RunId from `run-id.txt`, invoke
`$OBI_HOME/tools/archive-run-state.ps1 -RunId <active RunId>`, and require both process exit 0 and
a structured result whose status is complete. The helper moves the generic control files,
`phase-*-complete.marker` files, task/resume checkpoints, and recognized run-scoped artifacts into
the reversible `.obi/archive/state-<RunId>/` tree. Never delete `run-id.txt` first: doing so leaves
unscoped completion markers that a fresh watchdog can mistake for current work. If archival fails,
leave the remaining state in place and report the manifest-backed recovery location so the next
run offers explicit recovery semantics.

---

## Phase-Output Validation

After every `Agent` call, before treating the result as the phase output or running /compact,
validate it against the phase's accepted-signals contract from `phases/phase-table.json`.

### Step 0 — Strategy gate (preemptive inline)

Before issuing any `Agent` call, consult `default_strategy` for the current phase in
`phases/phase-table.json`:

- `"inline"` — run the codified recipe DIRECTLY in this orchestrator context. Do NOT dispatch to
  `Agent`. Skip Steps 1-3 entirely. Recipe letter: Simplify=S, Review=RV, Re-review=R, README
  Review=M. When the recipe emits its completion signal, advance to /compact.
- `"dispatch"` — first route by the known primary platform. Under Codex, follow
  `platforms/codex/AGENTS.md` "Delegated Phases Under Codex": one native thread, bounded waits, one
  metadata-timed synthesis steer, one explicit interrupt/resume of that same thread, authoritative
  `.obi/state/native-phase-<run>-<phase>.json` timestamps, and no replacement. An oversized native
  phase is decomposed BEFORE dispatch into at most three chunk threads, each with its own timeline
  record and the same one-thread bound (`platforms/codex/AGENTS.md` "Oversized Native Phase");
  decomposition is never a replacement for an interrupted thread. A Codex
  `native_completion_stalled` record is neither `timed_out` nor Claude capacity evidence; in this
  autonomous contract it triggers the scoped primary takeover above. Under
  Claude, use the **bounded supervisor** (`tools/dispatch-worker.ps1`), FOREGROUND
  with a hard outer timeout. The numbered mechanics below are the Claude path; Codex consumes the
  same phase inputs and accepted-signal contract but not the Claude CLI/status-file mechanism.
  1. Compose the phase inputs (per the agent's "You receive" contract) into a prompt file, e.g.
     `.obi/state/task-<N>.md`, and resolve the phase's required deliverable path. Three things the
     prompt MUST carry, because the worker can be killed
     at the deadline mid-thought:
     - **Write completed facts to the artifact early.** A non-empty checkpoint and later
       bytes/mtime movement are advisory health signals, not running-worker kill conditions.
       Semantic structure is validated after completion. Do not create a speculative outline or
       restate settled design merely to move the checkpoint. For Author, source/test work comes
       before report prose; each report update names only actual edits and test results.
     - **A DO-NOT-READ list and settled facts.** Name generated/irrelevant trees to skip (e.g.
       `graphify-out/`, `hooks/tests/`) and any facts already verified this run, marked "do not
       re-verify". Budget spent re-deriving known facts is budget not spent on the deadline.
     - **The exact checkpoint path.** Default canonical paths are `.obi/discovery-report.md` for
       Discovery, `.obi/reports/author-report.md` for Author, and a run-scoped report under
       `.obi/reports/` for Learning. Create its parent before launch.
  2. Resolve the phase policy first, then run the supervisor FOREGROUND. Pass the returned
     `outer_timeout_ms` (configured `absolute_sec + cleanup_margin_sec`) as an EXPLICIT `timeout` on
     the outer tool call so scoped kill, verification, and terminal persistence retain a bounded
     cleanup margin. It is never optional: without it the harness caps the call at its own default
     and can end it before the supervisor writes a terminal status, leaving a stale `running` record
     and an orphan child. The PreToolUse duration guard blocks a supervisor call that omits it or
     that exceeds the 600 000 ms host ceiling; every phase budget is held under that ceiling by
     `Test-DispatchDocs` (`absolute_sec + cleanup_margin_sec <= 570`). A phase that needs more time
     decomposes (Step 0.5) — never raise the budget past the ceiling. Normal orchestration omits
     `-TimeoutSec`; that switch is a diagnostic override bounded by the phase's absolute cap:
     ```
     POLICY = powershell -NoProfile -File $OBI_HOME/tools/get-dispatch-phase-policy.ps1 -Phase <N>
     Bash(command: "powershell -NoProfile -File $OBI_HOME/tools/dispatch-worker.ps1 -Persona <agent> -PromptFile .obi/state/task-<N>.md -RunId <run_id> -Phase <N> -CheckpointPath <phase-artifact> -ExpectSignal '<primary_signal>'", timeout: POLICY.outer_timeout_ms)
     ```
     `$OBI_HOME` is the deployed, endpoint security-trusted tools path (`powershell`, not `pwsh` — the
     latter is not installed). The supervisor runs `claude -p` with streaming JSON, an ASSIGNED
     `--session-id`, the persona as system prompt, its declared `--tools` (restricted), and
     `OBI_WORKER=1` so its state-mutating hooks no-op while PreToolUse policy remains active. It
    watches output-growth + process-tree CPU separately from checkpoint bytes/mtime and, on true
    idle or the hard deadline, scoped-tree-kills ONLY its own PID and records a terminal status.
    Missing/stalled checkpoint health is advisory while the child is running. A child that exits
    without a non-empty required artifact is a resumable `error`, not `killed`.
    `last_progress_at` is real stream/CPU progress; `finished_at` is terminal wall-clock.
    Concurrency is
     capped at ONE live worker per run — never launch a second dispatch in parallel.
  3. **Read the authoritative current STATUS first** —
     `.obi/state/dispatch-status-<run_id>-<N>.json`; the current summary is at
     `.obi/state/worker-summary-<run_id>-<N>.json`. Each current record names immutable
     `attempt_status_file` / `attempt_summary_file` snapshots with `-a1` or `-a2`; retain attempt 1
     before resuming and use both snapshots for any two-attempt predicate. Branch on `status.status`:
     - `completed` â†’ consume the artifact (`out_file` + the phase artifact the worker wrote, e.g.
       `.obi/discovery-report.md`), record completion, proceed to Step 1 on the result. Do NOT
       re-execute.
     - `timed_out` / `killed` / `error`, AND `status.resumable` is true, AND `status.session_id` is
       set, AND `status.kill_verified` is not false, AND this is attempt 1 â†’ **resume the SAME
       session EXACTLY once** (never replay the original prompt). FIRST write the short
       continue-instruction to `.obi/state/resume-<N>.md`. For
       `failure_reason=checkpoint_missing` (or a historical checkpoint kill), its first instruction
       is "synthesize current evidence into the checkpoint before further reading". For an interrupted Author, first compare report claims
       with `git diff --name-only` and record any mismatch. THEN:
       ```
       POLICY = powershell -NoProfile -File $OBI_HOME/tools/get-dispatch-phase-policy.ps1 -Phase <N>
       Bash(command: "powershell -NoProfile -File $OBI_HOME/tools/dispatch-worker.ps1 -Persona <agent> -PromptFile .obi/state/resume-<N>.md -RunId <run_id> -Phase <N> -CheckpointPath <phase-artifact> -ExpectSignal '<primary_signal>' -ResumeSessionId <status.session_id> -Attempt 2", timeout: POLICY.outer_timeout_ms)
       ```
       Re-read the status after the resume.
     - `launch_error` (bad persona/prompt path, or the launch itself failed) â†’ NOT resumable; fix
       a proven prompt/persona/path defect and re-dispatch attempt 1 once. If it remains fixable in
       scope, record `phase_blocker_fixable` and take over; a missing required credential/source is
       a named terminal boundary, not a request for permission.
     - `refused_concurrency` â†’ another worker is live for this run; wait ONCE for 60 s, then re-read the status
       file. If the recorded owner PID is dead, use the supervisor's verified cleanup path and
       re-dispatch attempt 1; if it is alive, wait one more 60 s window and re-check. Still live or
       unverifiable after that: append the data-integrity `hard_stop`. Never run two writers and never
       wait open-endedly.
     - **status file absent / `Read-DispatchStatus` null** (the supervisor died before writing any
       status — should not happen now that pre-launch failures record `launch_error`, but guard it):
       wait once for 60 s and re-read (a slow publish is not absence); if still absent, prove no
       child/owner remains live (launch-record PID, `Get-Process`), then use Step 0.5 (inline fallback if eligible,
       otherwise bounded primary takeover). If safe ownership cannot be proven, append the exact
       data-integrity hard stop and preserve all state.
  4. On a `completed` status (first attempt or after the single resume), record completion:
     ```powershell
     Set-Content -Path ".obi/state/phase-<N>-complete.marker" -Value "done" -Encoding ascii
     ```
     Because the worker's state-mutating hooks no-op (`OBI_WORKER=1`), the ORCHESTRATOR appends the resume
     completion record so resume can skip this phase:
     `dispatch-state.json.completions[] += { ts, phase: <N>, signal: '<primary_signal>', source: 'headless' }`.
  5. **Still not completed** after the single resume (or not safely resumable — no session id /
     `resumable` false): use the **inline fallback** ONLY for phases with
     `inline_fallback_eligible: true` (Step 3 — Recipe S/R/M/G). For any phase whose table entry has
     `inline_fallback_eligible: false`, apply the **capacity test** below FIRST. If it does not
     apply and verified termination makes takeover safe, append `phase_blocker_fixable` with the
     EXACT state (`status`, `kill_reason`, `kill_verified`, `last_progress_at`, `session_id`) and
     perform the bounded primary takeover. Never fall back to a background `Agent`: it has no
     timer and writes no status record. An unverified live child is a named data-integrity hard
     stop.

     **Verified checkpoint-recovery exception (synthesis phases only).** A missing or stalled
     checkpoint can leave useful, already-settled evidence stranded even though the worker never
     produced an acceptable phase result. This is not general inline fallback. Apply it only after
     **two verified checkpoint failures** (the initial attempt plus the one allowed resume) when
     both attempts used the **same assigned session**, each terminal record has `kill_verified`
     true and `progress_count` greater than zero, and each `kill_reason` is
     `checkpoint_missing` or `artifact_stalled`:

     1. Never dispatch a third worker. Standing autonomous authority begins the primary takeover
        automatically after appending both terminal records and the bounded synthesis decision.
     2. Discovery may use `primary-synthesis-recovery`: synthesize the
        canonical discovery artifact from **settled evidence only**, perform **no new source
        research**, and record that provenance rather than implying a worker completed it.
     3. Author may use `primary-author-takeover`: continue the actual
        implementation in the primary context with **honest attribution**. The primary first
        compares every checkpoint claim to the worktree, then **reconciles source and tests**. The completed Author
        report must state which edits and validations belong to the primary takeover.
     4. Record the standing-authority recovery source in the append-only status ledger,
        `dispatch-state.json`, and the phase artifact.
        This exception does not change `inline_fallback_eligible` and cannot be reused by another
        phase or after any unverified kill, zero-progress attempt, different session, or third
        dispatch.

     **Capacity test — "too big" is not "confused".** Inline-ineligibility assumes a dispatch
     failure means an unreliable subagent. A task that reaches its configured absolute phase cap
     while still productive is a different failure: halting on it delivers nothing, and a third
     dispatch burns another full phase budget for the same outcome. Declare a **capacity mismatch** only when BOTH
     attempts show all of:
     - `status` is `timed_out` (NOT `error`, `killed`, or `launch_error`), AND
     - `kill_verified` is true, AND
     - `progress_count` is greater than zero, AND
     - `last_progress_at` is no earlier than `deadline_at - (poll_sec + 1 second)`. The supervisor
       stops observing at the deadline, so requiring an at/after timestamp would be unreachable;
       the persisted poll cadence defines the fail-closed final observation window. Apply
       `Test-CapacityMismatchEvidence` from `tools/lib/dispatch-worker-lib.ps1` to each attempt.

     On a confirmed capacity mismatch, do NOT halt and do NOT dispatch a third time:
     1. Emit `CAPACITY MISMATCH [phase N]: <what was measured>`. Grep-stable contract:
        `^CAPACITY MISMATCH \[phase \d+\]:`.
     2. **Prefer deterministic decomposition** — define at most three serialized chunk keys
        `<phase>c<k>` (`k=1..3`). Each uses `-Phase <phase> -DispatchKey <phase>c<k>`, task/resume
        filenames containing that key, and checkpoint
        `.obi/reports/<run_id>-<phase-slug>-c<k>.md`. A fourth chunk is invalid: never dispatch it;
        record primary takeover and reconcile the existing chunk evidence instead. Every mapped
        chunk must complete; no chunk artifact may retain `PENDING`, `IN_PROGRESS`, or `TBD`.
     3. The primary orchestrator synthesizes the canonical phase artifact inline from completed
        chunk reports only, records their paths as provenance, performs no new source research, and
        never dispatches a fourth synthesis worker.
     4. Only if the phase genuinely cannot be decomposed, run it **inline in this orchestrator
        context** and state in-channel that it ran inline and why. Never present inline output as
        though a worker produced it.

     Anything failing the capacity test — an `error`, a `launch_error`, or a worker that stopped
     progressing well before its deadline — is NOT a capacity mismatch. Preserve that exact
     classification, then use the safe primary-takeover route above; halt only when termination,
     ownership, source, credentials, or scope cannot be proven safe.

Escalation (input too large for inline): only **Phase 4 Review** may escalate to `Agent` dispatch
when the diff exceeds **150 changed files** OR **50 000 lines** of `git diff`. Phases 3, 6, 8
are **never** escalated. Record any Phase-4 escalation in `.obi/state/dispatch-state.json`
under `escalations[phase_n] = {reason, measured_<files_or_lines>}`, then proceed to Step 1.

**Observability requirement.** Before executing the inline recipe OR issuing the `Agent` call, emit:
- `STRATEGY: inline (recipe <letter>)` for default-inline phases
- `STRATEGY: dispatch (<reason>)` for default-dispatch phases or Phase-4 escalation

### Step 1 — Detect abnormal results

If a result is empty, interrupted, contains a harness sentinel, requests context, reports a
blocker/concerns/strike limit, or lacks its expected completion signal, load
`docs/workflow/dispatch-failure.md` before taking action. It owns the ordered sentinel detection,
classification, retry and inline fallback procedure, including the mandatory DROP transcript.
This is a lazy-loaded continuation of this contract; every instruction remains mandatory when
triggered. Normal successful phase boundaries use the efficiency controller.

---

## When rigor=max: Phase 0 and Gates 2-5

These do not run on a `rigor: standard` invocation, so they are NOT inlined here.

**If and only if `rigor: max`:** read `docs/policies/rigor-max-gates.md` and follow it.
It carries Phase 0 (prereq lock-in, plan-file resolution + write-back, probes,
peer-on-plan), Gate 2 (hard grep gates), Gate 3 (probe library), Gate 4
(iterate-until-green), Gate 5 (auto-memory-write), and the config loader.

Plan-file schema: `docs/policies/obi-auto-max-schema.md`. The plan lives at
`.obi/reports/<run_id>-plan.md` (or an explicit `--plan <abs-path>`); when neither exists,
synthesize the minimal plan there and continue. Never write under `~/.claude/plans/`.

Do this BEFORE emitting the Phase 0 header — the gates change what each later phase
must do, not just Phase 0.

---

## Remember

- Autonomy is limited to the requested acceptance criteria and phase contracts; unrelated
  improvements remain out of scope
- *(rigor=max)* Phase 0 plan write-back is the source of truth for surprise detection — never re-derive from `default:` or live conversation
- *(rigor=max)* Peer-on-plan through the supervised durable harness per [`policies/peer-review.md`](../policies/peer-review.md)
- *(rigor=max)* `tools/config-guardian.ps1` is the consolidated structural validator
- *(rigor=max)* auto-lock reversible, conservative declared defaults; ask only for a material
  product/authority input that cannot be derived from the authorized task
- `graphify-out/` is generated and gitignored: never dump it into a tool result — prefer
  `graphify query/path/explain` or a targeted search with a small excerpt of at most 100 lines
- A shell call that fails before child execution with Windows error 1312 is a host pre-execution
  failure: change the invocation path once (explicit Windows PowerShell path) and retry — never a
  phase or provider strike

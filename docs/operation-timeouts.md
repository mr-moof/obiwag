# Operation Timeouts

Before any operation expected to take >10 seconds: **announce** what you're about to do, **estimate** duration, then **execute**.

## Timeout Table

The default hard ceiling is **300 000 ms (5 min)** for a foreground `Bash` call. The one scoped
exception is a recognized Pester-only command, capped at **600 000 ms (10 min)**. Larger timeouts
are rejected for ordinary commands, and detached long-running commands are denied (issue #200).
The bounded dispatch supervisor is the scoped exception: its phase-derived internal absolute cap
and cleanup margin own termination and durable status. Anything else that can exceed its
command-class ceiling gets decomposed or moved to a bounded supervisor. The rows below are budgets
to plan against, not arbitrary headroom.

| Operation Type | Budget / cap | If it can exceed its cap |
|----------------|--------------|--------------------------|
| ExitPlanMode | 2 minutes | n/a |
| Skill invocations | 5 minutes | n/a |
| Non-Pester test runs | plan for 3 minutes; hard cap 5 minutes | bounded supervisor or deterministic decomposition |
| Pester-only test runs | hard cap 10 minutes | deterministic Pester sharding or bounded supervisor |
| obiwag-agents release suite | 3 Pester shards at most 10 minutes each + 1 Python component at most 5 minutes | already decomposed by Recipe G |
| Build operations | 5 minutes (was documented as 10 — not achievable in a foreground call) | `dispatch-worker.ps1` |
| obi-auto Claude phase dispatch | phase metadata: Discovery 540 s, Author 540 s, Learning 420 s absolute cap, each plus 30 s outer cleanup (never above the 570 s ceiling) | already bounded; one same-session resume, never a replacement |
| obi-auto Codex native phase | metadata-timed synthesis due + grace + one same-thread resumed bound | authoritative native timeline; never launch a replacement thread |
| Peer review foreground `run` | configurable, at most 240 s hard deadline | use durable `peer-review.ps1 start` |
| Peer review durable `start` | up to 3600 s, with a 2 s broker heartbeat | already detached and bounded by the broker |

If a budget is reached, preserve its exact terminal classification and follow the provider-specific
recovery contract. Autonomous Codex runs may use their scoped standing primary takeover after a
validated native stall; other safety/input boundaries still surface to the user. Do not silently exceed
the applicable cap.

## Release-suite decomposition

The obiwag-agents no-argument `tools/run-tests.ps1` suite is mixed Pester + Python, so it retains the
five-minute default foreground ceiling. It normally takes six to eight minutes and is therefore
invalid to launch as a foreground monolith even though a Pester-only component may use ten minutes.

The PreToolUse guard recognizes `Invoke-Pester`, or `tools/run-tests.ps1` with `-PowerShellOnly` or
only explicit `*.tests.ps1` paths, as Pester-only. `-PythonOnly`, a no-argument runner, an ambiguous
path, any explicit `*.py` path, or a chained/pipelined command keeps the five-minute default. This
prevents the exception from silently expanding to the mixed full suite or unrelated commands.

Recipe G in `orchestration/inline-fallback-recipes.md` is the canonical release procedure. It first
uses `-ListTargets` to prove that three deterministic `-PowerShellOnly -ShardCount 3 -ShardIndex
<N>` selections are complete and disjoint, then executes each Pester shard under its own 600 000 ms
bound and runs `-PythonOnly` once under a 300 000 ms bound. The four results collectively constitute
one fresh full suite; running the monolith as well is duplicate work.

A host timeout is not proof that the owned Windows descendants exited. Capture the component PID,
command line, and start time, terminate only that exact timed-out process tree, and verify it is
gone before failing the gate. Never retry the timed-out component blindly.

## When the user reports a stall

> If the user says the session is stuck, hung, or stalled, he is right by definition — from the
> operator's seat, silence is stuck regardless of internal classification. NEVER respond with
> terminology corrections ("that was a dropped result, not a hang"). Respond with exactly three
> things, then act: (1) what was dispatched, (2) what state you verified, (3) the next action being
> taken now. The sentinel taxonomy below exists to drive YOUR recovery behavior, not to adjudicate
> the user's word choice.

## Tool-result drops — sentinel-recovery contract

A `[Tool result missing due to internal error]`, empty, or whitespace-only tool
result still leaves you in control, so react immediately — treat it internally as a
returned result requiring immediate reaction (do not idle); this is a recovery rule, not
language to use with the user. Never idle, never re-issue blindly, never stop to ask first.

**Observability (emit, don't recover silently).** BEFORE the verify read in step 1, emit one line
to the terminal: `DROP DETECTED: <tool/phase> — verifying with <check>, then <retry once | proceed
| halt>`. After the check resolves, emit one outcome line: `DROP RESOLVED: <already-applied |
retried-ok | escalating>`. From the terminal, silent verification is indistinguishable from a
freeze, so these lines are the visible evidence recovery is running. If you reach the step-1 read
after a sentinel without having emitted the DETECTED line, you skipped the contract — emit it now.
Stable contract: grep-stable on `^DROP (DETECTED|RESOLVED):`.

```text
DROP DETECTED: Agent/Review — verifying with .obi/state/dispatch-state.json, then retry once
VERIFY: no completed Review artifact; retry budget available
RETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS
DROP RESOLVED: retried-ok
```

1. **Verify, don't assume.** A drop does NOT mean the action failed — some land,
   some don't. Confirm actual state with one cheap read matched to the action:
   `Grep`/`Read` for an edit, `git status`/`git log -1` for a commit,
   `SELECT count(*)` for a DB write, `Test-Path` for a file.
2. **Branch on what you found.** Did not happen → retry **once**, now bounded
   (an explicit foreground `timeout` or the appropriate durable supervisor). Already
   happened → proceed; do not retry (re-running an edit/commit can double-apply).
3. **Keep moving.** Escalate to the user only if state is genuinely unknowable after
   the check — and even then work independent steps meanwhile rather than idling.

The **true silent-hang** (no sentinel ever returns) cannot be detected from
inside a blocking call, so it is **prevention-only**: the PreToolUse duration
guard (`hooks/pre_tool_use.py::check_duration_guard`) forces likely-long
commands through the bounded supervisor or a bounded foreground timeout — a raw
long-running *background* command is now DENIED (issue #200), because detached
it is wall-clock-killed with no durable terminal state. So a drop costs seconds,
not an idle hour.

**This contract is now ENFORCED, not just prose (issue #200 §3).** `hooks/core/drop_guard.py`
makes it executable: `post_tool_use.py` records only the exact missing-result sentinel from
`tool_response` or `tool_error` as a pending,
unresolved drop; the `Stop` hook (`drop_guard.evaluate_stop_block`) inspects the transcript and, if
the newest tool-result batch containing a drop has no LATER successful recovery batch, returns
`{"decision":"block","reason":"Unresolved tool-result drop: verify state and recover now."}` —
bounded by a retry counter + `stop_hook_active` so a "that wasn't a hang" reassurance can never be
the terminal response while no recovery happens; at the cap it fails loud with the 3-line
operator-stall report. Detection is sentinel-*dominant* (the whole result IS the sentinel), so
reading a doc that merely contains the sentinel string is not a false drop.

## Peer review harness

Both provider CLIs can go idle mid-run, so review never invokes either provider directly.
Foreground `run` owns a provider tree for at most 240 seconds. Foreground `start` returns a
durable receipt and launches a detached broker worker whose hard deadline is at most 3600 seconds.
The worker streams bounded diagnostics, emits a status heartbeat every two seconds even before
provider output, and distinguishes output activity from aggregate process-tree CPU activity. The
first-review-output deadline is 120 seconds; stderr-only startup noise remains diagnostic activity
and cannot satisfy it. Once review output begins, five minutes without output or CPU is a real idle
kill rather than a punishment for a long useful review.

On Windows the worker assigns provider descendants to a kill-on-close Job Object. Its receipt also
records the worker PID plus creation time, so cancellation/recovery never targets a reused PID.
Owner loss, a stale worker heartbeat, timeout, idle detection, cancellation, and output overflow
all terminate the owned tree where identity can be verified and persist an explicit terminal
status. A forced fallback uses `taskkill /T` on Windows; if tree termination cannot be proved, the
terminal record says `kill_verified=false` and healthcheck fails instead of claiming success.

Every accepted start creates an obligation. Callers retain its RunId and use only foreground,
bounded `status`, `wait`, `result`, or `cancel` operations. They never use shell background
mode, tail diagnostic files, busy-poll, or retry a terminal result. Stop and health checks surface
active or terminal-but-unconsumed runs. Contract: `policies/peer-review.md`.

## Dispatch supervisor (issues #200 and #205)

`tools/dispatch-worker.ps1` is a **bounded supervisor**. It resolves normal phase budgets from
`phases/phase-table.json`; `-TimeoutSec` is now only an explicit diagnostic override and cannot
exceed the selected phase's absolute cap. `tools/get-dispatch-phase-policy.ps1` returns the resolved
policy and `outer_timeout_ms = absolute_sec + cleanup_margin_sec`, so orchestration no longer pairs
a larger prompt request with a hidden 270-second worker/300-second outer ceiling.

The supervisor runs `claude -p` with streaming JSON and an ASSIGNED `--session-id`, streams to
files (observable growth), watches output-growth + process-tree CPU, and on idle (90 seconds by
current phase policy) or its effective deadline scoped-tree-kills ONLY its own PID, verifies
termination, and persists a **terminal status** to
`.obi/state/dispatch-status-<run>-<phase>.json`. Checkpoint
existence and bytes/mtime are separate advisory health: productive stream/CPU/artifact activity is
never killed as `checkpoint_missing` or `artifact_stalled`. A child that exits without a non-empty
required artifact is a resumable `error` with `failure_reason=checkpoint_missing`; only true idle
is `killed/hung`, and only the hard deadline is `timed_out`.

At the nominal cutoff the supervisor grants at most one productive extension only when recent
stream/CPU progress, repository-contained non-empty checkpoint growth, and non-idle state are all
present. A missing or unchanged checkpoint denies work extension but retains the bounded
finalization reserve. The launch instruction tells the non-interactive worker when to stop tools,
reconcile source/tests/checkpoint, and emit the exact signal. Status schema 3 records the initial
duration, extension decision/reason/duration, progress-at-cutoff, finalization time, final duration,
absolute cap, and derived outer margin. Absolute exhaustion remains `timed_out`.

Everything after launch runs inside a fault boundary. A terminating error anywhere in the watchdog
loop or the classification block is published as a terminal `error` with
`failure_reason=supervisor_fault` (exit 5), the child tree is killed if it survived, and the
concurrency mutex is always released. There is no path on which the supervisor exits leaving the
status at `running`.

### Calibration evidence (2026-08-17 through 2026-08-19)

The policy is based on archived real phase records, not an unexplained multiplier:

| Phase | Sample | Observed first-attempt behavior | Configured nominal + extension + finalization |
|-------|--------|---------------------------------|-----------------------------------------------|
| Discovery | 6 records | all productive at ~271 s; 24-25 progress observations | 390 + 90 + 60 = 540 s |
| Author | 4 records | all productive at ~271 s; 25 progress observations | 420 + 60 + 60 = 540 s |
| Learning | 2 records | one completed at 142 s; one productive at 271 s and completed after a 47 s resume | 270 + 90 + 60 = 420 s |

The calibration evidence alone would justify a larger Discovery/Author budget, but it is capped by
the **host outer-call ceiling**: one foreground tool call ends at 600 s, and the outer call is
`absolute_sec + cleanup_margin_sec`. The earlier 630 s / 810 s budgets meant the harness — not the
supervisor — ended the call, so nothing persisted a terminal status: the run was left with a stale
`running` record, a live orphan `claude` tree, and no summary. Every delegated phase must therefore
satisfy `absolute_sec + cleanup_margin_sec <= 570`, which `Test-DispatchDocs`
(`tools/lib/checks/check-dispatch-docs.ps1`) enforces mechanically, and the PreToolUse duration
guard requires the supervisor's outer call to state that bound explicitly. A phase that genuinely
needs more time decomposes (Step 0.5) instead of raising the budget.

The samples are preserved in archived state for runs `20260817T133423Z`, `20260817T151143Z`,
`20260817T162938Z`, `20260819T124315Z`, `20260819T142312Z`, and `20260819T165202Z`.

Each Claude attempt writes immutable `-a1` / `-a2` stream, status, summary, and system files while
atomically refreshing the unscoped status/summary as compatibility current views. The orchestrator
runs it FOREGROUND (never a background `Agent`) and recovers state-driven: read the status;
`completed` → consume the artifact; `timed_out`/`killed`/`error` + `resumable` → `--resume` the same
session EXACTLY once; otherwise use the codified inline fallback or append a bounded primary
takeover decision and continue. Only a named safety, authority, source, ownership, or data-integrity
boundary fails loud. Never use a blind prompt replay. Contract: `orchestration/obi-auto.md` Step 0.

Codex does not invoke this Claude supervisor. A delegated Codex phase uses one native thread and
`tools/native_phase_state.py` to atomically record start, synthesis due/sent, observable progress,
interrupt, resume, and terminal timestamps. The phase policy prohibits an early steer and all tool
calls after its exact synthesis instruction. Recent progress yields `productive_budget_exhausted`,
not stalled. Only a resumed turn with no progress for its full bound becomes
`native_completion_stalled`; it is never `timed_out`. One same-thread resume and no replacement
remain mandatory. Autonomous invocation supplies standing scoped primary-takeover authority after
that validated stall. The orchestrator records every recovery through `autonomous_recovery.py` in
`.obi/state/status-updates-<run_id>.jsonl` before acting. Contract: `platforms/codex/AGENTS.md`.

## Git transport errors

The post-tool hook flags temporary RPC/server transport errors as advisory. Verify remote state before a bounded retry. Authentication and non-fast-forward failures require diagnosis; no hook redirects pushes or automatically retries credentials.

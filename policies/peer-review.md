# Peer Review Harness Policy

Cross-model review is advisory input, not an arbitrary shell session and not an
independent gate. Every handoff goes through the repo-owned
`tools/peer-review.ps1` command. Provider-specific CLI flags are private to its
adapters.

## Invariants

- Run `preflight` before the first `run` or `start`. It has a 30-second default/120-second maximum,
  inspects local metadata/content under bounded reads, creates no capsule, and invokes no provider.
  An exhausted/incomplete inspection returns `approval_required`, never standing authorization.
  Report the canonical repository root, exact
  `include_paths` (or "all tracked and unignored text"), and resolved destination from its compact
  result. This scope report is informational, not a blocking question, when status is
  `standing_approved`. Presentation is bounded to 32 visible include paths and 16 reason details
  of 512 characters each; omitted counts preserve honest scope without flooding model context.
- Ordinary read-only peer review is standing-authorized only when the canonical repository and every
  declared evidence/attachment path resolve beneath `OBI_TRUSTED_ROOT (default: ~/source)`; `-Provider auto` resolves to
  the standard opposite provider; the estimated request stays within the harness's 5,000-file,
  32-MiB capsule limits and normal external-file bound; and the classifier finds no likely secret.
  The adapters still enforce read-only isolation. Proceed with `-Authorization auto` without asking
  the user again.
- Status `approval_required` stops before packetization/provider launch. Ask only for a meaningful
  risk expansion: a path outside `OBI_TRUSTED_ROOT (default: ~/source)`; a likely credential, private key, or token; bulk or
  multi-repository scope beyond a normal review; an explicit/custom provider or destination; or a
  purpose other than the harness's read-only peer review. Name the repository, exact path scope,
  destination, and reasons. Preflight emits `approval_scope_sha256`, binding the normalized
  request, repository, resolved provider, reason codes, and current payload content. The packet
  builder rechecks that exact payload hash immediately before provider launch, including in a
  detached `start`, so intervening content drift stops the run. After explicit
  approval, rerun the same scope once with `-Authorization approved -ApprovalScopeSha256 <hash>`.
  A missing/mismatched hash remains `accepted:false` and triggers no provider. Denial/absence means
  no content was sent and `[Peer: unavailable]`, not provider failure.
- Track the boundary as `not_requested`, `standing_approved`, `approval_required`, `approved`, or
  `denied`. Never infer `approved` merely from a prior unrelated review.

- Invoke every public harness operation in the foreground. `run` owns one
  provider tree for at most 240 seconds. `start` is a short foreground receipt
  operation that detaches a broker-owned worker for reviews up to 3600 seconds.
- Never use the shell tool's background mode, tail raw files, invoke a provider
  directly, or retry a terminal run. For a durable run, use only
  `status`, bounded `wait`, `result`, and `cancel`.
- Every accepted `start` creates an orchestrator obligation: retain its `run_id`
  and consume a terminal `result` or explicitly `cancel` it before finalizing the
  parent task. A Stop hook and healthcheck reinforce this obligation.
- `unavailable`, `timed_out`, `idle_killed`, `output_limit`, malformed output,
  and `inconclusive` are terminal for that attempt. Label the synthesis
  `[Peer: unavailable]` and continue once.
- A peer verdict is never authoritative. The primary reviewer must reproduce
  each accepted finding against the original working tree. Only that primary
  disposition can fail the phase.
- Read only `status.json`, `result.json`, and `summary.md`. `events.jsonl`,
  `stderr.log`, and `result.raw.json` are bounded diagnostic artifacts for
  humans and health checks, not model context.
- Never append provider arguments. The public command accepts semantic inputs
  only: operation, provider, primary platform, authorization mode, repo, request, run ID, and bounded deadlines.
  For public `run`/`start`, pass the already-known primary explicitly as `-Platform codex` or
  `-Platform claude`; `OBI_PLATFORM` remains only a compatibility fallback for older callers.

## Request

Create a request JSON (normally `.obi/review/request.json`):

```json
{
  "schema_version": 1,
  "objective": "Adversarially review the current implementation against its specification.",
  "acceptance_criteria": [
    "Findings identify behavior that can be reproduced from local evidence.",
    "Tests and failure paths are assessed."
  ],
  "focus": ["correctness", "reliability", "security"],
  "include_paths": [],
  "evidence": [],
  "attachments": []
}
```

`include_paths` is an optional exact-prefix/glob scope. Empty means all current
tracked and unignored untracked text files. The packet builder excludes `.git`,
`.obi/review`, `.obi/reviews`, `graphify-out`, links/reparse points, binaries,
non-UTF-8 files, and oversized files. It rejects traversal and total-size
overflow; it never silently truncates. Request JSON is capped at 256 KiB before
parsing, and temporary capsules are namespaced by canonical repository path as
well as RunId so parallel checkouts cannot collide.
Evidence and attachment arrays are hard-capped at 32 items each; more must be narrowed before
preflight. Likely-secret detection covers private-key blocks, common vendor tokens, quoted JSON/YAML
credentials, environment-variable prefixes, and sensitive filenames. Conventional placeholder,
sample, example, and test fixtures do not trigger on filename alone when their content is inert.

Use `evidence` when source-versus-deployed drift matters:

```json
{
  "label": "deployed-hook",
  "source": "hooks/pre_tool_use.py",
  "deployed": "C:\\source\\obi-tools\\hooks\\pre_tool_use.py"
}
```

The exact pair is copied into the capsule and hashed. `attachments` copies one
explicit external text file without exposing its parent directory; use
`tools/peer-plan-review.ps1` for an external plan.

## Invocation

Set the primary from the active runtime contract, not ambient shell discovery:

```powershell
$obiPrimaryPlatform = 'codex' # Use 'claude' when Claude Code is the primary.
```

Use `run` only for a deliberately narrow, low-complexity pass expected to finish within four
minutes. A semantically complex review (for example concurrency, security, or lifecycle analysis)
uses durable `start` even when its file list is short:

```powershell
& "$env:OBI_HOME\tools\peer-review.ps1" preflight `
  -Provider auto `
  -Platform $obiPrimaryPlatform `
  -RepoRoot (Get-Location).Path `
  -RequestFile .obi\review\request.json `
  -TimeoutSec 30

& "$env:OBI_HOME\tools\peer-review.ps1" run `
  -Provider auto `
  -Platform $obiPrimaryPlatform `
  -Authorization auto `
  -RepoRoot (Get-Location).Path `
  -RequestFile .obi\review\request.json `
  -TimeoutSec 240
```

Use `start` for a broad/full-bore review or whenever useful review depth may
take longer. Capture the returned `run_id` immediately:

```powershell
$receipt = & "$env:OBI_HOME\tools\peer-review.ps1" start `
  -Provider auto `
  -Platform $obiPrimaryPlatform `
  -Authorization auto `
  -RepoRoot (Get-Location).Path `
  -RequestFile .obi\review\request.json `
  -TimeoutSec 1800 | ConvertFrom-Json
$peerRunId = $receipt.run_id
```

If preflight returns `approval_required`, do not call `run`/`start` until the user approves the
reported expansion. Then use the same request with `-Authorization approved` and the exact
`-ApprovalScopeSha256` from that decision; that explicit mode does not relax capsule,
path-traversal, file-type, or provider-isolation checks.

Supervise it through the broker API, not raw file polling:

```powershell
& "$env:OBI_HOME\tools\peer-review.ps1" status -RepoRoot (Get-Location).Path -RunId $peerRunId
& "$env:OBI_HOME\tools\peer-review.ps1" wait -RepoRoot (Get-Location).Path -RunId $peerRunId -Until activity -WaitSec 120
& "$env:OBI_HOME\tools\peer-review.ps1" wait -RepoRoot (Get-Location).Path -RunId $peerRunId -Until terminal -WaitSec 240
& "$env:OBI_HOME\tools\peer-review.ps1" result -RepoRoot (Get-Location).Path -RunId $peerRunId
```

`wait` always returns within 240 seconds with `terminal`, `activity`, or
`pending`. A current heartbeat with new output or process-tree CPU is useful
work, not a hang; continue other primary work or wait again. A stale heartbeat,
owner loss, first-output failure, idle provider, output overflow, hard timeout,
or cancellation becomes an explicit durable terminal state. Use `cancel` only
when the review is no longer needed; it targets the recorded worker identity and
verifies tree termination. On Windows, Job Object ownership or a successful
PID-checked `taskkill /T` proves the tree; fallback failure is persisted with
`kill_verified=false` and is unhealthy rather than mislabeled as verified.

`auto` selects the opposite of explicit `-Platform codex|claude`. When that semantic input is
omitted, `OBI_PLATFORM` supplies the same value for compatibility. An explicit provider is for
diagnostics and therefore requires approval. The command
prints one compact JSON object containing paths to durable artifacts. `start`
also prints the accepted run ID and worker receipt. Each operation exits zero after an advisory
terminal result is persisted, even when the provider is unavailable or its result fails
validation. An approval-required `run`/`start` also exits zero with `accepted:false`,
`authorization_status:approval_required`, and no run/provider launch; branch on those fields rather
than treating it as a tool failure. Invalid arguments or malformed requests exit non-zero.

External plan (preflight first, then durable `start` by default):

```powershell
& "$env:OBI_HOME\tools\peer-plan-review.ps1" `
  -PlanPath $PlanFile -RepoRoot (Get-Location).Path -Provider auto `
  -Platform $obiPrimaryPlatform -Operation preflight

& "$env:OBI_HOME\tools\peer-plan-review.ps1" `
  -PlanPath $PlanFile -RepoRoot (Get-Location).Path -Provider auto `
  -Platform $obiPrimaryPlatform
```

If the plan preflight requires approval, pass its exact hash on the second call with
`-Authorization approved -ApprovalScopeSha256 <hash>` after the user approves. The wrapper
regenerates the same normalized request and the harness rejects any scope/content drift.

## Result contract

Transport and opinion remain separate:

| Field | Meaning |
|---|---|
| `transport_status` | Lifecycle state including `queued`, `preparing`, `running`, `cancel_requested`, and terminal `completed`, `unavailable`, `timed_out`, `idle_killed`, `output_limit`, `error`, `launch_error`, `cancelled`, `owner_lost`, or `stalled_killed` |
| `peer_verdict` | The peer's `pass`, `fail`, or `inconclusive`; null when no valid opinion completed |
| `validation_status` | `valid`, `partial`, `invalid`, or `not_run` |
| `findings` | Findings whose schema, path, line range, and snapshot hash validated |
| `rejected_findings` | Peer claims withheld from synthesis with mechanical validation errors |
| `limitations` | Peer limitations plus validation rejections |

Citation rejection does not rewrite `peer_verdict`; these are separate facts.
Never use rejected findings. Re-open every accepted citation in the original
working tree before agreeing, disputing, or combining it with a primary finding.

The provider wire schema stays shallow for compatibility with both CLIs. A peer
defines exact path/line entries in a top-level `citations` array and references
their IDs from each finding's `evidence_ids`. The broker rejects missing,
duplicate, unsafe, out-of-bounds, or hash-mismatched IDs and materializes valid
ones as the `findings[].evidence` objects consumed by the summary and primary
reviewer.

Control-plane reads are bounded too: status is capped at 1 MiB, validated
result/request data at 256 KiB, and the manifest at 8 MiB. Claude's event stream
is parsed incrementally under its 32 MiB transport cap. Corrupt or oversized
state becomes an explicit unreadable/invalid terminal result instead of an
unbounded parse on the orchestrator path. Mutable status, cancellation, and
result artifacts use a persistent per-file sidecar lock shared by broker,
health, and Stop-hook readers. Lock acquisition is bounded, atomic replacement
briefly retries non-cooperating Windows readers, and a reader that cannot prove
state remains visible as an outstanding obligation rather than failing open.
The validated consumer result is compacted before publication and is guaranteed
to fit the same 256 KiB reader cap; rejected bodies and diagnostic limitations
are shed before accepted findings, every omission is explicit, and an incomplete
stored result is marked partial. A result committed before a worker/status race
is preserved under a locked compare-and-finalize operation.

Heartbeat and progress status publication are best-effort only while the owned
provider is running: a transient lock/write failure is retained in memory,
retried on the next pulse, and attached to terminal detail. Consumer-result and
terminal-status commits remain strict durability gates.

## Provider isolation

The Codex adapter uses no named/user profile: `--ignore-user-config`,
`--ignore-rules`, `--strict-config`, `--ephemeral`, explicit model and `high` effort, web
search disabled, no project config at the provider working root, one output schema, and one
last-message result. The configured managed Codex requirements forbid
`approval_policy="never"` (exec mode then rejects every shell call and the review completes
having read nothing), so the adapter runs `--approve-for-me`. That mode does NOT enforce a
read-only sandbox, so read-only is enforced by the sanitized capsule copy plus a post-run hash
check: any capsule file that changed marks the review `capsule_modified` and invalid. A
transport-complete review whose tool calls were all refused is recorded `unavailable`, never a
clean pass.

The Claude adapter uses print mode, safe mode, only `Read,Grep,Glob`, strict
empty MCP configuration, slash commands disabled, `dontAsk`, no session
persistence, verbose streaming JSON output, and the same schema. Neither adapter receives the
developer repository as its working directory; both receive a sanitized
snapshot under a temporary capsule root. Off-manifest citations can never enter
the validated finding list.

## Attribution and synthesis

Use `[Peer: Codex]` or `[Peer: Claude]` for a reproduced peer finding,
`[Primary]` for an independent finding, `[Synthesis]` when both found it, and
`[Peer — disputed]` with a local-evidence rebuttal. Keep a disputed-findings
section even when empty.

### Accepted-finding taxonomy

The accepted-finding taxonomy remains compatible with
`tools/log-codex-catch.ps1` while historical telemetry retains that filename:

| Category | Meaning |
|---|---|
| `invented-api` | API or behavior lacks evidence |
| `missed-edge-case` | Failure/edge path omitted |
| `test-gap` | Required behavior is not exercised |
| `security` | Security or boundary defect |
| `regression-risk` | Change can break existing behavior |
| `doc-mismatch` | Executable behavior and contract disagree |
| `plan-gap` | Plan omits a dependency or verification step |
| `other` | Verified issue outside the named classes |

Acceptance equals a catch; a peer utterance does not. Log only findings the
primary reviewer reproduces and adopts. Pass the producing provider as
`-PeerProvider codex|claude` to `tools/log-codex-catch.ps1`; the filename is
historical, and Codex analytics intentionally exclude provider-attributed
Claude entries.

## Plan critique scope

Plan critique runs in max rigor only; standard rigor has no Phase 0-lite. Every usable critique
pass appends one `record_type=attempt` row (including zero-finding passes) so the catch log carries
denominators and timing.

# rigor=max - Phase 0 and Gates 2-5

> **Read this file ONLY when running `rigor: max`** (i.e. `/obi-auto-max`).
> `orchestration/obi-auto.md` points here instead of inlining this content, so a plain
> `/obi-auto` run does not pay ~180 lines of context it will never use. A `rigor: max`
> run reads both and pays the same total as before.
>
> Companion: `docs/policies/obi-auto-max-schema.md` is the plan-file SCHEMA (what a plan
> may declare). This file is the OPERATIONAL contract (what the orchestrator does).

## When rigor=max: Phase 0 — Prereq Lock-In (Gate 1)

Schema: `docs/policies/obi-auto-max-schema.md`.

### Plan-file resolution

1. If `--plan <abs-path>` was passed in the task line, use it.
2. Else, use `.obi/reports/<run_id>-plan.md` if it exists.
3. Else, SYNTHESIZE the minimal plan from the task line into `.obi/reports/<run_id>-plan.md`
   (Goal / Scope / Constraints only), append `reversible_default_selected`, and continue. Include
   `phase0:` only for a genuinely material input, and no `verification.grep` unless it is derivable
   from the task line. A missing plan is a reversible in-scope gap, not a stop.

Obi never writes under `~/.claude/plans/`: Claude Code prompts for approval on that path and the
peer harness rejects it as outside `C:\src`. Plans live in `.obi/reports/`.

### Sequence

1. **Read** the plan file via the `Read` tool.
2. **Idempotence guard.** If the plan body already has `runtime.phase0.answers:` (non-empty), Phase 0 is already locked: skip steps 3-5. Resume at step 6 (re-run probes) and step 7 (refresh `runtime.phase0.peer`). Safe to resume after compaction or session restart.
3. **Parse phase0:** Run `$env:OBI_HOME\tools\parse-plan-phase0.ps1 -PlanPath <plan>`. Empty `[]` => skip to step 5. (All `tools/*` references resolve via `$env:OBI_HOME` — the tools root configured by deploy.ps1, defaulting to `~/.obi-tools`.)
4. **Lock each `phase0:` entry** in declared order.
   - If `default:` is present and the choice is reversible, conservative, within the original task,
     and causes no destructive/external write or secret handling, select it automatically and
     append `reversible_default_selected`, then record source `auto-default` plus the assumption in
     the recovery ledger.
   - Otherwise the entry is a material product/authority input. Fire `AskUserQuestion` once using
     `docs/askuserquestion-format.md`; this is not a retry/fallback/continuation question.
   - Options come from `options:` (max 4). `free_text: true` adds "Other (specify)".
5. **Plan-file write-back** (use `Edit`, not `Write`):
   - One `Edit` per `<placeholder>` token replacing it with the locked answer.
   - Append structured `runtime.phase0` block at end-of-file:
     ```yaml
     runtime:
       phase0:
         locked_at: "<UTC ISO>"
         plan_file: "<abs path>"
         answers:
           - id: <id>
             question: "<verbatim>"
             answer: "<user choice>"
             locks_field: "<from phase0:>"
             source: "auto-default" # or "AskUserQuestion" for a material input
         peer:
           status: "pending"   # overwritten in step 7
           provider: ""
           reason: ""
           checked_at: ""
     ```
   - Downstream surprise detection (Gate 5) reads from `runtime.phase0.answers[]`, NOT from `default:` or live conversation.
6. **Run probes** keyed off locked answers. Probe routing per `docs/policies/obi-auto-max-schema.md` Probe Routing table. Each probe writes to `.obi/runtime/probes-<UTC>.jsonl`. **Append a `runtime.probes` block** to the plan file (one entry per probe: `id`, `ts`, `status`, `data_ref`).
7. **Run peer-on-plan** through the canonical harness:
   ```powershell
   # Set from the active runtime contract, not ambient shell state.
   $obiPrimaryPlatform = 'codex' # Use 'claude' when Claude Code is the primary.
   $peerReceipt = & "$env:OBI_HOME\tools\peer-plan-review.ps1" `
       -PlanPath $PlanFile -RepoRoot (Get-Location).Path -Provider auto `
       -Platform $obiPrimaryPlatform `
       -Operation start -TimeoutSec 1800 | ConvertFrom-Json
   $peerRunId = $peerReceipt.run_id
   ```
   The start operation itself runs in the foreground and returns a durable receipt. Retain its
   RunId, then use only bounded `peer-review.ps1 status/wait/result/cancel` calls until the result
   is consumed or explicitly cancelled. Never add provider CLI arguments, use shell background
   mode, tail raw files, busy-poll, or retry a terminal result. Read only the referenced
   `status.json`, `result.json`, and `summary.md`; never load raw events, stderr, or the
   provider's unvalidated output into model context.

   Treat transport and opinion as separate facts. Only
   `transport_status: "completed"` with `validation_status: "valid"` or `"partial"` can supply
   accepted findings. Any other terminal transport/validation state is peer unavailability for
   this Phase 0 attempt: update `runtime.phase0.peer.status: "unavailable"`, capture its one-line
   limitation in `reason`, and continue without retry. A completed peer `fail` verdict remains
   advisory; reproduce every accepted finding against the original tree and attachment before
   deciding whether to amend the plan. Never let a rejected citation affect the plan.

   Update `runtime.phase0.peer` after every accepted invocation: set `provider` from the status,
   `checked_at` to UTC, and `status: "ran"` only for a completed, usable validated result.

   **Log the catches.** For each peer critique finding the primary reviewer reproduces, accepts,
   and folds into the plan, append one catch. This is the `phase=plan` capture point — skip it and
   `.obi/codex-catches.jsonl` stays empty, so promotion telemetry cannot gather data. Acceptance
   equals catch; a peer utterance is not a catch. Cite accepted finding IDs and log ONLY those.
   ```
   & "$env:OBI_HOME\tools\log-codex-catch.ps1" -Repo "<target-project>" -Ref "<run-id>" `
       -Phase plan -Category <finding.category> -Severity <finding.severity> `
       -Summary "<finding.summary>"
   ```
   The peer schema's `category` and `severity` enums match the historical catch log, so an accepted
   finding maps 1:1 without reinterpretation. Count lines written into `catches_logged` below.
   For every completed usable peer result, append one attempt row after catch triage, including a
   zero-finding pass. Calculate `DurationMs` from the terminal status timestamps. This supplies the
   denominator and timing that finding-only history lacks:
   ```powershell
   & "$env:OBI_HOME\tools\log-codex-catch.ps1" -Repo "<target-project>" -Ref "<run-id>" `
       -Phase plan -PeerProvider $peerStatus.provider -Attempt -PassId $peerRunId `
       -DurationMs <terminal-minus-started> -FindingCount $peerResult.findings.Count `
       -AcceptedCount $catchesLogged
   ```
   Peer `status: unavailable` means `catches_logged: 0` and no attempt row; transport failure is not
   a clean critique pass.
8. **Write phase state** to `.obi/state/phase-0-prereqs.json`:
   ```json
   { "phase": 0, "status": "complete", "plan_file": "...", "answers_count": N,
     "probe_runs": N, "peer_status": "ran|unavailable", "catches_logged": N, "completed_at": "<UTC ISO>" }
   ```
9. Emit: `PHASE 0 COMPLETE`.

### Phase 0 failure modes

| Mode | Handling |
|---|---|
| Peer unavailable | `runtime.phase0.peer.status: unavailable` in plan + state JSON. Continue without retry. Peer unavailability is not recorded in `.obi/session-quality.jsonl`. |
| Probe `auth_failure` | Advisory probes record and continue. If a later required gate needs that credential, append terminal `hard_stop` evidence `required_credentials_unavailable`; do not ask merely to continue. |
| Probe `not_found` / `network_failure` / `unknown` | Advisory. Log to probes JSONL; continue unless a Gate-2 grep gate requires the probe. |

---

## When rigor=max: Gate 2 — Hard Grep Gates

Plan-file schema (`verification.grep` block) per `docs/policies/obi-auto-max-schema.md`. The
block must be literal YAML (`verification:` / `grep:` / `- after_phase:` ...). A Markdown heading
or prose description of the same check is invisible to the gate, which then reports
`no verification: block in plan; nothing to check` and exits 0 — treat that message on a plan
that was supposed to carry a gate as a plan-file defect, run the grep manually, and record it
via `check_failure_fixable`.

After each phase completes, fire the gate when a `verification.grep` entry has `after_phase`
matching the just-completed phase:

```
powershell -NoProfile -File $env:OBI_HOME\tools\run-grep-gates.ps1 -PlanPath <plan> -Phase <N> -RepoRoot <target-repo>
```

**Pass `-RepoRoot` explicitly.** The gate runs from the deployed `$env:OBI_HOME` copy; without it the
scan root is inferred from the cwd's git top-level, and a wrong root reports `clean` for the wrong
repo. Naming the target repo keeps the gate correct regardless of the orchestrator's cwd.

Implementation: PowerShell `Select-String -AllMatches` only — there is no `rg` path. Post-filters
matched paths against the `allow_files` regex list using `-notmatch`.

Exit codes:
- `0` (clean) — advance.
- `1` (forbidden match) — emit `GREP GATE FAIL [phase N]: <fail_message>`. Read `.obi/runtime/grep-gate-<N>-<UTC>.json`. Loop back per standard fail-loop rules.
- `2` (script error) — append `check_failure_fixable`, repair task-local configuration, and rerun
  once with a materially different fix. Missing authoritative config or scope expansion is a named
  terminal boundary.

---

## When rigor=max: Gate 3 — Probe Library

Probes live in `tools/probes/` with fixed JSON schema (`{probe, ts, status, input, data, error}`) emitted by `tools/probes/_lib.ps1`.

| Probe | Args | Returns |
|---|---|---|
| `namespace_kind.ps1` | `-Namespace <path>` | `{kind: "user"\|"group", id, full_path}` |
| `runner_tags.ps1` | `-ProjectId <int>` | `{available_tags, shared_runners}` |
| `pages_access.ps1` | `-ProjectId <int> [-PublicUrl <url>]` | `{pages_enabled, access_level, anonymous_reachable, content_redirect}` |
| `marketplace_reach.ps1` | `-MarketplaceUrl <url>` | `{reachable, status_code, latency_ms}` |
| `mirror_existence.ps1` | `-SourceProj <path> -MirrorTarget <path>` | `{mirror_exists, last_sync, target_path}` |

Probes are general-purpose. rigor=max wires them in Phase 0 (assumption capture) and Phase 5 Integrate (assumption validation before push). Raw data in `.obi/runtime/probes-<UTC>.jsonl`; plan file stores path refs only.

### Stderr classification (in `_lib.ps1`)

| stderr substring | status |
|---|---|
| `not authorized`, `401`, `403` | `auth_failure` |
| `404`, `not found` | `not_found` |
| connection refused / timeout / DNS | `network_failure` |
| anything else with non-zero exit | `unknown` |

---

## When rigor=max: Gate 4 — Iterate-Until-Green

Wraps `obi-pipeline-monitor`. After each push that triggers CI:

1. Dispatch `obi-pipeline-monitor` with `--iterate-until-green N` (default 3, override via `.obi/auto-max.yaml: pipeline.max_iterations`).
2. The monitor classifies failures per `platforms/claude-code/agents/obi-pipeline-monitor.md`:

| Class | Detection regex | Fix |
|---|---|---|
| `runner-unavailable` | Waiting for a runner, unmatched labels, or queued >5m | Check `runs-on:` labels in `.github/workflows/`; cross-ref `tools/probes/runner_tags.ps1` |
| `quota` | `(spending limit\|exceeded.*minutes\|usage limit\|billing)` | Append terminal `hard_stop` evidence `external_ci_quota_unavailable`; no continuation question |
| `yaml-error` | Invalid workflow file, workflow is not valid, or unexpected value | Inspect the workflow with `gh workflow view` and validate its YAML |
| `image-pull-failure` | `(image.*not found\|pull access denied\|manifest unknown)` | Probe registry; suggest fallback image |
| `script-error` | catch-all | Hand back to Author with last 50 lines of trace |

3. Pipeline iteration counter in `.obi/runtime/pipeline-iter.json` (separate from strike counter).
4. Two consecutive identical classifications fall through to the 3-strike checkpoint at
   `obi-pipeline-monitor.md`. Diagnose and select a materially different bounded recovery. Fixable
   failures return to Author/Integrate and continue. Retry exhaustion alone is not terminal; only a
   separately named external-resource, source, credential, safety, or data-integrity boundary ends
   the autonomous run.

---

## When rigor=max: Gate 5 — Auto-Memory-Write

After each phase's grep gate succeeds and BEFORE advancing:

1. **Build surprise candidates:** call `compute_surprises(phase, locked_answers, probe_outcomes)` from `hooks/core/auto_memory_capture.py`. Pass: `phase` (just-completed), `locked_answers` (`runtime.phase0.answers[]` from plan), `probe_outcomes` (most recent `.obi/runtime/probes-*.jsonl`).
2. **Inline classification** (no recursive rigor=max loop): for each candidate, prompt:
   > Given expected `<X>` and actual `<Y>`, is this surprise actionable for memory? Return JSON `{confidence: 0.0-1.0, summary: <1-line>, type: <user|feedback|project|reference|tool>}`.
3. **`capture_surprise(candidate, verdict_raw)`**: parse JSON (lenient). Gate on `confidence >= 0.7`. Below threshold or invalid JSON => log `auto-memory-skip` to `.obi/session-quality.jsonl`. At/above threshold => write to both locations below.

### Proposal landing

Surprises land at TWO locations, deduplicated by `(phase, captured_at)` on re-runs:

1. **Markdown audit** — `~/.claude/.obi/pending/surprise-<phase>-<UTC>-<slug>.md` with frontmatter (`name`, `description`, `type`, `auto_captured: true`, `confidence`, `phase`, `captured_at`).
2. **Pending-learnings entry** — appended to `~/.claude/.obi/pending-learnings.json` so `/obi-memory-review approve <#>` can graduate it.

---

## When rigor=max: Config Loader

Defaults baked in by `tools/load-auto-max-config.ps1`. Per-repo override at `.obi/auto-max.yaml` (gitignored). Validate with `tools/config-guardian.ps1 -CheckOnly`.

```yaml
phase0:
  required: true
  peer_review: true
grep_gates:
  fail_fast: true
probes:
  parallel: false
pipeline:
  max_iterations: 3
auto_memory:
  enabled: true
  confidence_threshold: 0.7
```

# Rigor=max Plan-File Schema Reference

> **Audience:** plan authors, `/obi-auto` rigor=max orchestrator, `tools/parse-plan-phase0.ps1`, `tools/run-grep-gates.ps1`, and `hooks/core/auto_memory_capture.py`.
> **Parser:** regex-based (per `tools/bump-version.ps1:55-60` pattern). No YAML library is introduced. Plans must use the simplified flat-block format documented below.

## Overview

A plan file consumed by `/obi-auto` (rigor=max) is a Markdown file with two optional structured blocks parsed by the orchestrator:

1. `phase0:` — Phase 0 prereq lock-ins (reversible defaults auto-lock; material inputs drive `AskUserQuestion`).
2. `verification.grep:` — hard grep gates that fire after specific phases.

Plus two write-back blocks the orchestrator inserts during Phase 0:

3. `runtime.phase0:` — locked answers + peer-on-plan status.
4. `runtime.probes:` — file-path references to probe outputs (raw data lives in `.obi/runtime/probes-<UTC>.jsonl`, not in the plan).

A plan with neither `phase0:` nor `verification.grep:` is still valid — it just becomes a rigor=max run with chrome relabeled `Lane: max`.

## `phase0:` block

Drives one lock per entry in declared order. A declared `default` is auto-selected only when it is
reversible, conservative, in scope, and has no destructive/external-write or credential effect.
Other entries drive one material-input `AskUserQuestion`; format per
`docs/askuserquestion-format.md`. Never use these entries merely to authorize retry or continuation.

```yaml
phase0:
  - id: target_namespace
    question: "Where does the fork land?"
    placeholder: "<target-namespace>"
    options: [user, group, instance]
    default: group
    locks_field: bootstrap-config.json:repoBase
  - id: runner_tags
    question: "Which runner tags do CI jobs need?"
    placeholder: "<runner-tags>"
    free_text: true
```

### Field semantics

| Field | Required | Type | Behavior |
|---|---|---|---|
| `id` | yes | string (snake_case) | Stable key for this question. Used in `runtime.phase0.answers[]`, probe routing, and surprise-detection lookups. |
| `question` | yes | string | Verbatim decision text recorded for auto-default or shown via `AskUserQuestion`. |
| `placeholder` | optional | string | Token (e.g. `<target-namespace>`) replaced with answer in the plan body via `Edit` tool. Skip if no body replacement needed. |
| `options` | optional | list of strings | Becomes `AskUserQuestion` option labels. If absent + `free_text: true`, becomes a single "Other (specify)" slot. |
| `default` | optional | string | Auto-locked when it meets the reversible/conservative boundary; otherwise rendered as "(Recommended)" in `AskUserQuestion`. |
| `locks_field` | optional | string | `<file>:<jsonpath>` — annotation for downstream probes/integrations to know what config field this answer locks. Read by probes; not enforced by the parser. |
| `free_text` | optional | boolean | `true` adds a free-text "Other" option. Mutually compatible with `options`. |

### Probe routing

If a `phase0:` entry's `id` matches a probe name (e.g. `target_namespace` -> `tools/probes/namespace_kind.ps1`), the orchestrator runs the matching probe with the answer as input. Wiring is keyword-based:

| `phase0` id | Probe |
|---|---|
| `target_namespace` | `namespace_kind.ps1 -Namespace <answer>` |
| `runner_tags` | `runner_tags.ps1 -ProjectId <derived>` |
| `pages_access` | `pages_access.ps1 -ProjectId <derived>` |
| `marketplace_url` | `marketplace_reach.ps1 -MarketplaceUrl <answer>` |
| `mirror_target` | `mirror_existence.ps1 -SourceProj <derived> -MirrorTarget <answer>` |

Other ids are accepted but won't trigger any probe.

## `verification.grep:` block

Hard grep gates fire only when their `after_phase` matches the just-completed phase (1-10). The
block is parsed as YAML from the plan body; a prose or Markdown-heading description of the same
rule is not recognized (`run-grep-gates.ps1` reports "nothing to check" and passes), so copy the
shape below verbatim.

```yaml
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "TODO\\(prereq\\)"
        - "<.*placeholder.*>"
      allow_files:
        - "docs/templates/.*"
        - "phases/.*/command\\.md$"
      fail_message: "Placeholder still present after Integrate"
```

### Field semantics

| Field | Required | Type | Behavior |
|---|---|---|---|
| `after_phase` | yes | int 1-10 | Run this gate immediately after the named phase succeeds, before advancing. |
| `forbidden_patterns` | yes | list of regex strings | Any match on a file outside `allow_files` fails the gate (exit 1). |
| `allow_files` | optional | list of regex strings | File paths matching ANY entry are exempt from the forbidden-pattern check. |
| `fail_message` | optional | string | Surfaced verbatim in `GREP GATE FAIL [phase N]` output. |

### Failure contract

`tools/run-grep-gates.ps1` writes JSON to `.obi/runtime/grep-gate-<phase>-<UTC>.json`:

```json
{
  "phase": 5,
  "matches": [{"file": "...", "line": 42, "text": "TODO(prereq)"}],
  "fail_message": "Placeholder still present after Integrate",
  "proposed_allow_files": ["docs/templates/foo.md"]
}
```

`proposed_allow_files` is a hint based on file paths matched — not authoritative. Author can extend `allow_files` if a match is intentional, or fix the file.

Multiple `verification.grep` entries per plan are supported (different phases, different rules).

## Write-back: `runtime.phase0:`

Appended to the plan file at end-of-Phase-0 by the orchestrator. Required because subsequent probes and `auto_memory_capture` must read authoritative answers from the plan, NOT from `default:` values or the live conversation.

```yaml
runtime:
  phase0:
    locked_at: "2026-05-02T17:32:00Z"
    plan_file: "C:/src/obiwag-agents/.obi/reports/20260901T221915Z-plan.md"
    answers:
      - id: target_namespace
        question: "Where does the fork land?"
        answer: "user"
        locks_field: "bootstrap-config.json:repoBase"
        source: "auto-default"
      - id: runner_tags
        question: "Which runner tags do CI jobs need?"
        answer: "default-build"
        locks_field: ""
        source: "AskUserQuestion"
    peer:
      status: "ran"  # or "unavailable" or "pending"
      provider: "codex"  # or "claude" or ""
      reason: ""
      checked_at: "2026-05-02T17:33:14Z"
```

The `peer:` sub-block is **required** in every locked plan. It is initialized with `status: "pending"` during the answers write-back (Phase 0 step 5) and overwritten in step 7 with the real outcome. Three valid statuses:

- `pending` — write-back complete but the peer harness has not yet been invoked. Transient; should never persist past a successful Phase 0 run.
- `ran` — the peer harness reached `transport_status: completed`; `provider` records the selected peer. Validation and verdict remain separate in the harness result.
- `unavailable` — the harness reached any other terminal transport state, or validation yielded no usable result. `reason` carries a one-line cause, `provider` is set when known, and `checked_at` is set.

The orchestrator MUST overwrite `peer` after step 7 regardless of outcome. The schema parser does not enforce this; the contract is enforced by `docs/policies/rigor-max-gates.md` (its "Phase 0 — Prereq Lock-In" section). `orchestration/obi-auto-max.md` is only a thin wrapper that re-invokes `obi-auto.md` with `rigor: max`, and `obi-auto.md` in turn points at the gates file.

### Reading rules

- Surprise detection: read expected values from `runtime.phase0.answers[]`, not from the original `phase0:` schema.
- `source` is `auto-default` for a reversible declared default or `AskUserQuestion` for a material
  input. Downstream consumers must accept both and must not re-derive the answer from `default:`.

## Write-back: `runtime.probes:`

Light pointer block — full data lives in `.obi/runtime/probes-<UTC>.jsonl`.

```yaml
runtime:
  probes:
    - id: namespace_kind
      ts: "2026-05-02T17:33:21Z"
      status: "ok"
      data_ref: ".obi/runtime/probes-2026-05-02T173321Z.jsonl"
```

`status` enum: `ok | auth_failure | not_found | network_failure | unknown` (per `tools/probes/_lib.ps1`).

## Examples

### Minimal plan (no Phase 0, no grep gates)

```markdown
# My Task

Standard /obi-auto run with Lane: max chrome.
```

### Phase-0-only plan

```markdown
# Fork sample-project

Goal: fork sample-project to <target-namespace>, strip user-specific code, push.

phase0:
  - id: target_namespace
    question: "Which namespace owns the fork?"
    placeholder: "<target-namespace>"
    options: [user-personal, project-team, instance-public]
    default: project-team
```

### Plan with grep gates

```markdown
# Migrate vendor wrapper

verification:
  grep:
    - after_phase: 2
      forbidden_patterns:
        - "TODO\\(vendor\\)"
      fail_message: "Vendor TODO must be resolved before Simplify"
    - after_phase: 5
      forbidden_patterns:
        - "import-old-vendor"
      allow_files:
        - "tests/migration/.*"
      fail_message: "Old vendor import remains after Integrate"
```

## Cross-references

- `tools/parse-plan-phase0.ps1` — emits ordered JSON for the orchestrator.
- `tools/run-grep-gates.ps1` — fires gates and writes `.obi/runtime/grep-gate-*.json`.
- `tools/probes/_lib.ps1` — probe runtime + JSON schema emitter.
- `policies/rigor-max-gates.md` — rigor=max Phase 0 protocol uses this schema.

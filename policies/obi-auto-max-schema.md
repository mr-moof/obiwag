# Rigor=max Plan-File Schema Reference

> **Status:** v0.69.31 contract.
> **Audience:** plan authors, `/obi-auto` rigor=max orchestrator, `tools/parse-plan-phase0.ps1`, `tools/run-grep-gates.ps1`, and `hooks/core/auto_memory_capture.py`.
> **Parser:** regex-based (per `tools/bump-version.ps1:55-60` pattern). No YAML library is introduced. Plans must use the simplified flat-block format documented below.

## Overview

A plan file consumed by `/obi-auto` (rigor=max) is a Markdown file with two optional structured blocks parsed by the orchestrator:

1. `phase0:` — Phase 0 prereq lock-in questions (drives `AskUserQuestion`).
2. `verification.grep:` — hard grep gates that fire after specific phases.

Plus two write-back blocks the orchestrator inserts during Phase 0:

3. `runtime.phase0:` — locked answers + codex-on-plan status.
4. `runtime.probes:` — file-path references to probe outputs (raw data lives in `.obi/runtime/probes-<UTC>.jsonl`, not in the plan).

A plan with neither `phase0:` nor `verification.grep:` is still valid — it just becomes a rigor=max run with chrome relabeled `Lane: max`.

## `phase0:` block

Drives one `AskUserQuestion` per entry, in declared order. Format per `docs/askuserquestion-format.md`.

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
| `question` | yes | string | Verbatim prompt shown to user via `AskUserQuestion`. |
| `placeholder` | optional | string | Token (e.g. `<target-namespace>`) replaced with answer in the plan body via `Edit` tool. Skip if no body replacement needed. |
| `options` | optional | list of strings | Becomes `AskUserQuestion` option labels. If absent + `free_text: true`, becomes a single "Other (specify)" slot. |
| `default` | optional | string | Recommended option (rendered as "(Recommended)" in `AskUserQuestion` per `docs/askuserquestion-format.md`). |
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

Hard grep gates fire only when their `after_phase` matches the just-completed phase (1-10).

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
    plan_file: "C:/Users/user/.claude/plans/my-plan.md"
    answers:
      - id: target_namespace
        question: "Where does the fork land?"
        answer: "user"
        locks_field: "bootstrap-config.json:repoBase"
        source: "AskUserQuestion"
      - id: runner_tags
        question: "Which runner tags do CI jobs need?"
        answer: "win-container-bld"
        locks_field: ""
        source: "AskUserQuestion"
    codex:
      status: "ran"  # or "unavailable" or "pending"
      reason: ""
      checked_at: "2026-05-02T17:33:14Z"
```

The `codex:` sub-block is **required** in every locked plan. It is initialized with `status: "pending"` during the answers write-back (Phase 0 step 5) and overwritten in step 7 with the real outcome. Three valid statuses:

- `pending` — write-back complete but Codex has not yet been invoked. Transient; should never persist past a successful Phase 0 run.
- `ran` — Codex executed; transcript was the audit. `reason: ""`.
- `unavailable` — Codex failed (not on PATH, exec failed, etc.). `reason` carries a one-line cause, `checked_at` is set.

The orchestrator MUST overwrite `codex` after step 7 regardless of outcome. The schema parser does not enforce this; the contract is enforced by `orchestration/obi-auto-max.md`.

### Reading rules

- Surprise detection: read expected values from `runtime.phase0.answers[]`, not from the original `phase0:` schema.
- `source` is `AskUserQuestion` for v0.69.31. Reserved for future cron / answers-file modes.

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
# Fork driver-hype

Goal: fork driver-hype to <target-namespace>, strip user-specific code, push.

phase0:
  - id: target_namespace
    question: "Which namespace owns the fork?"
    placeholder: "<target-namespace>"
    options: [personal, organization, public]
    default: personal
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
- `orchestration/obi-auto.md` — rigor=max Phase 0 protocol uses this schema.

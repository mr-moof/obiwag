# Agent efficiency runtime

All five changes are one bundle: source-grounded handoffs, deterministic permitted transitions,
early effort routing, conditional Learning, and independent read batches. The helper uses only
local files and existing lifecycle authorities. It does not call a new model service.

## Roots and entry point

Resolve `project_root` from the task repository and `runtime_root` from the explicit active
Obi installation (`OBI_HOME`), or the candidate checkout during isolated validation. Never infer
the project from the helper location. Source policy is `<runtime_root>/phases/phase-table.json`.
All commands use this form; write inputs as UTF-8 JSON, not shell-interpolated text:

```powershell
python "$runtimeRoot/tools/efficiency.py" route --project-root "$projectRoot" `
  --runtime-root "$runtimeRoot" --phase-table "$runtimeRoot/phases/phase-table.json" `
  --input "$projectRoot/.obi/state/route-input.json" --output "$projectRoot/.obi/state/route.json"
```

For source contracts outside the task project/runtime, also pass `--source-root` with the explicit
Obi checkout (`OBIWAG_SOURCE`, after verification), then use `root: "source"` in references.
Do not assume `OBI_HOME` contains every source contract; its deployed tools and policy are separate
from platform instruction files. Load only the relevant section of this reference for the current
operation, and keep hashes/paths rather than recopying this entire document into worker prompts.

## Before Discovery and Author

In one read batch, inspect task acceptance criteria, repository status, relevant project policy,
available reference implementations, and test entry points. Each result keeps its own error and
unknown status. Record evidence references for each risk assessment; one unknown cannot be
cancelled out by another favorable assessment.

The `route` input fields are `platform` (`claude` or `codex`), `rigor` (`standard` or `max`),
`familiarity`, `scope`, `security_impact`, `api_impact`, `integration_novelty`, `evidence_gaps`, and
`tests_available`. Only explicit values `familiar`, `bounded`, `none`, `none`, `known`, `false`, and
`true` respectively permit routine routing. Everything else, including unknown rigor, selects
strong. Keep the evidence alongside the input JSON. These are semantic assessments; the helper
only makes their conservative combination mechanical.

Consume the result immediately:

- Claude: pass `-RoutingTier <tier> -PhaseTablePath <explicit-policy>` to `dispatch-worker.ps1`.
  The dispatcher resolves the policy mapping and records actual model/effort; an omitted tier
  defaults to strong for Discovery/Author. Other phases retain their persona defaults.
- Codex: pass result `model` and `effort` as `spawn_agent.model` and `reasoning_effort`, with
  `fork_turns="none"`. Existing native timeline/synthesis/resume rules still apply.

Rerun after Discovery and material scope growth. Promote to strong when evidence deteriorates;
do not restart a live thread or replay completed work just to change tier. The primary thread's
already-selected model cannot be changed by this procedure.

## Handoff and independent reads

Run `handoff` using the same root arguments before each new worker or inline phase. Input:

```json
{
  "run_id": "example",
  "from_phase": 1,
  "to_phase": 2,
  "acceptance_criteria": ["Implement only the approved behavior"],
  "settled_facts": ["Reference implementation found"],
  "unknowns": [],
  "source_artifacts": [
    {"id": "discovery", "root": "project", "path": ".obi/discovery-report.md",
     "relevance": "Source evidence and implementation boundaries", "required": true}
  ],
  "independent_read_batches": [
    {"name": "project-policies", "reads": [
      {"id": "project-contract", "root": "project", "path": "AGENTS.md"}
    ]}
  ]
}
```

Include relevant phase contracts, acceptance criteria, evidence, changed-file references, and
unresolved questions, rather than accumulated conversation. Shared evidence stays in files;
follow-up handoffs refer to the complete reachable base plus changed facts. A fresh worker must
never depend on another thread's hidden context. Required sources remain retrievable by exact
root/path/hash. The helper deduplicates excerpts, drops optional excerpts first, then replaces
required excerpts with references. It fails if remaining metadata exceeds policy budgets.

Run `validate` on the generated packet before use and after any source changes. Missing or stale
sources require rebuilding or explicit missing evidence, never silent truncation. Keep the stable
phase contract/persona first and the dynamic packet last in the actual dispatch prompt. Avoid
duplicating the persona in excerpts. Cache reuse is a possible provider benefit, not a guarantee.

Before each dispatch, run `inventory` with `components` containing every controlled system,
persona, stable instruction, and dynamic prompt file (`root` and `path` per component). This
checks the whole controlled input against `efficiency.controlled_prompt_bytes`; the handoff's
section budget alone is insufficient. Codex records the inventory beside its native timeline.
Claude also measures its final composed persona/budget instructions plus task prompt before
launch and rejects overflow. Provider-added instructions, tool schemas, hidden context and
actual billed tokens remain explicitly unknown until provider telemetry supplies them.

Named batches: `project-policies` for independent policy/reference reads;
`change-inventory` for independent changed files; `verification-results` for independent test
and status artifacts. The helper uses bounded threads, stable result order, and separate
result/error/unknown fields. Small UTF-8 files include text; larger files return a hash and a
retrieval-required flag. Read relevant sections through the normal source tools. Do not batch a
mutation with its verification, approval with sending, or a dependent read before its prerequisite.

## Phase boundaries

Run `transition` with input `{"lane":"standard","phase":4,"signal":"REVIEW COMPLETE: PASS",
"handoff":{}}`. It consumes `default_strategy`, accepted signals, lane order, and configured
skip transitions. Re-review and README-review require `handoff.verdict_report` naming a project
artifact with a parsed `Verdict: PASS` line. Other outcomes keep the current phase and require normal resolution. Phase 5 no-op
skips require independent Git verification of the integration report. Max-rigor gates are still
required at their existing boundaries; a returned next phase never waives a gate.

The primary executes returned actions; this is not a new scheduler. Recovery requests use the
existing `native_phase_state.py` validation and `autonomous_recovery.py` ledger. A request names
the same run, phase, provider, exact evidence, and project-contained state paths. Replays reuse
the recorded decision. Do not create competing timeout or retry state.

## Learning eligibility

The `learning` input names explicit `state_root` (the active runtime's `.obi` directory), plus
`session_findings` and `codex_hook_evidence`. Each semantic evidence object requires
`available: true` and `parse_ok: true`; session findings has integer `corrections`,
`reusable_discoveries`, and `unresolved_failures`, and hook evidence has integer `candidates`.
On Claude the latter describes its equivalent observed hook evidence. Missing Codex hooks never
imply zero candidates.

The helper reads `pending-learnings.json` (`learnings` array), `pending/*.md`, and
`<state_root>/state/last-maintenance.json` (`last_run_iso`) itself. A missing queue/directory, malformed data,
forced/due maintenance, or a nonempty project `.obi/codex-catches.jsonl` dispatches Learning.
The existing catch-log owner defines missing/empty logs as zero; nonempty logs go through the
existing threshold/suppression procedure, conservatively avoiding a second threshold engine.
The returned evidence contains source paths/hashes and six eligibility fields: pending queues,
memory health, session findings, hook candidates and blindspot evidence. Preserve the result in
the run report; do not replace actual observations with asserted empty counts.

Only all-valid zero counts and `due: false` return `skip`. Save that decision and use its exact
signal; no learner/native timeline is created. A phase-10 transition must carry the same full
`handoff.learning_evidence`. Otherwise run the existing memory-review/capture procedure.
The transition rereads queues/health through the supplied `state_root`; a changed queue or a
hand-written empty-count dictionary cannot authorize a skip. Phase 4 FAIL advances to Integrate
with `required_action: integrate_findings`, preserving the existing lane/reclassification rules.
Phase 0 accepts only `PHASE 0 COMPLETE` in the max lane; extra gates remain required.

## Evidence and rollout

Record controlled prompt bytes separately from observed provider input/output/cached tokens.
When provider telemetry is missing, use `unknown`. Record actual dispatches, model/effort,
retrieval follow-ups, repeated-context requests, retries, and elapsed time. Configured deadlines
are not observed latency. File-size inventory is not evidence that every file was model-loaded.

Use development fixtures and a separate defect holdout. Keep real source retrieval available to
Review. At most three representative live tasks per runtime, one candidate retest per task, and
one tuning pass are allowed. Compare quality and dispatch/read counts before claiming savings.
All five ship together after tests, review, isolated deployment and rollback rehearsal; a failed
required outcome keeps the whole bundle pending. Keep the release evidence in a local report outside the public distribution.

## Bundle rollback

Use `tools/efficiency_bundle.py` before deployment. Legacy Codex manifests contain mixed absolute
runtime and relative project/user targets; never treat that file as one root. Prepare one explicit
scope per runtime, Claude configuration, Codex configuration, or project root. Each scope JSON has
`source_root` and `mappings` from relative deployed target paths to relative candidate source paths.
Use only reviewed bundle files and keep unrelated user configuration outside those mappings.

```powershell
python tools/efficiency_bundle.py prepare --runtime-root "$targetRoot" `
  --scope "$scopeFile" --manifest "$targetRoot/.obi/efficiency-scope-$runId.json"
python tools/efficiency_bundle.py snapshot --runtime-root "$targetRoot" `
  --manifest "$targetRoot/.obi/efficiency-scope-$runId.json" --snapshot-dir "$backupDir"
# After the complete deployment, restore only if rollback is needed:
python tools/efficiency_bundle.py restore --runtime-root "$targetRoot" `
  --manifest "$targetRoot/.obi/efficiency-scope-$runId.json" --snapshot-dir "$backupDir"
```

`prepare` records expected candidate hashes from source, without claiming those bytes are already
deployed. Snapshot records actual prior bytes and absence. Restore preflights every target and
backup before mutation; changed/missing candidate files cause a collision error, and candidate-only
files are removed only if their current hash still matches. Ordinary deployment manifests with
relative mappings and `deployed_hashes` are also accepted. Never use Force or remove user backups
to bypass a collision. When restoring the Claude root through a separate scope manifest, also pass
`--ownership-manifest "$targetRoot/.obi/deployment-manifest.json"`. This updates only the restored
scope's ownership hashes and removes candidate-only entries; unrelated manifest entries remain.
It rejects changed mappings/hashes before file mutation. Without this reconciliation a later
deployment correctly sees restored files as ownership collisions. This option accepts Claude's
schema-2 receipt only, not Codex's mixed-root schema-1 receipt.
Restoring multiple roots is sequential; record and verify every root before
calling the bundle restored. The isolated deployment test exercises prepare/snapshot, the real
copy helpers, helper execution, and restore while preserving unrelated configuration.

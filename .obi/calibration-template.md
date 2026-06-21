---
# Obi Calibration Settings
# Copy this to ~/.claude/.obi/calibration.md to activate
version: 1.0
last_updated: 2026-01-25

verification:
  default_budget: 1
  max_budget: 3  # Agent hard cap is 3 (non-evolvable)
  budgets_by_type:
    cloud: 2
    database: 2
    powershell: 1
    unknown: 1

safety:
  auto_inject_sources: true
  log_corrections: true
  log_grounding_sources: true
  generate_summaries: true
  propose_evolutions: true
  autonomous_learning: true
  dirty_session_nag: true
  capability_claim: true

detectors:
  capability_claim:
    enabled: true
    review_after_sessions: 20
    review_after_firings: 15
    firings_total: 0
  codex_blindspot:
    enabled: true
    threshold: 3

intervals:
  compact_nag: 20          # Tool calls between [Context] nags (WI-3)

patterns:
  source_injection_threshold: 0.7
  max_injected_sources: 3

performance:
  total_sessions: 0
  corrections_received: 0
  successful_completions: 0
  hallucination_catches: 0

quality_thresholds:
  file_size_yellow: 400  # Soft limit (Rule #3 "consider splitting")
  file_size_red: 600     # Hard limit (Rule #3 "never exceed")
  ignore_globs:          # Paths to skip for size alerts
    - '**/vendor/**'
    - '**/*.generated.*'
    - '**/node_modules/**'
---

# Obi Calibration Documentation

## Verification Budgets

The verification budget controls how many "solve -> verify" cycles Obi attempts
before requiring human input. Higher budgets for complex tasks, lower for simple ones.

| Task Type | Default Budget | Rationale |
|-----------|----------------|-----------|
| cloud | 2 | APIs with many edge cases |
| database | 2 | Complex query/transaction logic |
| powershell | 1 | Reliable linting and testing |
| unknown | 1 | Conservative default |

## Safety Settings

- **auto_inject_sources**: When true, SessionStart injects relevant grounding sources based on task type
- **log_corrections**: When true, user corrections are logged for later analysis
- **log_grounding_sources**: When true, cited sources are logged to grounding-log.jsonl for coverage analysis
- **generate_summaries**: When true, Stop hook creates session summary markdown
- **propose_evolutions**: When true, learning loop generates pending proposals
- **autonomous_learning**: When true, Stop hook detects learnings and writes them to pending-learnings.json
- **dirty_session_nag**: When true, Stop hook emits a `[Git]` advisory if the working tree has uncommitted or untracked changes (WI-1a)
- **capability_claim**: When true, Stop hook emits a `[Deflection]` advisory if Claude denied a capability (no access, no permission, can't do X) in the last 2 assistant turns without any tool_use in the same turn to back up the claim (WI-6). Default on. See `detectors.capability_claim` for the per-detector review gate.

## Intervals

The `intervals:` block holds numeric thresholds that aren't safety booleans and aren't detector review clocks. Read via `get_interval(name, default)`.

- **compact_nag**: Tool calls the PostToolUse hook tolerates between `/compact` runs before emitting a `[Context]` nag (WI-3). Default 20. The counter resets on `/compact` via the `PreCompact` hook, so the nag naturally stops firing after a compact. Set to 0 to disable the nag entirely.

## Detector Review Gates

The `detectors:` block configures per-detector review clocks (cross-cutting rule 4 of the friction-reduction plan). Each detector accumulates firings independently and emits a `[Review]` advisory when either `review_after_sessions` or `review_after_firings` is reached — at that point the user reads recent firings and decides keep / tune / delete.

- **enabled**: Detector-level toggle (distinct from the `safety.*` flag, which is the hook-wiring toggle).
- **review_after_sessions**: Sessions elapsed (via `performance.total_sessions`) before nag appears.
- **review_after_firings**: Firings accumulated before nag appears.
- **firings_total**: Incremented by the detector on every fire. Reset manually after review.

### codex_blindspot

The `codex_blindspot` detector runs at Phase 10 / `/obi-memory-review` time (not in the Stop hook). It counts confirmed Codex catches per category from `.obi/codex-catches.jsonl` and proposes a pending learning when a category reaches `threshold` confirmed catches.

- **enabled**: Toggle the blindspot threshold check. Default `true`.
- **threshold**: Number of confirmed catches per category before proposing a learning. Default `3`. After a rejection, the category must accrue `threshold` NEW catches past the rejection count before re-proposing.

## Pattern Matching

- **source_injection_threshold**: Confidence threshold (0-1) for matching task to patterns
- **max_injected_sources**: Maximum sources to inject (keeps context manageable)

## Performance Tracking

These metrics are updated automatically by the memory system:
- **total_sessions**: Count of sessions since last reset
- **corrections_received**: Total corrections logged
- **successful_completions**: Sessions ending with RELEASE GATE PASSED
- **hallucination_catches**: Times zero-hallucination policy triggered

## Quality Thresholds

Controls PostToolUse file-size alerts (CLAUDE.md Non-Negotiable Rule #3).

- **file_size_yellow**: Soft limit. Files exceeding this emit `[Quality]` advisory.
- **file_size_red**: Hard limit. Files exceeding this emit `[Quality RED]` enforcement message.
- **ignore_globs**: List of fnmatch patterns to skip. Paths are lowercased and normalized to forward slashes before matching, so `**/vendor/**` works on Windows. Typical values: vendored code, generated files, or third-party modules where you can't control the size.

## Adjusting Calibration

1. Run `/obi-memory-review` to see pending proposals
2. Accept proposals that improve outcomes: `/obi-memory-review accept <id>`
3. Reject proposals that don't fit: `/obi-memory-review reject <id>`
4. One parameter change per acceptance (prevents runaway drift)
5. Prior state preserved in proposal file for rollback

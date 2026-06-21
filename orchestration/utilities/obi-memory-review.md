---
description: Review and manage Obi memory system pending evolutions
allowed-tools: Read, Edit, Glob, Bash
---

# Obi Memory Review Command

Review and manage pending evolutions to Obi's calibration settings. The memory system proposes changes based on session analysis - this command lets you accept or reject them.

## Usage

```
/obi-memory-review                  # List all pending proposals + learnings
/obi-memory-review accept <id>      # Accept calibration proposal and apply
/obi-memory-review reject <id>      # Reject calibration proposal with reason
/obi-memory-review approve <#>      # Graduate a pending learning to permanent storage
/obi-memory-review clear-learnings  # Clear pending learnings after review
/obi-memory-review history          # Show applied/rejected history
/obi-memory-review calibration      # Show current calibration values
/obi-memory-review sessions         # Show recent session summaries
```

## Process

### Step 1: Check for Arguments

Parse the command arguments to determine the action:
- No args or "list": List pending proposals AND pending learnings
- "accept <id>": Accept and apply a calibration proposal
- "reject <id>": Reject a calibration proposal
- "approve <#>": Graduate a pending learning to permanent storage
- "clear-learnings": Clear all pending learnings (after review)
- "history": Show evolution history
- "calibration": Show current settings
- "sessions": Show session history

### Step 2: Execute Action

#### Listing Proposals

**Codex blind-spot threshold check (run first):**

Before listing pending learnings, run the Codex blind-spot threshold check to generate any new blind-spot learnings. This detects categories with enough confirmed Codex catches to warrant a process countermeasure:

```bash
python "$OBI_HOME/tools/codex_catch_stats.py" --check-threshold
```

Any new blind-spot learnings appear in the pending learnings list below. The check reads `.obi/codex-catches.jsonl` from the current repo, compares confirmed catch counts per category against the `detectors.codex_blindspot.threshold` calibration value (default 3), and respects suppression (rejected proposals do not regenerate until N new catches accrue past the rejection count).

**Calibration proposals:**

1. Read all files from `~/.claude/.obi/pending/*.md`
2. Display summary table:

```
| ID  | Parameter                        | Current | Proposed | Confidence |
|-----|----------------------------------|---------|----------|------------|
| 001 | verification.budgets_by_type.xyz | 2       | 3        | 0.72       |
```

3. For each proposal, show brief evidence summary

**Pending learnings:**

1. Read `~/.claude/.obi/pending-learnings.json`
2. If `learnings` array is non-empty, display each learning:

```
| #  | Type     | Title                    | Target File                          | Confidence |
|----|----------|--------------------------|--------------------------------------|------------|
| 1  | vendor   | Cloud pattern found      | docs/domain-patterns/...            | 0.5        |
```

3. Assess each learning — the learning detector can produce false positives (low confidence, content is session noise rather than real learnings). Flag suspicious entries.
4. Suggest `/obi-memory-review clear-learnings` if all are noise.

#### Approving Learnings (Graduation)

Promotes a pending learning from `pending-learnings.json` to permanent storage.

1. Read `~/.claude/.obi/pending-learnings.json`
2. Validate the learning number `<#>` exists in the `learnings` array (1-indexed)
3. Display the full learning content to the user
4. Ask the user for the destination:

| Destination | Target | When to Use |
|-------------|--------|-------------|
| `memory` | `MEMORY.md` (auto-memory) | Stable facts, preferences, tool gotchas — always loaded |
| `pattern` | `.obi/patterns/<domain>.md` | Domain-specific knowledge — injected conditionally |
| `docs` | `docs/gotchas.md` in obiwag-agents | Obi-specific gotchas — referenced during development |

5. Based on destination:

**memory**:
   - Read `~/.claude/projects/C--Users-user/memory/MEMORY.md`
   - Identify the best section for the learning (match by topic)
   - Append a concise bullet point to that section
   - Verify MEMORY.md stays under 200 lines — if it would exceed, warn and suggest `pattern` instead

**pattern**:
   - Determine the domain from the learning's `metadata.category` or `type` (e.g., "platform" → `windows.md`, "vendor" → `cloud.md`)
   - Read the target pattern file from `~/.claude/.obi/patterns/`
   - Append the learning content
   - If the pattern file doesn't exist, create it with a header

**docs**:
   - Read `docs/gotchas.md` in the obiwag-agents repo
   - Append a new numbered gotcha entry with Problem/Solution format
   - Follow the existing numbering convention

6. Remove the approved learning from the `learnings` array in `pending-learnings.json`
7. Write back the updated JSON
8. Report success with the destination path

**Safety Rules:**
- One learning per approval — never batch
- User must confirm destination before writing
- If MEMORY.md would exceed 200 lines, block and suggest alternative

#### Clearing Learnings

1. Read `~/.claude/.obi/pending-learnings.json`
2. **Suppress recurring false positives.** Before clearing, for each learning
   that is a detector false positive (not a real learning you simply chose not
   to graduate), record its `(type, category)` so the detector stops
   regenerating it every Stop. Run (from `$env:OBI_HOME`):
   ```
   python -c "import sys; sys.path.insert(0,'hooks'); from core.learning_suppression import suppress_key; suppress_key('<type>','<category>')"
   ```
   where `<type>` is the learning's `type` and `<category>` is
   `metadata.category` (use `''` if absent). Skip this for genuine learnings
   you may want to capture later — only suppress noise.
3. Reset to empty: `{"session_id": null, "timestamp": null, "learnings": []}`
4. Report how many were cleared, and which `(type, category)` keys were suppressed.

To undo a suppression, edit `~/.claude/.obi/suppressed-learnings.json` and
remove the key from the `keys` array.

#### Accepting Proposals (Calibration)

1. Validate proposal file exists in `~/.claude/.obi/pending/`
2. Read current `~/.claude/.obi/calibration.md`
3. Parse the YAML frontmatter
4. Apply the SINGLE parameter change (never batch changes)
5. Update `last_updated` timestamp
6. Write back calibration.md
7. Move proposal to `~/.claude/.obi/memory/evolutions/applied/`
8. Add acceptance note with timestamp
9. Report success

**Safety Rule:** Only ONE parameter change per acceptance. This prevents calibration drift.

#### Rejecting Proposals

1. Ask user for rejection reason
2. Move proposal to `~/.claude/.obi/memory/evolutions/rejected/`
3. Add rejection note with reason and timestamp
4. Report rejection

#### Showing History

1. Read files from `~/.claude/.obi/memory/evolutions/applied/`
2. Read files from `~/.claude/.obi/memory/evolutions/rejected/`
3. Display combined chronological history

#### Showing Calibration

1. Read `~/.claude/.obi/calibration.md`
2. Display current values in readable format
3. Compare to template defaults if different

#### Showing Sessions

1. Read recent files from `~/.claude/.obi/memory/sessions/`
2. Display summary table with date, task type, outcome, corrections

## Example Output

### List Pending

```
## Pending Evolutions

Found 2 pending proposals:

### Proposal 001: Increase Cloud Budget
- Parameter: verification.budgets_by_type.cloud
- Current: 2 → Proposed: 3
- Confidence: 0.72
- Evidence: 5 sessions analyzed, avg 1.6 corrections

### Proposal 002: Enable Source Injection
- Parameter: safety.auto_inject_sources
- Current: false → Proposed: true
- Confidence: 0.85
- Evidence: Consistent source citations in corrections

Commands:
- `/obi-memory-review accept 001`
- `/obi-memory-review reject 002`
```

### Accept (Calibration)

```
## Accepted: Proposal 001

Applied change:
- verification.budgets_by_type.cloud: 2 → 3

Calibration updated. Prior state preserved in:
~/.claude/.obi/memory/evolutions/applied/001_verification_budgets_by_type_cloud.md
```

### Approve (Learning Graduation)

```
## Learning #1: Python path gotcha

Type: gotcha | Confidence: 0.7
Content: Python was not found; run without arguments...

Where should this learning go?
1. memory — MEMORY.md (always loaded, 132/200 lines used)
2. pattern — .obi/patterns/windows.md (loaded when Windows work detected)
3. docs — docs/gotchas.md (Obi development reference)

> User selects: memory

✓ Graduated to MEMORY.md → "Windows + Bash" section
✓ Removed from pending-learnings.json (0 remaining)
```

## Safety Rules

1. **One Change Per Acceptance**: Never batch multiple parameter changes
2. **Prior State Preservation**: Original values saved in applied proposal file
3. **Human Gate Required**: All changes require explicit acceptance
4. **Rejection Tracking**: Rejections logged to prevent re-proposing same change
5. **No Auto-Evolution**: Proposals are suggested, never auto-applied

## Calibration Parameters

### verification.default_budget
Default verification passes before human gate. Range: 1-5.

### verification.budgets_by_type.<type>
Per-task-type verification budgets. Types: cloud, database, powershell, unknown (typically the matched grounding pattern's topic).

### safety.auto_inject_sources
Enable/disable automatic grounding source injection at session start.

### safety.log_corrections
Enable/disable correction signal logging.

### safety.generate_summaries
Enable/disable session summary generation on stop.

### safety.propose_evolutions
Enable/disable evolution proposal generation.

### patterns.source_injection_threshold
Confidence threshold for pattern matching (0.0-1.0).

### patterns.max_injected_sources
Maximum sources to inject per session (1-10).

### correction_retrieval.enabled
Enable/disable RAG-style correction injection at session start.

### correction_retrieval.max_corrections
Maximum corrections to inject per session (1-10).

### correction_retrieval.min_relevance_score
Minimum relevance score for correction matching (0.0-1.0).

### correction_retrieval.days_of_history
Number of days of correction history to search (1-90).

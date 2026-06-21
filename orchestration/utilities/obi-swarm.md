---
description: Parallel issue resolution. Fetches 'ready' issues, classifies risk, spawns isolated workers, merges results.
allowed-tools: Read, Glob, Grep, Bash, Write, Task
---

# Obi Swarm - Parallel Issue Resolution

Coordinator command that fetches GitHub issues tagged `ready`, classifies risk, spawns parallel Task subagents in isolated git worktrees, merges clean results, and closes issues.

## Procedure

### Phase 1: Fetch Ready Issues

```bash
gh -R user/obiwag-agents issue list --label ready --json number,title,labels
```

Parse the output to get issue IDs, titles, descriptions, and labels. If no issues have the `ready` label, report "No ready issues found" and stop.

### Phase 2: Classify Issues

For each issue, read the full description:

```bash
gh -R user/obiwag-agents issue view <N>
```

Classify each issue using the swarm classifier:

```bash
python C:/src/obiwag-agents/tools/swarm_classifier.py --title "<title>" --body "<body>" --labels "<labels>" --json
```

Group issues by risk tier:
- **config-only**: docs, yaml, env, comments, typos
- **single-file**: one code file change
- **multi-file**: cross-module refactors

Display classification summary before proceeding.

### Phase 3: Worktree Setup

For each issue, create an isolated git worktree:

```bash
git -C C:/src/obiwag-agents worktree add C:/src/.obi-swarm/issue-<N> -b fix/issue-<N>
```

Verify all worktrees were created successfully.

### Phase 4: Dispatch Workers

Spawn Task subagents (maximum 3 concurrent per wave) using `subagent_type: general-purpose`.

For each worker, provide:
- Issue number and full description
- Worktree path: `C:/src/.obi-swarm/issue-<N>`
- Risk tier from classification
- Instructions to follow the obi-swarm-worker agent protocol

**Worker prompt template (4-part structure):**

```
You are an obi-swarm-worker. Resolve GitHub issue #<N>.

## Scope
- Worktree: <worktree_path>
- All file operations MUST target paths within this worktree only
- Risk tier: <tier>

## Objective
- Issue: <title>
- Description: <full description>
- Read the current state of target files before making changes — do not trust
  descriptions in this prompt over what the code actually says

## Constraints
- Do NOT modify files outside the worktree
- Do NOT touch protected paths (settings.json, settings.local.json, hooks/*.cmd, hooks/*.py, hooks/core/*.py, .claude/, .obi/, tools/deploy.ps1, tools/config-guardian.ps1, users/)
- Do NOT push commits — the coordinator handles merging
- Do NOT add improvements beyond what the issue describes
- Do NOT modify these unrelated files: <list any files other workers are touching>

## Reporting
End with the structured Worker Report:
- Status: COMPLETE | FAILED | SKIPPED
- Files modified, diff stats, test results, commit SHA
- If FAILED/SKIPPED: explain why
```

Process issues in waves of 3 (or fewer for the last wave). Wait for each wave to complete before starting the next.

### Phase 5: Collect Results

Parse each worker's output for the structured report. Track:
- COMPLETE: Ready to merge
- FAILED: Needs investigation
- SKIPPED: Requires manual review

Display collection summary.

### Phase 6: Merge

Run the merge helper on all COMPLETE branches:

```bash
python C:/src/obiwag-agents/tools/swarm_merge.py \
    --repo C:/src/obiwag-agents \
    --branches <comma-separated-branches> \
    --risk-map '<json>' \
    --base master
```

Review merge results:
- **merged**: Successfully integrated
- **conflicted**: Need manual resolution
- **skipped**: File overlap with higher-priority merge

### Phase 7: Post-Merge (Requires the user Approval)

**STOP HERE and request the user's approval before proceeding.**

Present the full merge report and ask for confirmation to:
1. Push merged changes to origin
2. Close resolved issues with a note

If approved:

```bash
git -C C:/src/obiwag-agents push origin master
```

For each successfully merged issue:

```bash
gh -R user/obiwag-agents issue comment <N> --body "Resolved via obi-swarm batch processing. Commit: <SHA>"
gh -R user/obiwag-agents issue close <N>
```

### Phase 8: Cleanup

Remove all worktrees:

```bash
git -C C:/src/obiwag-agents worktree remove C:/src/.obi-swarm/issue-<N> --force
```

Delete the swarm directory:

```bash
rm -rf C:/src/.obi-swarm
```

Prune stale worktree references:

```bash
git -C C:/src/obiwag-agents worktree prune
```

Delete feature branches that were merged:

```bash
git -C C:/src/obiwag-agents branch -d fix/issue-<N>
```

### Phase 9: Report

Write a batch summary:

```
## Obi Swarm Batch Report

### Issues Processed: <total>

| Issue | Title | Tier | Worker | Merge | Status |
|-------|-------|------|--------|-------|--------|
| #N    | ...   | ...  | COMPLETE/FAILED/SKIPPED | merged/conflicted/skipped | Closed/Open |

### Summary
- Resolved: <count>
- Failed: <count>
- Skipped: <count>
- Conflicted: <count>

### Issues Requiring Manual Attention
- #N: <reason>
```

## Guardrails

- **Max 3 concurrent workers** per wave
- **the user approval required** before pushing and closing issues
- Workers cannot modify protected paths (settings, hooks, deploy scripts)
- Workers use native tools only (no Bash file I/O, no heredocs)
- Each worker is isolated in its own git worktree
- Merge order: config-only first, then single-file, then multi-file
- Conflicting merges are skipped, not forced

## Operational Notes

- **Swarm worker email bug** — workers commit as `swarm@obiwag.local`. After merge, rewrite the authored emails with `git filter-branch --env-filter` to `user@example.com` so the history attributes correctly.
- **Overlapping file edits cause merge skips** — when two workers touch the same file, the merge phase skips the second worker to avoid corruption. Resolve manually with `git merge` + conflict resolution in the main worktree.

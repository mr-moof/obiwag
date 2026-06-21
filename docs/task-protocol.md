# Task Protocol Specification

Filesystem-based task coordination for multi-agent workflows. Tasks are files, git is the audit trail, and agents coordinate through commit-based claims.

---

## Design Principles

- Tasks are files. No database, no server, no message queue.
- Git is the audit trail. Every state change is a commit.
- Claims are validated at commit time, not move time.
- Agents are selfish readers, polite writers. They claim work by committing, not by locking.
- The `can_use_tool` callback enforces a layered policy chain, not a single flat check.

---

## Task Lifecycle

```
tasks/
  pending/          Unclaimed tasks
    task-001.md
  claimed/          In-progress
    task-002.md     Agent ID and timestamp in YAML header
  done/             Completed
    task-003.md     Result section filled in
  failed/           Failed
    task-004.md     Error section filled in
  blocked/          Waiting on dependency or human input
    task-005.md
```

---

## Task File Format

```yaml
---
id: task-001
created: 2026-03-12T14:22:00Z
created_by: coordinator
claimed_by:
claimed_at:
priority: normal              # low | normal | high
depends_on: []                # list of task IDs that must be in done/ first
phase: implement              # which Obi phase this maps to
scope:                        # files or directories this task may touch
  - src/utils/
  - tests/utils/
parent_task:                  # if agent-created, the task ID that spawned it
---

## Objective

<task description>

## Context

<background information>

## Acceptance Criteria

- <criterion 1>
- <criterion 2>

## Result

<!-- Filled by agent on completion -->

## Error

<!-- Filled by agent on failure -->
```

---

## Claim Protocol -- Commit-Based with Rebase

1. `git pull --rebase origin main`
2. Verify task file still exists in `tasks/pending/`
3. `git mv tasks/pending/task-NNN.md tasks/claimed/task-NNN.md`
4. Write agent ID and timestamp into `claimed_by` / `claimed_at` fields
5. `git add tasks/claimed/task-NNN.md`
6. `git commit -m "claim: task-NNN by <agent-id>"`
7. `git pull --rebase origin main`
8. If rebase conflict: abort, reset, pick a different task
9. `git push origin main`

The worst case is a failed push that triggers a re-rebase, which detects the conflict cleanly. No work is lost because the agent hasn't started execution yet.

---

## Completion Protocol

1. Agent appends result summary to the `## Result` section
2. `git mv tasks/claimed/task-NNN.md tasks/done/task-NNN.md`
3. `git add -A`
4. `git commit -m "done: task-NNN -- <summary>"`
5. `git pull --rebase origin main && git push origin main`

---

## Failure Protocol

1. Agent appends error details to the `## Error` section
2. `git mv tasks/claimed/task-NNN.md tasks/failed/task-NNN.md`
3. `git add -A`
4. `git commit -m "fail: task-NNN -- <one-line error summary>"`
5. `git pull --rebase origin main && git push origin main`

---

## Conflict Prevention via Scope Enforcement

- The `scope` field declares which files/directories the task may touch.
- Two tasks with overlapping scopes must not be in `claimed/` simultaneously.
- Agents check this before claiming: if any task in `claimed/` has overlapping scope, skip it.
- The `can_use_tool` SDK callback enforces scope at runtime.

---

## Agent-Created Subtasks

Agents may create new tasks during execution. Rules:

1. `created_by` must be the creating agent's ID.
2. `parent_task` must be the ID of the task the agent is currently executing.
3. `scope` must be a subset of, or non-overlapping with, the parent task's scope.
4. New tasks are created in `tasks/pending/` and committed normally.
5. The creating agent must not claim its own subtasks in the same execution cycle.

---

## Staleness Recovery

If a task has been in `claimed/` for more than N minutes (configurable, default 30), any agent may reclaim it:

1. Move the task back to `pending/` with a note appended.
2. Clear the `claimed_by` and `claimed_at` fields.
3. Commit and push.

---

## can_use_tool Policy Chain

Three layers evaluated in order. The first deny stops evaluation.

**Layer 1: Safety Rails** (always active)
- Block Write/Edit to files outside the working directory
- Block Bash commands matching deny patterns (rm -rf, curl, wget, etc.)
- Block any tool call that would modify `.git/` internals

**Layer 2: Scope Enforcement** (active when agent has a claimed task)
- Read task's scope field
- Block Write/Edit to any path not under a scope prefix
- Block Bash commands that write to paths outside scope
- Log a warning (don't block) for ambiguous Bash commands

**Layer 3: Logging** (always active, runs after layers 1-2)
- Log timestamp, tool name, first 100 chars of input
- Log allow/deny decision and which layer made it
- Write to `output/tool_log.jsonl`

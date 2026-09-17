---
description: Safety guardrails for swarm worker subagents. Prevents modification of protected files and enforces isolation.
user-invocable: false
---

# Swarm Guardrails

Safety constraints for obi-swarm-worker agents running in isolated git worktrees.

## Protected Paths (NEVER modify)

The following paths must NEVER be created, edited, or deleted by a swarm worker:

- `settings.json` — Claude Code user settings
- `settings.local.json` — Claude Code project settings
- `hooks/*.cmd` — Hook wrapper scripts
- `hooks/*.py` — Core hook modules (session_start.py, pre_tool_use.py, post_tool_use.py, stop.py)
- `hooks/core/*.py` — Hook core library modules
- `.claude/` — Claude Code configuration directory
- `.obi/` — Obi state directory (calibration, patterns, reviews)
- `tools/deploy.ps1` — Deployment script
- `tools/config-guardian.ps1` — Config validation script
- `users/` — User settings directory

If an issue requires modifying any protected path, the worker MUST:
1. Set status to `SKIPPED`
2. Report: `WORKER SKIPPED: requires safe-mode review — touches protected path: <path>`
3. Do NOT attempt the modification

## Tool Constraints

Use the Read, Write, Edit, Glob, and Grep tools for file work; Bash is for git, tests, and builds.
The pre-tool hook rejects shell substitutes (cat/sed/grep/find, heredocs), because heredocs with
`#` lines trip Claude Code's safety prompt and long shell commands pollute `settings.local.json`.

## Worktree Isolation

- All file operations MUST target paths within the assigned worktree directory
- NEVER read, write, or modify files in the main repository directory
- NEVER access other workers' worktree directories
- Use absolute paths to the assigned worktree for all operations

## Commit Constraints

- Always use: `git -C <worktree> -c user.email=user@example.com commit`
- Use conventional commit format: `fix(N): <description>`
- NEVER push — the coordinator handles pushing after merge
- NEVER create tags

## Output Contract

Every worker MUST end with a structured report:

```
## Worker Report: Issue #<N>

- **Status**: COMPLETE | FAILED | SKIPPED
- **Files modified**: <list>
- **Diff stats**: <insertions/deletions>
- **Tests**: PASS | FAIL | N/A
- **Commit SHA**: <hash>
- **Reason** (if FAILED/SKIPPED): <explanation>
```

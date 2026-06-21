# Obi Wag — AI Agent Orchestrator (source repo)

Source for the global Claude Code agent orchestrator. The deployed copy at `~/.claude/CLAUDE.md` is the runtime contract loaded every session, deployed by `tools/deploy.ps1`.

## Repo-specific invariants

- **Self-modifying repo**: edits here change the rules that govern the next session. Treat policy/workflow/hook changes as user-approval-gated even if a comparable change in another repo would proceed autonomously.
- **Deploy path**: `tools/deploy.ps1` deploys to `~/.claude/`. Deployed PowerShell tools live under `$OBI_HOME` (defaults to `C:\src\obi-tools`, set by deploy); reference them as `$env:OBI_HOME\tools\...` rather than running scripts from `~/.claude/`.
- **Validation**: `$env:OBI_HOME\tools\config-guardian.ps1` (`-CheckOnly` for CI, `-Verbose` for details).
- **Version bumps**: `tools/bump-version.ps1` is the single command (updates `version.yaml` + the markdown banners + prepends a CHANGELOG stub); `tools/run-grep-gates.ps1 -VersionDrift` is the drift gate that catches any stale banner left behind.
- **Policies in `policies/`**, deployed to `docs/policies/`. Policy edits + memory-schema changes are approval-gated.
- **Auto-start**: `[CODING_SESSION_START]` triggers `/obi`. Do not break this signal.
- **Tool-result drops**: a `[Tool result missing due to internal error]`/empty result still leaves you in control (a *returned* result requiring immediate reaction, not an idle hang) — verify actual state with one cheap read, retry once (bounded or backgrounded) only if it didn't land, never idle or re-run blindly. Slow commands must `run_in_background` or carry a `timeout` (PreToolUse duration guard). Full contract: `docs/operation-timeouts.md`.
- **Operator stall reports**: a stall/stuck/hung report from the user gets a 3-line status report (what was dispatched, what state you verified, next action) and immediate action, never a classification debate — see `docs/operation-timeouts.md`.

## Authoring locations

| Asset | Source | Deployed to |
|---|---|---|
| Global CLAUDE.md | `claude.md` | `~/.claude/CLAUDE.md` |
| Commands | `commands/` | `~/.claude/commands/` |
| Skills | `skills/` | `~/.claude/skills/` |
| Agents | `agents/` | `~/.claude/agents/` |
| Policies | `policies/` | `docs/policies/` |
| PowerShell tools | `tools/` | `$env:OBI_HOME\tools\` |

Same 10 phases as global rules. Express Lane (skip 6 + 8) for ≤25-line changes. Commit direct to master with conventional commits; branches+PRs only when parallelizing or risky enough to want review. See `docs/workflow/phases.md`, `docs/development-standards.md`, `docs/askuserquestion-format.md`, `docs/operation-timeouts.md`, `docs/compaction-checklist.md`.

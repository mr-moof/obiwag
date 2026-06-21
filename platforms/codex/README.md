# Codex Integration

This directory is the Codex platform target for Obi Wag.

## Source Files

| File | Purpose |
|---|---|
| `AGENTS.md` | Codex runtime contract copied into the repo root during deploy |
| `hooks.json` | Codex hook configuration copied to `.codex/hooks.json` |
| `validate-codex.py` | Structural validator for this platform target |

## Deployment

Use the main deploy script:

```powershell
.\tools\deploy.ps1 -CodexOnly
```

Deploy writes:

- `platforms/codex/AGENTS.md` -> `AGENTS.md`
- `platforms/codex/hooks.json` -> `.codex/hooks.json`
- `hooks/` -> `$env:OBI_HOME\hooks\`
- `skills/` -> `~/.codex/skills\`

The hook wrapper sets `OBI_PLATFORM=codex`, so runtime state is written under
`~/.codex/.obi` unless `OBI_ROOT` is set.
The hook commands also set a literal `OBI_HOME=C:\src\obi-tools`
fallback before invoking the wrapper, so a fresh deploy works before a newly
set user environment variable is inherited by a restarted Codex process.

## Command Model

Codex does not load Claude Code slash-command files, and bare messages that
start with `/` are intercepted by Codex's own command palette. Obi commands are
handled as slashless prompt aliases by `AGENTS.md`: when the user asks for
`review` or "run /review", Codex reads `phases/04-review/command.md` and
follows that contract inline.

Use these forms in Codex:

```text
obi-auto fix the failing tests
obi-auto-max implement the plan in C:\path\plan.md
review
release
```

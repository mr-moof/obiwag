# Codex Integration

This directory is the Codex platform target for Obi Wag.

## Source Files

| File | Purpose |
|---|---|
| `AGENTS.md` | Codex runtime contract copied into the repo root during deploy |
| `obi.config.toml` | Optional `obi` profile for the Terra/medium coordinator baseline |
| `hooks.json` | Codex hook configuration copied to `.codex/hooks.json` |
| `validate-codex.py` | Structural validator for this platform target |

## Deployment

Use the main deploy script:

```powershell
.\tools\deploy.ps1 -CodexOnly
```

Deploy writes:

- `platforms/codex/AGENTS.md` -> `AGENTS.md`
- `platforms/codex/obi.config.toml` -> `~/.codex/obi.config.toml`
- `platforms/codex/hooks.json` -> `.codex/hooks.json`
- `hooks/` -> `$env:OBI_HOME\hooks\`
- `skills/` -> `~/.codex/skills\`

The hook wrapper sets `OBI_PLATFORM=codex`, so runtime state is written under
`~/.codex/.obi` unless `OBI_ROOT` is set.
The hook commands also set a user-local `%USERPROFILE%\.obi-tools`
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

Start the CLI with `codex --profile obi` to use Terra/medium for the coordinator and routine
phases. Discovery and Author override their delegated phase threads to Sol/xhigh. An alias cannot
change the primary model of a Codex session that is already running, so a session started without
the profile keeps its existing model and effort.

## Windows launcher error 1312

If a Codex shell call fails before the child command starts with Windows error 1312, retry once
through an alternate invocation path. For repository PowerShell scripts, prefer the explicit
Windows PowerShell executable:

```powershell
& "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -File .\tools\script.ps1
```

This is a host pre-execution failure, not a provider or Obi phase attempt. If the alternate launch
also fails, surface the host blocker rather than consuming another phase strike.

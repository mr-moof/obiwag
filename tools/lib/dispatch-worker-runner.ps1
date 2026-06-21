<#
.SYNOPSIS
    Inner runner for a single headless dispatch worker (OPT-23). Launched by dispatch-worker.ps1
    via Start-Process so the parent gets a clean PID for a SCOPED process-tree kill on timeout.

.DESCRIPTION
    Runs `claude` non-interactively with OBI_WORKER=1 set (so the worker's own hooks no-op). The
    large strings (persona system prompt, task) are read from FILES here rather than passed as
    command-line args, so the launch command stays short and quoting-safe. stdout (the JSON result)
    and stderr (logs/warnings) are written to SEPARATE files so log text never corrupts the JSON.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BodyFile,   # persona body -> --append-system-prompt (system)
    [Parameter(Mandatory)][string]$TaskFile,   # task -> -p (user)
    [Parameter(Mandatory)][string]$OutFile,    # stdout (claude JSON)
    [Parameter(Mandatory)][string]$ErrFile,    # stderr (logs/warnings)
    [Parameter(Mandatory)][string]$LibPath,    # dispatch-worker-lib.ps1 (for Build-ClaudeArgs)
    [string]$Tools,
    [string]$Model,
    [int]$MaxTurns = 80
)

$env:OBI_WORKER = '1'
. $LibPath

$body = Get-Content -LiteralPath $BodyFile -Raw -Encoding UTF8
$task = Get-Content -LiteralPath $TaskFile -Raw -Encoding UTF8

# Persona body is the system prompt (injected ONCE here, not in -p); task is the -p prompt.
$claudeArgs = Build-ClaudeArgs -Prompt $task -Body $body -Tools $Tools -Model $Model -MaxTurns $MaxTurns

& claude @claudeArgs 1> $OutFile 2> $ErrFile
exit $LASTEXITCODE

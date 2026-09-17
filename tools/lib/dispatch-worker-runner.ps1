<#
.SYNOPSIS
    Inner runner for a single headless dispatch worker (OPT-23 / issue #200 SS1). Launched by
    dispatch-worker.ps1 via Start-Process so the supervisor gets a clean PID for a SCOPED
    process-tree kill on timeout, and so the child's stdout/stderr can be redirected to FILES by
    the parent (they grow incrementally -> observable liveness, never a block-buffered pipe).

.DESCRIPTION
    Runs `claude` non-interactively with OBI_WORKER=1 set (so the worker's own hooks no-op). The
    large strings (persona system prompt, task) are read from FILES here rather than passed as
    command-line args, so the launch command stays short and quoting-safe. Streaming JSON is used
    (Build-ClaudeArgs -StreamJson) so the supervisor sees output grow during the run.

    Exit code is written to -ExitFile: Start-Process -PassThru does NOT reliably surface a child's
    ExitCode on PS 5.1 (issue #199), so the file is the source of truth.

    stdout/stderr are NOT redirected here -- the parent Start-Process redirects them to files.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BodyFile,   # persona body -> --append-system-prompt (system)
    [Parameter(Mandatory)][string]$TaskFile,   # task -> -p (user)
    [Parameter(Mandatory)][string]$LibPath,    # dispatch-worker-lib.ps1 (for Build-ClaudeArgs)
    [string]$ExitFile,                          # claude's exit code is written here (reliable)
    [string]$Tools,
    [string]$Model,
    [string]$Effort,
    [string]$SessionId,                         # assigned session id (--session-id)
    [string]$ResumeSessionId                    # resume an existing session (--resume)
)

$env:OBI_WORKER = '1'
$ErrorActionPreference = 'Continue'
. $LibPath

$body = Get-Content -LiteralPath $BodyFile -Raw -Encoding UTF8
$task = Get-Content -LiteralPath $TaskFile -Raw -Encoding UTF8
if ($ResumeSessionId) {
    # The resumed session retains its original persona and first-attempt budget. Carry only the
    # freshly generated budget block in the new user instruction so attempt 2 gets a new clock.
    $budgetMarker = 'DELEGATED PHASE BUDGET (supervisor-enforced):'
    $budgetIndex = $body.IndexOf($budgetMarker, [System.StringComparison]::Ordinal)
    if ($budgetIndex -ge 0) {
        $task = $task.TrimEnd() + "`n`n" + $body.Substring($budgetIndex).Trim() + "`n"
    }
}

# Persona body is the system prompt (attempt 1 only; a resumed session already carries it).
$claudeArgs = Build-ClaudeArgs -Prompt $task -Body $body -Tools $Tools -Model $Model -Effort $Effort `
    -StreamJson -SessionId $SessionId -ResumeSessionId $ResumeSessionId

& claude @claudeArgs
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 0 }
if ($ExitFile) { Set-Content -LiteralPath $ExitFile -Value ([string]$code) -Encoding ascii }
exit $code

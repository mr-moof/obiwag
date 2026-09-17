<#
.SYNOPSIS
    Run or supervise a provider-neutral advisory peer review.

.DESCRIPTION
    `preflight` classifies the scope without creating a capsule. `run` is
    foreground and capped at 240 seconds. `start` accepts a durable
    long review and returns a RunId; the primary orchestrator must subsequently
    use status/wait/result or cancel. All operations print one bounded JSON
    object. Provider failures remain durable advisory outcomes.

.PARAMETER Platform
    Known primary runtime for `run`/`start` (`codex`, `claude`, or `claude-code`). With
    `-Provider auto`, the harness selects the opposite provider without depending on ambient hook
    state. Omit only for compatibility with callers that reliably set OBI_PLATFORM.

.PARAMETER RequestFile
    Path to the schema-version 1 JSON semantic request defined in `policies/peer-review.md`.
    Required for `preflight`, `run`, and `start`; this is not a free-form Markdown prompt.

.PARAMETER Authorization
    `auto` uses the standing trusted-scope classifier. Use `approved` only after
    explicit user approval for a scope that preflight marked approval-required;
    pass that decision's exact ApprovalScopeSha256 too.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('preflight', 'run', 'start', 'status', 'wait', 'result', 'cancel')]
    [string]$Operation = 'run',

    [ValidateSet('auto', 'codex', 'claude')]
    [string]$Provider = 'auto',

    [ValidateSet('codex', 'claude', 'claude-code')]
    [string]$Platform,

    [ValidateSet('auto', 'approved')]
    [string]$Authorization = 'auto',

    [string]$ApprovalScopeSha256,

    [Parameter(Mandatory)]
    [string]$RepoRoot,

    [string]$RequestFile,
    [int]$TimeoutSec = 0,
    [string]$RunId,
    [ValidateSet('terminal', 'activity')]
    [string]$Until = 'terminal',
    [int]$WaitSec = 120,
    [int]$GraceSec = 10
)

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error 'peer-review: python is not installed or not on PATH.'
    exit 2
}

$launcher = Join-Path $PSScriptRoot 'peer-review.py'
$arguments = @($launcher, $Operation, '--repo-root', $RepoRoot)

if ($Operation -in @('preflight', 'run', 'start')) {
    if (-not $RequestFile) {
        Write-Error "peer-review: RequestFile is required for '$Operation'."
        exit 2
    }
    $arguments += @('--provider', $Provider, '--request-file', $RequestFile)
    if ($Platform) { $arguments += @('--platform', $Platform) }
}

if ($Operation -eq 'preflight') {
    if ($TimeoutSec -eq 0) { $TimeoutSec = 30 }
    if ($TimeoutSec -lt 1 -or $TimeoutSec -gt 120) {
        Write-Error "peer-review: TimeoutSec must be between 1 and 120 for 'preflight'."
        exit 2
    }
    $arguments += @('--timeout-sec', [string]$TimeoutSec)
}

if ($Operation -eq 'run' -or $Operation -eq 'start') {
    if ($TimeoutSec -eq 0) {
        $TimeoutSec = if ($Operation -eq 'start') { 1800 } else { 240 }
    }
    $maximum = if ($Operation -eq 'start') { 3600 } else { 240 }
    if ($TimeoutSec -lt 1 -or $TimeoutSec -gt $maximum) {
        Write-Error "peer-review: TimeoutSec must be between 1 and $maximum for '$Operation'."
        exit 2
    }
    $arguments += @('--timeout-sec', [string]$TimeoutSec, '--authorization', $Authorization)
    if ($ApprovalScopeSha256) {
        $arguments += @('--approval-scope-sha256', $ApprovalScopeSha256)
    }
    if ($RunId) { $arguments += @('--run-id', $RunId) }
} elseif ($Operation -ne 'preflight') {
    if (-not $RunId) {
        Write-Error "peer-review: RunId is required for '$Operation'."
        exit 2
    }
    $arguments += @('--run-id', $RunId)
    if ($Operation -eq 'wait') {
        if ($WaitSec -lt 1 -or $WaitSec -gt 240) {
            Write-Error 'peer-review: WaitSec must be between 1 and 240.'
            exit 2
        }
        $arguments += @('--wait-sec', [string]$WaitSec, '--until', $Until)
    }
    if ($Operation -eq 'cancel') {
        if ($GraceSec -lt 1 -or $GraceSec -gt 30) {
            Write-Error 'peer-review: GraceSec must be between 1 and 30.'
            exit 2
        }
        $arguments += @('--grace-sec', [string]$GraceSec)
    }
}

& $python.Source @arguments
exit $LASTEXITCODE

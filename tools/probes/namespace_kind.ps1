<#
.SYNOPSIS
    Probe: classify a GitHub account as user vs organization.

.DESCRIPTION
    Uses gh api to look up the account via the /users/{name} endpoint, which
    returns the right object for both users and organizations. Returns probe
    result with {kind: "User"|"Organization"|"", id, full_path}.

.PARAMETER Namespace
    The GitHub account login (e.g., "octocat" or "github").

.PARAMETER JsonlPath
    Optional path to append the JSONL result. Defaults to repo's
    .obi/runtime/probes-<UTC>.jsonl.

.EXAMPLE
    .\tools\probes\namespace_kind.ps1 -Namespace octocat
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Namespace,
    [string]$JsonlPath = ''
)

. (Join-Path $PSScriptRoot '_lib.ps1')

$encoded = [uri]::EscapeDataString($Namespace)
$apiResult = Invoke-GhApi -Path "users/$encoded"

$kind = ''
$nsId = $null
$fullPath = ''
$err = ''

if ($apiResult.ok) {
    try {
        $obj = $apiResult.stdout | ConvertFrom-Json -ErrorAction Stop
        # GitHub /users/{name}: { login, id, type: "User"|"Organization" }
        $kind     = if ($obj.PSObject.Properties['type'])  { [string]$obj.type }  else { '' }
        $nsId     = if ($obj.PSObject.Properties['id'])    { $obj.id }            else { $null }
        $fullPath = if ($obj.PSObject.Properties['login']) { [string]$obj.login } else { '' }
    } catch {
        $err = "JSON parse failed: $($_.Exception.Message)"
    }
} else {
    $err = $apiResult.stderr
}

$status = if ($apiResult.ok -and -not $err) { 'ok' } else { $apiResult.status }

$result = New-ProbeResult `
    -Probe 'namespace_kind' `
    -Status $status `
    -InputData @{ namespace = $Namespace } `
    -Data  @{ kind = $kind; id = $nsId; full_path = $fullPath } `
    -ErrorText $err

if ($JsonlPath) {
    Write-ProbeOutput -Result $result -JsonlPath $JsonlPath | Out-Null
} else {
    Write-ProbeOutput -Result $result | Out-Null
}

# Emit JSON to stdout for orchestrator consumption (after the summary line)
$result | ConvertTo-Json -Compress -Depth 6

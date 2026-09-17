<#
.SYNOPSIS
    Probe: check whether a target GitHub repo exists, and its last push time.

.DESCRIPTION
    GitHub has no native push-mirror concept, so this probe verifies that the
    target repo EXISTS (and reports its last push time). Calls
    gh api repos/<owner/repo>: 200 => exists, 404 => does not.
    Returns {mirror_exists, last_sync, target_path}.

.PARAMETER SourceProj
    The source project full path (e.g. "owner/example-project").
    Currently advisory only; not used for the existence check.

.PARAMETER Repo
    The "owner/repo" string of the candidate target repo.

.PARAMETER JsonlPath
    Optional path to append the JSONL result.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$SourceProj,
    [Parameter(Mandatory)] [string]$Repo,
    [string]$JsonlPath = ''
)

. (Join-Path $PSScriptRoot '_lib.ps1')

$apiResult = Invoke-GhApi -Path "repos/$Repo"

$mirrorExists = $false
$lastSync = ''
$targetPath = $Repo
$err = ''

if ($apiResult.ok) {
    $mirrorExists = $true
    try {
        $obj = $apiResult.stdout | ConvertFrom-Json -ErrorAction Stop
        # GitHub repo: { full_name: "owner/repo", pushed_at: "<iso8601>", ... }
        if ($obj.PSObject.Properties['pushed_at']) {
            $lastSync = [string]$obj.pushed_at
        }
        if ($obj.PSObject.Properties['full_name'] -and $obj.full_name) {
            $targetPath = [string]$obj.full_name
        }
    } catch {
        $err = "JSON parse failed: $($_.Exception.Message)"
    }
} else {
    $err = $apiResult.stderr
}

$status = if ($apiResult.ok -and -not $err) {
    'ok'
} elseif ($apiResult.status -eq 'not_found') {
    # not_found is an OK probe outcome ("mirror does not exist") - preserve
    # the structured status so the orchestrator can branch on it.
    'not_found'
} else {
    $apiResult.status
}

$result = New-ProbeResult `
    -Probe 'mirror_existence' `
    -Status $status `
    -InputData @{ source_proj = $SourceProj; repo = $Repo } `
    -Data  @{
        mirror_exists = $mirrorExists
        last_sync     = $lastSync
        target_path   = $targetPath
    } `
    -ErrorText $err

if ($JsonlPath) {
    Write-ProbeOutput -Result $result -JsonlPath $JsonlPath | Out-Null
} else {
    Write-ProbeOutput -Result $result | Out-Null
}

$result | ConvertTo-Json -Compress -Depth 6

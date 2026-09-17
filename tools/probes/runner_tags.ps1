<#
.SYNOPSIS
    Probe: enumerate self-hosted runner labels available to a GitHub repo.

.DESCRIPTION
    Calls gh api repos/<owner/repo>/actions/runners and returns
    {available_tags: [...], shared_runners: <bool>}.
    Required runner labels must match the repository workflow configuration.

    available_tags is the sorted unique set of all self-hosted runner label
    names. shared_runners is true when no self-hosted runners are registered
    (total_count == 0), meaning the repo relies on GitHub-hosted runners,
    which are always available.

.PARAMETER Repo
    The "owner/repo" string (e.g. "octocat/Hello-World").

.PARAMETER JsonlPath
    Optional path to append the JSONL result.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Repo,
    [string]$JsonlPath = ''
)

. (Join-Path $PSScriptRoot '_lib.ps1')

$apiResult = Invoke-GhApi -Path "repos/$Repo/actions/runners"

$tags = @()
$sharedRunners = $false
$err = ''

if ($apiResult.ok) {
    try {
        $obj = $apiResult.stdout | ConvertFrom-Json -ErrorAction Stop
        # GitHub response: { total_count, runners: [ { labels: [ { name }, ... ] }, ... ] }
        # No self-hosted runners registered => rely on GitHub-hosted runners.
        $totalCount = if ($obj.PSObject.Properties['total_count']) { [int]$obj.total_count } else { 0 }
        $sharedRunners = ($totalCount -eq 0)

        $allTags = @()
        if ($obj.PSObject.Properties['runners'] -and $null -ne $obj.runners) {
            foreach ($r in @($obj.runners)) {
                if ($r.PSObject.Properties['labels'] -and $r.labels) {
                    foreach ($label in @($r.labels)) {
                        if ($label.PSObject.Properties['name'] -and $label.name) {
                            $allTags += [string]$label.name
                        }
                    }
                }
            }
        }
        $tags = @($allTags | Sort-Object -Unique)
    } catch {
        $err = "JSON parse failed: $($_.Exception.Message)"
    }
} else {
    $err = $apiResult.stderr
}

$status = if ($apiResult.ok -and -not $err) { 'ok' } else { $apiResult.status }

$result = New-ProbeResult `
    -Probe 'runner_tags' `
    -Status $status `
    -InputData @{ repo = $Repo } `
    -Data  @{ available_tags = $tags; shared_runners = $sharedRunners } `
    -ErrorText $err

if ($JsonlPath) {
    Write-ProbeOutput -Result $result -JsonlPath $JsonlPath | Out-Null
} else {
    Write-ProbeOutput -Result $result | Out-Null
}

$result | ConvertTo-Json -Compress -Depth 6

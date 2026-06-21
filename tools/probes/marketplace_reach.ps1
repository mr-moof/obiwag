<#
.SYNOPSIS
    Probe: anonymous HTTP probe of a Claude Code marketplace URL.

.DESCRIPTION
    Returns {reachable, status_code, latency_ms} for a marketplace endpoint
    (typically the published marketplace/static-site JSON manifest URL). Used
    in /obi-auto-max Phase 0 to verify marketplace assumptions before declaring reach.

.PARAMETER MarketplaceUrl
    Full URL to the marketplace manifest (e.g. https://example.com/marketplace.json).

.PARAMETER JsonlPath
    Optional path to append the JSONL result.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$MarketplaceUrl,
    [string]$JsonlPath = ''
)

. (Join-Path $PSScriptRoot '_lib.ps1')

$reachable = $false
$statusCode = 0
$latencyMs = 0
$err = ''

$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $req = [System.Net.HttpWebRequest]::Create($MarketplaceUrl)
    $req.Method = 'GET'
    $req.AllowAutoRedirect = $true
    $req.Timeout = 15000
    $req.UserAgent = 'obi-auto-max-probe'
    $resp = $req.GetResponse()
    $sw.Stop()
    $statusCode = [int]$resp.StatusCode
    $reachable = ($statusCode -ge 200 -and $statusCode -lt 400)
    $latencyMs = [int]$sw.ElapsedMilliseconds
    $resp.Close()
} catch [System.Net.WebException] {
    $sw.Stop()
    $latencyMs = [int]$sw.ElapsedMilliseconds
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
    }
    $err = $_.Exception.Message
} catch {
    $sw.Stop()
    $latencyMs = [int]$sw.ElapsedMilliseconds
    $err = $_.Exception.Message
}

$probeStatus = if ($reachable) {
    'ok'
} elseif ($statusCode -eq 401 -or $statusCode -eq 403) {
    'auth_failure'
} elseif ($statusCode -eq 404) {
    'not_found'
} elseif ($statusCode -eq 0) {
    'network_failure'
} else {
    'unknown'
}

$result = New-ProbeResult `
    -Probe 'marketplace_reach' `
    -Status $probeStatus `
    -InputData @{ marketplace_url = $MarketplaceUrl } `
    -Data  @{
        reachable   = $reachable
        status_code = $statusCode
        latency_ms  = $latencyMs
    } `
    -ErrorText $err

if ($JsonlPath) {
    Write-ProbeOutput -Result $result -JsonlPath $JsonlPath | Out-Null
} else {
    Write-ProbeOutput -Result $result | Out-Null
}

$result | ConvertTo-Json -Compress -Depth 6

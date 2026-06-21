<#
.SYNOPSIS
    Probe: GitHub Pages enabled + access level + anonymous reachability.

.DESCRIPTION
    Queries GET /repos/:owner/:repo/pages. On 200 the repo has Pages enabled;
    a 404 (probe status not_found) means Pages is NOT enabled.

    GitHub Pages response (200): { url, status, cname, html_url, source,
    public, https_enforced }. The `public` flag drives access level and
    anonymous reachability.

    Returns {pages_enabled, access_level, anonymous_reachable, content_redirect}.

    Then performs an optional anonymous HTTP probe of the published Pages URL
    to detect login-page-instead-of-content.

.PARAMETER Repo
    The "owner/repo" string (e.g. "octocat/Hello-World").

.PARAMETER PublicUrl
    Optional explicit Pages URL to probe. If omitted, no anonymous reachability
    check is performed.

.PARAMETER JsonlPath
    Optional path to append the JSONL result.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Repo,
    [string]$PublicUrl = '',
    [string]$JsonlPath = ''
)

. (Join-Path $PSScriptRoot '_lib.ps1')

# GitHub Pages config lives on the dedicated /pages endpoint. A 404 means
# Pages is not enabled for the repo.
$pagesResult = Invoke-GhApi -Path "repos/$Repo/pages"

$pagesEnabled = $false
$accessLevel = ''
$anonymousReachable = $null
$contentRedirect = ''
$err = ''

# pages_enabled tracks the success of the /pages endpoint: 200 => enabled,
# 404 => not enabled. access_level / anonymous_reachable derive from the
# `public` flag in the response.
$pagesEnabled = [bool]$pagesResult.ok
if ($pagesResult.ok) {
    try {
        $pagesObj = $pagesResult.stdout | ConvertFrom-Json -ErrorAction Stop
        $isPublic = if ($pagesObj.PSObject.Properties['public']) { [bool]$pagesObj.public } else { $false }
        $accessLevel = if ($isPublic) { 'public' } else { 'private' }
        $anonymousReachable = $isPublic
    } catch {
        if (-not $err) { $err = "Pages JSON parse failed: $($_.Exception.Message)" }
    }
} elseif ($pagesResult.status -ne 'not_found') {
    # 404 is an OK outcome ("Pages not enabled"); any other failure is an error.
    $err = $pagesResult.stderr
}

if ($PublicUrl) {
    try {
        $req = [System.Net.HttpWebRequest]::Create($PublicUrl)
        $req.Method = 'GET'
        $req.AllowAutoRedirect = $true
        $req.Timeout = 10000
        $req.UserAgent = 'obi-auto-max-probe'
        $resp = $req.GetResponse()
        $finalUrl = $resp.ResponseUri.AbsoluteUri
        $anonymousReachable = ($resp.StatusCode -eq 'OK')
        if ($finalUrl -ne $PublicUrl) {
            $contentRedirect = $finalUrl
            # If redirect lands on a login URL, mark not reachable as content
            if ($finalUrl -match '/users/sign_in|/login|/auth/') {
                $anonymousReachable = $false
            }
        }
        $resp.Close()
    } catch {
        $anonymousReachable = $false
        if (-not $err) { $err = $_.Exception.Message }
    }
}

$status = if ($pagesResult.ok -and -not $err) {
    'ok'
} else {
    # On 404 this is 'not_found' ("Pages not enabled"); preserve the
    # structured status so the orchestrator can branch on it.
    $pagesResult.status
}

$result = New-ProbeResult `
    -Probe 'pages_access' `
    -Status $status `
    -InputData @{ repo = $Repo; public_url = $PublicUrl } `
    -Data  @{
        pages_enabled       = $pagesEnabled
        access_level        = $accessLevel
        anonymous_reachable = $anonymousReachable
        content_redirect    = $contentRedirect
    } `
    -ErrorText $err

if ($JsonlPath) {
    Write-ProbeOutput -Result $result -JsonlPath $JsonlPath | Out-Null
} else {
    Write-ProbeOutput -Result $result | Out-Null
}

$result | ConvertTo-Json -Compress -Depth 6

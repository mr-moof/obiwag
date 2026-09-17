<#
.SYNOPSIS
    Append a peer finding or review attempt to the historical JSONL log (OPT-19/21).

.DESCRIPTION
    Logs one provider-attributed finding or attempt to the historical
    .obi/codex-catches.jsonl path in the target repo. Each line is a
    self-contained JSON object. The file is created on first use. BOM-less
    UTF-8, LF line endings.

    "Utterance != catch; acceptance = catch." Only log findings that have been
    triaged and accepted (or explicitly disputed) by the integrator.

.PARAMETER Repo
    Short name of the target repository (e.g. "obiwag-agents").

.PARAMETER Ref
    Issue, MR, or run reference (e.g. "OPT-19", "#192").

.PARAMETER Phase
    Workflow phase where the catch originated.

.PARAMETER Category
    Taxonomy category for the finding.

.PARAMETER Severity
    Impact severity.

.PARAMETER Summary
    One-line description of the accepted finding.

.PARAMETER PeerProvider
    Provider that produced the finding. Defaults to codex for compatibility
    with existing callers and historical entries.

.PARAMETER Disputed
    Set when the finding was tagged [Codex -- disputed].

.PARAMETER DisputeResolution
    Resolution of the dispute. Only meaningful when -Disputed is set.

.PARAMETER Attempt
    Record one completed peer pass, including clean passes with zero findings.

.PARAMETER PassId
    Stable identity of the peer pass.

.PARAMETER DurationMs / .PARAMETER FindingCount / .PARAMETER AcceptedCount
    Attempt timing and outcome counts. AcceptedCount cannot exceed FindingCount.

.PARAMETER RepoRoot
    Root directory of the target repo. Defaults to the git top-level of the
    CURRENT working directory -- the repo whose findings are being logged.

    It deliberately does NOT default to the parent of this script's directory,
    the way config-guardian does. policies/peer-review.md tells every caller to
    invoke this as `& $env:OBI_HOME\tools\log-codex-catch.ps1`, and $OBI_HOME is
    the deployed tools root (C:\src\obi-tools), so that default wrote
    every catch to obi-tools\.obi\codex-catches.jsonl instead of the tracked log
    in the repo being worked on. Found with 10 real catches already misfiled.
    Falls back to the script's parent only when cwd is not inside a git repo.

.PARAMETER DryRun
    Build and validate the line, print it to stdout, but do not append to file.
#>
[CmdletBinding(DefaultParameterSetName='Finding')]
param(
    [Parameter(Mandatory)]
    [string]$Repo,

    [Parameter(Mandatory)]
    [string]$Ref,

    [Parameter(Mandatory)]
    [ValidateSet('review','plan','freeform')]
    [string]$Phase,

    [Parameter(Mandatory, ParameterSetName='Finding')]
    [ValidateSet('invented-api','missed-edge-case','test-gap','security','regression-risk','doc-mismatch','plan-gap','other')]
    [string]$Category,

    [Parameter(Mandatory, ParameterSetName='Finding')]
    [ValidateSet('high','medium','low')]
    [string]$Severity,

    [Parameter(Mandatory, ParameterSetName='Finding')]
    [string]$Summary,

    [ValidateSet('codex','claude')]
    [string]$PeerProvider = 'codex',

    [Parameter(ParameterSetName='Finding')]
    [switch]$Disputed,

    [Parameter(ParameterSetName='Finding')]
    [ValidateSet('codex-right','claude-right','unresolved')]
    [string]$DisputeResolution,

    [Parameter(Mandatory, ParameterSetName='Attempt')]
    [switch]$Attempt,

    [Parameter(Mandatory, ParameterSetName='Attempt')]
    [string]$PassId,

    [Parameter(Mandatory, ParameterSetName='Attempt')]
    [ValidateRange(0, 2147483647)]
    [int]$DurationMs,

    [Parameter(Mandatory, ParameterSetName='Attempt')]
    [ValidateRange(0, 2147483647)]
    [int]$FindingCount,

    [Parameter(Mandatory, ParameterSetName='Attempt')]
    [ValidateRange(0, 2147483647)]
    [int]$AcceptedCount,

    [string]$RepoRoot,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# Resolve the TARGET repo: the one being worked on, not the one this tool ships
# from. See the -RepoRoot help above for why this differs from config-guardian.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    # git writes "fatal: not a git repository" to stderr outside a repo, and
    # under $ErrorActionPreference='Stop' PS 5.1 promotes native stderr to a
    # TERMINATING error -- so the fallback below was unreachable and the script
    # threw instead. Relax the preference across the probe only.
    $gitTop = $null
    $prevEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $gitTop = & git rev-parse --show-toplevel 2>$null
    } catch {
        $gitTop = $null
    } finally {
        $ErrorActionPreference = $prevEap
    }

    if ($LASTEXITCODE -eq 0 -and $gitTop) {
        $RepoRoot = ($gitTop | Select-Object -First 1).Trim() -replace '/', '\'
    } else {
        # Not inside a git repo -- fall back to the script's own parent.
        $RepoRoot = Split-Path -Parent $ScriptDir
    }
}

$obiDir  = Join-Path $RepoRoot '.obi'
$logPath = Join-Path $obiDir 'codex-catches.jsonl'

# Build one backward-compatible row in the existing log.
if ($PSCmdlet.ParameterSetName -eq 'Attempt') {
    if ($AcceptedCount -gt $FindingCount) {
        throw 'AcceptedCount cannot exceed FindingCount.'
    }
    $catch = [ordered]@{
        record_type   = 'attempt'
        ts            = [datetime]::UtcNow.ToString('o')
        repo          = $Repo
        ref           = $Ref
        phase         = $Phase
        peer_provider = $PeerProvider
        pass_id       = $PassId
        duration_ms   = $DurationMs
        finding_count = $FindingCount
        accepted_count = $AcceptedCount
        outcome       = if ($FindingCount -eq 0) { 'zero_findings' } else { 'findings' }
    }
} else {
    $catch = [ordered]@{
        record_type       = 'finding'
        ts                = [datetime]::UtcNow.ToString('o')
        repo              = $Repo
        ref               = $Ref
        phase             = $Phase
        category          = $Category
        severity          = $Severity
        summary           = $Summary
        peer_provider     = $PeerProvider
        disputed          = [bool]$Disputed
        dispute_resolution = if ($DisputeResolution) { $DisputeResolution } else { $null }
    }
}

$line = $catch | ConvertTo-Json -Compress

if ($DryRun) {
    Write-Output $line
    return
}

# Ensure .obi/ exists
if (-not (Test-Path $obiDir)) {
    New-Item -ItemType Directory -Force -Path $obiDir | Out-Null
}

# Append BOM-less UTF-8 with LF line ending
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::AppendAllText($logPath, $line + "`n", $utf8NoBom)

Write-Host $line

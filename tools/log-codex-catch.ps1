<#
.SYNOPSIS
    Append a confirmed Codex catch to the JSONL catch log (OPT-19).

.DESCRIPTION
    Logs a single Codex-attributed finding to .obi/codex-catches.jsonl in the
    target repo. Each line is a self-contained JSON object. The file is created
    on first use. BOM-less UTF-8, LF line endings.

    "Utterance != catch; acceptance = catch." Only log findings that have been
    triaged and accepted (or explicitly disputed) by the integrator.

.PARAMETER Repo
    Short name of the target repository (e.g. "obiwag-agents").

.PARAMETER Ref
    Issue, PR, or run reference (e.g. "OPT-19", "#192").

.PARAMETER Phase
    Workflow phase where the catch originated.

.PARAMETER Category
    Taxonomy category for the finding.

.PARAMETER Severity
    Impact severity.

.PARAMETER Summary
    One-line description of the accepted finding.

.PARAMETER Disputed
    Set when the finding was tagged [Codex -- disputed].

.PARAMETER DisputeResolution
    Resolution of the dispute. Only meaningful when -Disputed is set.

.PARAMETER RepoRoot
    Root directory of the target repo. Defaults to the parent of this script's
    directory (tools/ -> repo root), matching config-guardian/dispatch-watchdog.

.PARAMETER DryRun
    Build and validate the line, print it to stdout, but do not append to file.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Repo,

    [Parameter(Mandatory)]
    [string]$Ref,

    [Parameter(Mandatory)]
    [ValidateSet('review','plan','freeform')]
    [string]$Phase,

    [Parameter(Mandatory)]
    [ValidateSet('invented-api','missed-edge-case','test-gap','security','regression-risk','doc-mismatch','plan-gap','other')]
    [string]$Category,

    [Parameter(Mandatory)]
    [ValidateSet('high','medium','low')]
    [string]$Severity,

    [Parameter(Mandatory)]
    [string]$Summary,

    [switch]$Disputed,

    [ValidateSet('codex-right','claude-right','unresolved')]
    [string]$DisputeResolution,

    [string]$RepoRoot,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# Resolve repo root (same pattern as config-guardian.ps1)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $ScriptDir
}

$obiDir  = Join-Path $RepoRoot '.obi'
$logPath = Join-Path $obiDir 'codex-catches.jsonl'

# Build the catch object
$catch = [ordered]@{
    ts                 = [datetime]::UtcNow.ToString('o')
    repo               = $Repo
    ref                = $Ref
    phase              = $Phase
    category           = $Category
    severity           = $Severity
    summary            = $Summary
    disputed           = [bool]$Disputed
    dispute_resolution = if ($DisputeResolution) { $DisputeResolution } else { $null }
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

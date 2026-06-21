<#
.SYNOPSIS
    Classify a change into an Obi Wag lane (trivial/express/standard/max) from the diff (OPT-18).

.DESCRIPTION
    Lane-first orchestration (OPT-18) classifies the lane at a single well-defined point --
    after Author -- and the orchestrator then walks the lane's phase list from
    phases/phase-table.json. This helper centralizes the MECHANICAL part of that decision so it
    is testable and so orchestration/obi-auto.md does not grow an inline classifier:

      - diff size (added + removed lines in code files), and
      - an ADVISORY "appears comment/whitespace-only" heuristic.

    It returns the recommended lane + that lane's phase list (read from phase-table.json) as JSON.

    The SEMANTIC exclusions stay with the orchestrator (it has the task context the script does
    not): security-sensitive code, public API surface, multi-module blast radius, or an explicit
    "run the full pipeline" request bump the lane upward (trivial -> express -> standard). The
    orchestrator also owns the post-Integrate reclassification rule (Codex OPT-18 #2): if Review
    or Integrate changes functional code or pushes the diff over the express threshold, re-run
    this helper and adopt the higher lane (forcing Phase 6 Re-review).

.PARAMETER Rigor
    'standard' (default) classifies into trivial/express/standard from the diff.
    'max' short-circuits to the max lane (no diff classification needed).

.PARAMETER Base
    Git ref to diff the working tree against. Defaults to HEAD~1 (matches the historical
    express/trivial rule `git diff --stat HEAD~1`).

.PARAMETER RepoRoot
    The git repo whose diff is measured. Defaults to the CURRENT directory (the project the
    orchestrator is working in) — NOT this script's location, which after deploy lives under
    $OBI_HOME/tools, far from the project being classified.

.PARAMETER TablePath
    Explicit path to phase-table.json. When omitted it is resolved across the source layout
    (repo `phases/`), the deployed layout ($OBI_HOME/phases via the script's sibling), and the
    repo under classification.

.PARAMETER TrivialMax
    Max code lines for the trivial lane (inclusive). Default 5.

.PARAMETER ExpressMax
    Exclusive upper bound for the express lane. Default 25.

.OUTPUTS
    JSON object: { lane, phases, signal, lines_changed, code_files, appears_comment_only, reason }.

.EXAMPLE
    .\tools\classify-lane.ps1 -Base HEAD~1
.EXAMPLE
    .\tools\classify-lane.ps1 -Rigor max
#>

[CmdletBinding()]
param(
    [ValidateSet('standard', 'max')]
    [string]$Rigor = 'standard',
    [string]$Base = 'HEAD~1',
    [string]$RepoRoot,
    [string]$TablePath,
    [int]$TrivialMax = 5,
    [int]$ExpressMax = 25
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# RepoRoot is the git repo whose diff we measure — the project the orchestrator is working in.
# Default to the current directory, NOT the script's parent (after deploy the script lives under
# $OBI_HOME/tools, which is not the project being classified).
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }

# Resolve phase-table.json across layouts (first existing wins):
#   1. explicit -TablePath
#   2. the repo under classification (source repo, or a test fixture)   -> $RepoRoot\phases
#   3. next to the script                                               -> deployed sibling file
#   4. the script's sibling phases/ dir (source: tools<->phases; deployed: $OBI_HOME\phases)
$tableCandidates = @()
if ($TablePath) { $tableCandidates += $TablePath }
$tableCandidates += (Join-Path $RepoRoot 'phases\phase-table.json')
$tableCandidates += (Join-Path $ScriptDir 'phase-table.json')
$tableCandidates += (Join-Path (Split-Path -Parent $ScriptDir) 'phases\phase-table.json')
$jsonPath = $tableCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $jsonPath) { throw "phase-table.json not found (looked in: $($tableCandidates -join '; '))" }
$table = Get-Content $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Get-LaneResult {
    param([string]$Lane, [int]$Lines, [string[]]$Files, [bool]$CommentOnly, [string]$Reason,
          [bool]$NonCodeOnly = $false)
    $laneDef = $table.lanes.$Lane
    $signal = $laneDef.signal
    if ($signal) { $signal = $signal -replace '\{N\}', $Lines }
    return [ordered]@{
        lane                 = $Lane
        phases               = @($laneDef.phases)
        signal               = $signal
        lines_changed        = $Lines
        code_files           = @($Files)
        appears_comment_only = $CommentOnly
        non_code_only        = $NonCodeOnly
        reason               = $Reason
    }
}

# rigor=max short-circuits to the max lane (Option A: max IS a lane, OPT-18 section 6).
if ($Rigor -eq 'max') {
    (Get-LaneResult -Lane 'max' -Lines 0 -Files @() -CommentOnly $false `
        -Reason 'rigor=max: max lane assigned at invocation, no diff classification') |
        ConvertTo-Json -Depth 5
    return
}

# Code-file extensions that count toward lane size (the historical grep filter + tsx/jsx).
$codeExt = @('.ps1', '.psm1', '.psd1', '.cs', '.go', '.py', '.ts', '.tsx', '.js', '.jsx')

$numstat = @(& git -C $RepoRoot diff --numstat $Base 2>$null)
# Fail loud on git error: an unchecked failure would yield empty output -> lines=0 -> a silent,
# wrong 'express' classification. The orchestrator must see the error, not a misclassification.
if ($LASTEXITCODE -ne 0) {
    throw "git diff failed (exit $LASTEXITCODE) in '$RepoRoot' against base '$Base'. Cannot " +
          "classify lane safely; verify the directory is a git repo and the base ref exists."
}
$lines = 0
$files = @()
$totalChangedFiles = 0
foreach ($row in $numstat) {
    $parts = $row -split "`t"
    if ($parts.Count -lt 3) { continue }
    $totalChangedFiles++
    $added = $parts[0]; $removed = $parts[1]; $path = $parts[2]
    $ext = [System.IO.Path]::GetExtension($path)
    if ($ext -notin $codeExt) { continue }
    if ($added -eq '-' -or $removed -eq '-') { continue }  # binary
    $lines += ([int]$added + [int]$removed)
    $files += $path
}
# 0 code lines but non-code files changed (docs/config/binary): the size heuristic cannot speak to
# risk here. Flag it so the orchestrator applies semantic judgment instead of trusting express.
$nonCodeOnly = ($lines -eq 0 -and $totalChangedFiles -gt 0)

# Advisory comment/whitespace-only heuristic: every changed content line in the code files is
# blank or starts with a comment token. The orchestrator confirms the semantic call.
$commentOnly = $false
if ($files.Count -gt 0) {
    $commentPrefixes = @('#', '//', '<#', '#>', '*', '/*', '*/', '--', ';')
    $diff = @(& git -C $RepoRoot diff $Base -- $files 2>$null)
    $contentLines = $diff | Where-Object {
        ($_.StartsWith('+') -and -not $_.StartsWith('+++')) -or
        ($_.StartsWith('-') -and -not $_.StartsWith('---'))
    }
    $commentOnly = $true
    foreach ($cl in $contentLines) {
        $body = $cl.Substring(1).Trim()
        if ($body -eq '') { continue }
        $isComment = $false
        foreach ($p in $commentPrefixes) { if ($body.StartsWith($p)) { $isComment = $true; break } }
        if (-not $isComment) { $commentOnly = $false; break }
    }
}

# Size waterfall: trivial -> express -> standard. (Semantic exclusions applied by orchestrator.)
$nonCodeNote = if ($nonCodeOnly) { " - but $totalChangedFiles non-code file(s) changed; apply judgment" } else { '' }
if ($lines -le $TrivialMax -and $commentOnly -and $files.Count -gt 0) {
    $res = Get-LaneResult -Lane 'trivial' -Lines $lines -Files $files -CommentOnly $true `
        -Reason "$lines code lines, all comments/whitespace (<= $TrivialMax)"
} elseif ($lines -lt $ExpressMax) {
    $res = Get-LaneResult -Lane 'express' -Lines $lines -Files $files -CommentOnly $commentOnly `
        -NonCodeOnly $nonCodeOnly -Reason "$lines code lines changed (< $ExpressMax)$nonCodeNote"
} else {
    $res = Get-LaneResult -Lane 'standard' -Lines $lines -Files $files -CommentOnly $commentOnly `
        -NonCodeOnly $nonCodeOnly -Reason "$lines code lines changed (>= $ExpressMax)"
}

$res | ConvertTo-Json -Depth 5

<#
.SYNOPSIS
    Renders the human-readable Phase Table in phases/README.md from phases/phase-table.json.

.DESCRIPTION
    phases/phase-table.json is the single authored source of truth for the Obi Wag phase routing
    contract (OPT-10). This script renders its `display` + routing fields into the markdown table
    that lives between the <!-- obi:phase-table-start --> and <!-- obi:phase-table-end --> markers
    in phases/README.md. It is a pure formatter: every value it prints comes from the JSON.

    Default action writes the regenerated table back into README.md. Use -DryRun to print the
    rendered block without writing, or -Verify to exit non-zero when README.md is out of sync with
    a fresh render (used by tools/config-guardian.ps1 via lib/checks/check-dispatch-docs.ps1).

.PARAMETER RepoRoot
    Path to the obiwag-agents repo root. Defaults to two levels up from this script.

.PARAMETER DryRun
    Print the rendered block to stdout; do not modify README.md.

.PARAMETER Verify
    Compare the current README.md table block against a fresh render. Exit 0 if identical
    (line-ending-normalized), 1 if they differ. Does not modify README.md.

.EXAMPLE
    .\tools\render-phase-table.ps1
    # Regenerate the table in phases/README.md from phase-table.json.

.EXAMPLE
    .\tools\render-phase-table.ps1 -Verify
    # CI-friendly drift check; exit code reflects in-sync/out-of-sync.
#>

[CmdletBinding()]
param(
    [string]$RepoRoot,
    [switch]$DryRun,
    [switch]$Verify
)

$ErrorActionPreference = 'Stop'

if (-not $RepoRoot) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot  = Split-Path -Parent $ScriptDir
}

$jsonPath   = Join-Path $RepoRoot 'phases\phase-table.json'
$readmePath = Join-Path $RepoRoot 'phases\README.md'

$StartMarker = '<!-- obi:phase-table-start -->'
$EndMarker   = '<!-- obi:phase-table-end -->'
$GeneratedNote = '<!-- GENERATED FROM phases/phase-table.json BY tools/render-phase-table.ps1: DO NOT EDIT THIS TABLE BY HAND -->'

if (-not (Test-Path $jsonPath)) { throw "phase-table.json not found at $jsonPath" }

# UTF-8 round-trip preserves the em-dash (U+2014) in display.fallback_note etc.
$table = Get-Content $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json

# em-dash for empty cells; built from code point so this .ps1 source stays ASCII-safe.
$emDash = [string][char]0x2014

function Format-Row {
    param($Phase)
    $d = $Phase.display

    $c1 = [string]$Phase.n
    $c2 = "**$($Phase.name)**"
    $c3 = "``$($d.command)``"

    $c4 = "``$($Phase.agent)``"
    if ($d.model) { $c4 += " ($($d.model))" }

    # Default Strategy: bold when non-default (dispatch) or carrying a strategy note (#164).
    $strategyText = [string]$Phase.default_strategy
    if ($d.strategy_note) { $strategyText += " ($($d.strategy_note))" }
    $c5 = if ($Phase.default_strategy -ne 'inline' -or $d.strategy_note) { "**$strategyText**" } else { $strategyText }

    # Codified Recipe: bold letter (+ optional note), or em-dash when null.
    if ($Phase.recipe) {
        $c6 = "**$($Phase.recipe)**"
        if ($d.recipe_note) { $c6 += " ($($d.recipe_note))" }
    } else {
        $c6 = $emDash
    }

    # Inline-Fallback Eligible?: yes / no (note) / n/a (note).
    if ($Phase.inline_fallback_eligible) {
        $c7 = '**yes**'
    } else {
        $c7 = if ($Phase.delegated) { 'no' } else { 'n/a' }
        if ($d.fallback_note) { $c7 += " ($($d.fallback_note))" }
    }

    $c8 = [string]$d.signal_display

    # Lanes column (OPT-18): which lanes include this phase, in T/E/S/M order.
    $laneCells = $script:LaneMembership[[int]$Phase.n]
    $c9 = if ($laneCells) { $laneCells -join ' ' } else { $emDash }

    return "| $c1 | $c2 | $c3 | $c4 | $c5 | $c6 | $c7 | $c8 | $c9 |"
}

# Build per-phase lane membership from the top-level lanes structure (OPT-18). Each lane
# declares its TOTAL ordered phase list; a phase row shows the initials of every lane that
# includes it (T=trivial, E=express, S=standard, M=max), preserving that order.
$script:LaneMembership = @{}
$laneSpec = @(
    @{ key = 'trivial';  initial = 'T' },
    @{ key = 'express';  initial = 'E' },
    @{ key = 'standard'; initial = 'S' },
    @{ key = 'max';      initial = 'M' }
)
foreach ($spec in $laneSpec) {
    $lane = $table.lanes.$($spec.key)
    if (-not $lane) { continue }
    foreach ($p in $lane.phases) {
        $pn = [int]$p
        if (-not $script:LaneMembership.ContainsKey($pn)) { $script:LaneMembership[$pn] = @() }
        $script:LaneMembership[$pn] += $spec.initial
    }
}

# Header + separator are fixed; column widths in the separator are cosmetic markdown.
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add($GeneratedNote)
$lines.Add('| # | Phase | Command | Agent | Default Strategy | Codified Recipe | Inline-Fallback Eligible? | Completion Signal | Lanes |')
$lines.Add('|---|-------|---------|-------|------------------|-----------------|---------------------------|-------------------|:-----:|')
foreach ($phase in $table.phases) { $lines.Add((Format-Row $phase)) }

# LF-joined block; README.md is LF/no-BOM in this repo (core.autocrlf normalizes to LF in-index).
$block = ($lines -join "`n")

if ($DryRun) {
    Write-Output $block
    return
}

if (-not (Test-Path $readmePath)) { throw "phases/README.md not found at $readmePath" }
$content = [System.IO.File]::ReadAllText($readmePath)

$si = $content.IndexOf($StartMarker)
$ei = $content.IndexOf($EndMarker)
if ($si -lt 0 -or $ei -lt 0 -or $ei -lt $si) {
    throw "phases/README.md is missing the table markers ($StartMarker / $EndMarker)"
}

$expectedInner = "`n" + $block + "`n"
$currentInner  = $content.Substring($si + $StartMarker.Length, $ei - ($si + $StartMarker.Length))

if ($Verify) {
    $normCurrent  = $currentInner  -replace "`r`n", "`n"
    $normExpected = $expectedInner -replace "`r`n", "`n"
    if ($normCurrent -eq $normExpected) {
        exit 0
    } else {
        Write-Host 'phases/README.md table is OUT OF SYNC with phases/phase-table.json.' -ForegroundColor Red
        Write-Host 'Run: tools\render-phase-table.ps1' -ForegroundColor Yellow
        exit 1
    }
}

# Write mode: splice the fresh block between the markers, leave the rest of the file byte-identical.
$before = $content.Substring(0, $si)
$after  = $content.Substring($ei + $EndMarker.Length)
$new    = $before + $StartMarker + $expectedInner + $EndMarker + $after

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($readmePath, $new, $utf8NoBom)
Write-Host "Rendered phase table into $readmePath" -ForegroundColor Green

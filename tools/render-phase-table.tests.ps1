<#
.SYNOPSIS
    Pester tests for tools/render-phase-table.ps1 (OPT-10 phase-table renderer).
#>
BeforeAll {

# Requires Pester 5 (see .obi/runtime/run-pester5.ps1)

$ScriptDir  = $PSScriptRoot
$Renderer  = Join-Path $ScriptDir 'render-phase-table.ps1'
$RealRepo  = Split-Path -Parent $ScriptDir

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$MinimalJson = @'
{
  "schema_version": 6,
  "lanes": {
    "trivial":  {"phases": [2, 9], "signal": "TRIVIAL LANE: {N}", "description": "t"},
    "express":  {"phases": [1], "signal": "EXPRESS LANE: {N}", "description": "e"},
    "standard": {"phases": [1, 6], "signal": null, "description": "s"},
    "max":      {"phases": [1, 6], "signal": null, "description": "m"}
  },
  "phases": [
    {"n": 1, "name": "Discovery", "agent": "obi-discovery", "delegated": true, "default_strategy": "dispatch", "recipe": null, "inline_fallback_eligible": false, "primary_signal": {"value": "DISCOVERY COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": false, "display": {"command": "/discovery", "model": "opus", "strategy_note": null, "recipe_note": null, "fallback_note": "synthesis", "signal_display": "`DISCOVERY COMPLETE`"}},
    {"n": 6, "name": "Re-review", "agent": "obi-rereviewer", "delegated": true, "default_strategy": "inline", "recipe": "R", "inline_fallback_eligible": true, "primary_signal": {"value": "RE-REVIEW COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": true, "display": {"command": "/re-review", "model": "opus", "strategy_note": "#164", "recipe_note": null, "fallback_note": null, "signal_display": "`RE-REVIEW COMPLETE` (verdict in body)"}}
  ]
}
'@

function New-FixtureRepo {
    param([string]$Slug)
    $repo = Join-Path $env:TEMP "rpt-test-$Slug-$(Get-Random)"
    New-Item -ItemType Directory -Force -Path "$repo\phases" | Out-Null
    Set-Content -Path "$repo\phases\phase-table.json" -Value $MinimalJson -Encoding UTF8
    $readme = @"
# Phase Table

<!-- obi:phase-table-start -->
<!-- obi:phase-table-end -->

## Delegation policy
"@
    Set-Content -Path "$repo\phases\README.md" -Value $readme -Encoding UTF8
    return $repo
}

}

Describe 'render-phase-table.ps1' {

    It 'DryRun emits the header (with Lanes column) and one row per phase' {
        $repo = New-FixtureRepo 'dryrun'
        $out = (& $Renderer -RepoRoot $repo -DryRun) -join "`n"
        $out | Should -Match '\| # \| Phase \| Command \|'
        $out | Should -Match '\| Lanes \|'
        $out | Should -Match '\| 1 \| \*\*Discovery\*\* \|'
        $out | Should -Match '\| 6 \| \*\*Re-review\*\* \|'
    }

    It 'renders an em-dash (U+2014) for the null recipe cell' {
        $repo = New-FixtureRepo 'emdash'
        $out = (& $Renderer -RepoRoot $repo -DryRun) -join "`n"
        $emDash = [char]0x2014
        # Discovery has a null recipe -> em-dash in its recipe cell.
        ($out -split "`n" | Where-Object { $_ -match '^\| 1 \|' }) | Should -Match $emDash
    }

    It 'renders lane membership initials and leaves verdict-in-body note intact' {
        $repo = New-FixtureRepo 'lanes'
        $out = (& $Renderer -RepoRoot $repo -DryRun) -join "`n"
        # Phase 1 is in express/standard/max -> "E S M"; phase 6 only standard/max -> "S M".
        ($out -split "`n" | Where-Object { $_ -match '^\| 1 \|' }) | Should -Match 'E S M'
        ($out -split "`n" | Where-Object { $_ -match '^\| 6 \|' }) | Should -Match '\| S M \|'
        $out | Should -Match '`RE-REVIEW COMPLETE` \(verdict in body\)'
    }

    It 'is idempotent: rendering twice yields an identical README' {
        $repo = New-FixtureRepo 'idempotent'
        & $Renderer -RepoRoot $repo | Out-Null
        $first = [System.IO.File]::ReadAllText("$repo\phases\README.md")
        & $Renderer -RepoRoot $repo | Out-Null
        $second = [System.IO.File]::ReadAllText("$repo\phases\README.md")
        $second | Should -Be $first
    }

    It 'Verify exits 0 immediately after a render' {
        $repo = New-FixtureRepo 'verifyok'
        & $Renderer -RepoRoot $repo | Out-Null
        & $Renderer -RepoRoot $repo -Verify
        $LASTEXITCODE | Should -Be 0
    }

    It 'Verify exits 1 when the table is out of sync' {
        $repo = New-FixtureRepo 'verifystale'
        & $Renderer -RepoRoot $repo | Out-Null
        $readmePath = "$repo\phases\README.md"
        $c = [System.IO.File]::ReadAllText($readmePath) -replace 'Discovery', 'Drifted'
        [System.IO.File]::WriteAllText($readmePath, $c, $Utf8NoBom)
        & $Renderer -RepoRoot $repo -Verify
        $LASTEXITCODE | Should -Be 1
    }

    It 'keeps the production phases/README.md in sync with phase-table.json' {
        & $Renderer -RepoRoot $RealRepo -Verify
        $LASTEXITCODE | Should -Be 0
    }

    It 'production Phase 5 declares the guarded no-op signal and skips only Re-review' {
        $table = Get-Content -LiteralPath (Join-Path $RealRepo 'phases\phase-table.json') -Raw | ConvertFrom-Json
        $phase5 = @($table.phases | Where-Object { $_.n -eq 5 })[0]
        @($phase5.special_signals.value) | Should -Contain 'INTEGRATE NO-OP:'
        $transition = @($phase5.transitions | Where-Object { $_.on_signal -eq 'INTEGRATE NO-OP:' })[0]
        $transition.match_mode | Should -Be 'prefix'
        @($transition.skip) | Should -Be @(6)
    }
}

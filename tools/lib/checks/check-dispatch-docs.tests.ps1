<#
.SYNOPSIS
    Pester 3.4 tests for tools/lib/checks/check-dispatch-docs.ps1 (Test-DispatchDocs function).

.DESCRIPTION
    Tests the two invariants:
      Check 1 - phase-table.json parses, has expected schema_version, required keys.
      Check 2 - README table matches a fresh render from phase-table.json.

    Fixtures build a phase-table.json + marker-based README, populating the table
    with the real renderer so the happy path is in-sync by construction.
#>

$script:ToolsDir = Join-Path (Split-Path -Parent $PSScriptRoot) '..'
$script:ToolsDir = (Resolve-Path $script:ToolsDir).Path
$script:Renderer = Join-Path $script:ToolsDir 'render-phase-table.ps1'

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# Minimal 2-phase contract (schema 6, with lanes + display objects). Lane phase refs
# (0/1/3) must all be valid: 0 = max prereq, 1 + 3 exist in the phases array.
$MinimalJson = @'
{
  "schema_version": 6,
  "lanes": {
    "trivial":  {"phases": [3], "signal": "TRIVIAL LANE: {N}", "description": "t"},
    "express":  {"phases": [1, 3], "signal": "EXPRESS LANE: {N}", "description": "e"},
    "standard": {"phases": [1, 3], "signal": null, "description": "s"},
    "max":      {"phases": [0, 1, 3], "signal": null, "description": "m", "gates": [3]}
  },
  "phases": [
    {"n": 1, "name": "Discovery", "agent": "obi-discovery", "delegated": true, "default_strategy": "dispatch", "recipe": null, "inline_fallback_eligible": false, "primary_signal": {"value": "DISCOVERY COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": false, "display": {"command": "/discovery", "model": "opus", "strategy_note": null, "recipe_note": null, "fallback_note": "synthesis", "signal_display": "`DISCOVERY COMPLETE`"}},
    {"n": 3, "name": "Simplify", "agent": "obi-simplify", "delegated": true, "default_strategy": "inline", "recipe": "S", "inline_fallback_eligible": true, "primary_signal": {"value": "SIMPLIFY COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": false, "display": {"command": "/simplify", "model": "opus", "strategy_note": "#164", "recipe_note": null, "fallback_note": null, "signal_display": "`SIMPLIFY COMPLETE`"}}
  ]
}
'@

function New-FixtureRepo {
    param([string]$Slug)
    $repo = Join-Path $env:TEMP "hcdd-test-$Slug-$(Get-Random)"
    New-Item -ItemType Directory -Force -Path "$repo\phases" | Out-Null
    return $repo
}

function Set-SyncedFixture {
    param([string]$Repo, [string]$JsonBody = $MinimalJson)
    Set-Content -Path "$Repo\phases\phase-table.json" -Value $JsonBody -Encoding UTF8
    $readme = @"
# Phase Table

<!-- obi:phase-table-start -->
<!-- obi:phase-table-end -->

## Delegation policy
"@
    Set-Content -Path "$Repo\phases\README.md" -Value $readme -Encoding UTF8
    & $script:Renderer -RepoRoot $Repo | Out-Null
}

Describe 'Test-DispatchDocs' {

    BeforeEach {
        $script:ScriptDir = $script:ToolsDir
        $script:RepoRoot = $null

        # Dot-source the module under test and its dependencies
        . (Join-Path $ScriptDir 'lib\common.ps1')
        . (Join-Path $ScriptDir 'lib\checks\check-dispatch-docs.ps1')
    }

    It 'returns valid on a synced fixture (valid JSON + in-sync README)' {
        $repo = New-FixtureRepo 'happy'
        Set-SyncedFixture -Repo $repo
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should Be $true
        $result.Errors.Count | Should Be 0
    }

    It 'returns invalid on malformed phase-table.json' {
        $repo = New-FixtureRepo 'badjson'
        Set-Content -Path "$repo\phases\phase-table.json" -Value '{ "schema_version": 5, "phases": [ NOT JSON' -Encoding UTF8
        Set-Content -Path "$repo\phases\README.md" -Value 'placeholder' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should Be $false
    }

    It 'returns invalid when schema_version is not the expected value' {
        $repo = New-FixtureRepo 'badschema'
        $json = $MinimalJson -replace '"schema_version": 6', '"schema_version": 5'
        Set-SyncedFixture -Repo $repo -JsonBody $json
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should Be $false
    }

    It 'returns invalid when a phase is missing a required key' {
        $repo = New-FixtureRepo 'missingkey'
        # schema 6 + valid lanes, so the ONLY defect is phase 3 missing verdict_in_body.
        $missing = @'
{
  "schema_version": 6,
  "lanes": {
    "trivial":  {"phases": [3], "signal": null, "description": "t"},
    "express":  {"phases": [1, 3], "signal": null, "description": "e"},
    "standard": {"phases": [1, 3], "signal": null, "description": "s"},
    "max":      {"phases": [0, 1, 3], "signal": null, "description": "m"}
  },
  "phases": [
    {"n": 1, "name": "Discovery", "agent": "obi-discovery", "delegated": true, "default_strategy": "dispatch", "recipe": null, "inline_fallback_eligible": false, "primary_signal": {"value": "DISCOVERY COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": false, "display": {"command": "/discovery", "model": "opus", "strategy_note": null, "recipe_note": null, "fallback_note": "synthesis", "signal_display": "`DISCOVERY COMPLETE`"}},
    {"n": 3, "name": "Simplify", "agent": "obi-simplify", "delegated": true, "default_strategy": "inline", "recipe": "S", "inline_fallback_eligible": true, "primary_signal": {"value": "SIMPLIFY COMPLETE", "match_mode": "literal"}, "special_signals": [], "display": {"command": "/simplify", "model": "opus", "strategy_note": "#164", "recipe_note": null, "fallback_note": null, "signal_display": "`SIMPLIFY COMPLETE`"}}
  ]
}
'@
        Set-SyncedFixture -Repo $repo -JsonBody $missing
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should Be $false
    }

    It 'returns invalid when the README table is out of sync with the JSON' {
        $repo = New-FixtureRepo 'stale'
        Set-SyncedFixture -Repo $repo
        $readmePath = "$repo\phases\README.md"
        $c = [System.IO.File]::ReadAllText($readmePath) -replace 'Discovery', 'DiscoveryX'
        [System.IO.File]::WriteAllText($readmePath, $c, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should Be $false
    }

    It 'returns invalid when phases/README.md is missing the table markers' {
        $repo = New-FixtureRepo 'nomarkers'
        Set-Content -Path "$repo\phases\phase-table.json" -Value $MinimalJson -Encoding UTF8
        Set-Content -Path "$repo\phases\README.md" -Value "# Phase Table`n`n(no markers here)`n" -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should Be $false
    }
}

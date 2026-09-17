<#
.SYNOPSIS
    Pester 5 tests for tools/lib/checks/check-dispatch-docs.ps1 (Test-DispatchDocs function).

.DESCRIPTION
    Tests the contract invariants:
      Check 1 - phase-table.json parses, has expected schema_version, required keys.
      Check 2 - README table matches a fresh render from phase-table.json.
      Check 3 - Codex catch-log wiring (log-codex-catch.ps1 + -Phase plan/review)
                present in policies/rigor-max-gates.md and phases/05-integrate/command.md.

    Fixtures build a phase-table.json + marker-based README, populating the table
    with the real renderer so the happy path is in-sync by construction, plus the
    contract docs carrying the catch-log and bounded release wiring so the fixture passes by default.
#>
BeforeAll {

. (Join-Path $PSScriptRoot 'check-dispatch-docs.test-support.ps1')

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
        $result.Valid | Should -Be $true
        $result.Errors.Count | Should -Be 0
    }

    It 'rejects a missing efficiency tier mapping' {
        $repo = New-FixtureRepo 'missing-efficiency-route'
        Set-SyncedFixture -Repo $repo
        $path = Join-Path $repo 'phases/phase-table.json'
        $content = [IO.File]::ReadAllText($path).Replace('"model": "sonnet"', '"model": ""')
        [IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'efficiency policy lacks routine/claude'
    }

    It 'returns invalid when a phase budget exceeds the host outer-call ceiling' {
        # Issue #200 T1: absolute_sec + cleanup_margin_sec above 570 s means the
        # harness, not the supervisor, ends the call -- terminal status is lost.
        $repo = New-FixtureRepo 'budget-over-host-ceiling'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\phases\phase-table.json"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            '"nominal_work_sec": 390, "idle_sec": 90, "productive_extension_sec": 90, "finalization_sec": 60, "absolute_sec": 540',
            '"nominal_work_sec": 420, "idle_sec": 90, "productive_extension_sec": 120, "finalization_sec": 60, "absolute_sec": 600')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'exceeds the 570s host ceiling'
    }

    It 'returns invalid when operation-timeouts drops the self-correction clause' {
        $repo = New-FixtureRepo 'missing-timeout-self-correction'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\docs\operation-timeouts.md"
        [string]$selfCorrection = "If you reach the step-1 read after a sentinel without having emitted the DETECTED line, you skipped the contract $([char]0x2014) emit it now."
        $content = [System.IO.File]::ReadAllText($path).Replace(
            $selfCorrection,
            'Continue without emitting the missing line.')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'missing exact markers in docs/operation-timeouts.md'
    }

    It 'returns invalid when obi-auto reorders the recovery transcript' {
        $repo = New-FixtureRepo 'reordered-auto-transcript'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [regex]::Replace(
            [System.IO.File]::ReadAllText($path),
            'VERIFY: no completed Review artifact; retry budget available\r?\nRETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS',
            "RETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS`r`nVERIFY: no completed Review artifact; retry budget available")
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'lacks the exact ordered text transcript in orchestration/obi-auto.md'
    }

    It 'returns invalid when operation-timeouts changes DROP marker case' {
        $repo = New-FixtureRepo 'timeout-marker-case-drift'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\docs\operation-timeouts.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'DROP RESOLVED: retried-ok',
            'drop resolved: retried-ok')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'docs/operation-timeouts.md'
    }

    It 'returns invalid when obi-auto changes DROP marker case' {
        $repo = New-FixtureRepo 'auto-marker-case-drift'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'DROP RESOLVED: retried-ok',
            'drop resolved: retried-ok')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'orchestration/obi-auto.md'
    }

    It 'returns invalid when operation-timeouts is missing' {
        $repo = New-FixtureRepo 'missing-operation-timeouts'
        Set-SyncedFixture -Repo $repo
        Remove-Item -LiteralPath "$repo\docs\operation-timeouts.md" -Force
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'operation-timeouts.md not found for drop-recovery contract validation'
    }

    It 'returns invalid when operation-timeouts reorders the recovery transcript' {
        $repo = New-FixtureRepo 'reordered-timeout-transcript'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\docs\operation-timeouts.md"
        $content = [regex]::Replace(
            [System.IO.File]::ReadAllText($path),
            'VERIFY: no completed Review artifact; retry budget available\r?\nRETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS',
            "RETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS`r`nVERIFY: no completed Review artifact; retry budget available")
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'lacks the exact ordered text transcript in docs/operation-timeouts.md'
    }

    It 'returns invalid on malformed phase-table.json' {
        $repo = New-FixtureRepo 'badjson'
        Set-Content -Path "$repo\phases\phase-table.json" -Value '{ "schema_version": 5, "phases": [ NOT JSON' -Encoding UTF8
        Set-Content -Path "$repo\phases\README.md" -Value 'placeholder' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when schema_version is not the expected value' {
        $repo = New-FixtureRepo 'badschema'
        $json = $MinimalJson -replace '"schema_version": 7', '"schema_version": 5'
        Set-SyncedFixture -Repo $repo -JsonBody $json
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when a phase is missing a required key' {
        $repo = New-FixtureRepo 'missingkey'
        # Schema 7 + valid defaults/lanes; only Simplify loses verdict_in_body.
        $missing = $MinimalJson -replace '"verdict_in_body": false, "display": \{"command": "/simplify"',
            '"display": {"command": "/simplify"'
        Set-SyncedFixture -Repo $repo -JsonBody $missing
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when the README table is out of sync with the JSON' {
        $repo = New-FixtureRepo 'stale'
        Set-SyncedFixture -Repo $repo
        $readmePath = "$repo\phases\README.md"
        $c = [System.IO.File]::ReadAllText($readmePath) -replace 'Discovery', 'DiscoveryX'
        [System.IO.File]::WriteAllText($readmePath, $c, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when phases/README.md is missing the table markers' {
        $repo = New-FixtureRepo 'nomarkers'
        Set-Content -Path "$repo\phases\phase-table.json" -Value $MinimalJson -Encoding UTF8
        Set-Content -Path "$repo\phases\README.md" -Value "# Phase Table`n`n(no markers here)`n" -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when the phase=plan catch-log wiring is dropped from rigor-max-gates.md (DD-3, OPT-19/21)' {
        $repo = New-FixtureRepo 'nocatch'
        Set-SyncedFixture -Repo $repo
        # Simulate the regression: the -Phase plan invocation disappears from step 7.
        $p = "$repo\policies\rigor-max-gates.md"
        $c = [System.IO.File]::ReadAllText($p) -replace '-Phase plan', '-Phase nope'
        [System.IO.File]::WriteAllText($p, $c, $Utf8NoBom)
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when rigor-max-gates.md is missing entirely (DD-3)' {
        # Guards the move itself: if a future edit deletes or relocates the file the
        # rigor=max block now lives in, DD-3 must fail rather than silently pass.
        $repo = New-FixtureRepo 'nogatesfile'
        Set-SyncedFixture -Repo $repo
        Remove-Item "$repo\policies\rigor-max-gates.md" -Force
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
    }

    It 'returns invalid when a transition signal is not declared by its phase' {
        $repo = New-FixtureRepo 'badtransition'
        $json = $MinimalJson -replace '"special_signals": \[\], "verdict_in_body": false',
            '"special_signals": [], "transitions": [{"on_signal":"UNKNOWN:","skip":[3],"match_mode":"prefix"}], "verdict_in_body": false'
        Set-SyncedFixture -Repo $repo -JsonBody $json
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'does not match a declared primary/special signal'
    }

    It 'returns invalid when obi-auto hard-codes an inline-ineligible phase list' {
        $repo = New-FixtureRepo 'stalelist'
        Set-SyncedFixture -Repo $repo
        Set-Content -LiteralPath "$repo\orchestration\obi-auto.md" `
            -Value 'phase-table.json inline_fallback_eligible: false; inline-ineligible phases (Discovery, Review, Learning)' -Encoding UTF8
        $script:RepoRoot = $repo
        $result = Test-DispatchDocs -CheckOnly
        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'duplicated hard-coded'
    }

    It 'returns invalid when successful completion deletes only run identity and leaves stale markers' {
        $repo = New-FixtureRepo 'stale-completion-cleanup'
        Set-SyncedFixture -Repo $repo
        Set-Content -LiteralPath "$repo\orchestration\obi-auto.md" `
            -Value 'phase-table.json inline_fallback_eligible: false. On successful Learning delete BOTH dispatch-state.json AND run-id.txt.' -Encoding UTF8
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'archive active-run state'
    }

    It 'returns invalid when synthesis takeover requires explicit the user authorization' {
        $repo = New-FixtureRepo 'permission-gated-primary-takeover'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'Standing autonomous authority begins the primary takeover automatically.',
            'Primary takeover requires explicit the user authorization.')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'permission-only stop'
    }

    It 'returns invalid when the append-only autonomous recovery ledger is missing' {
        $repo = New-FixtureRepo 'missing-autonomous-ledger'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'status-updates-<run_id>.jsonl',
            'transient-status.txt')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'append-only autonomous-recovery'
    }

    It 'returns invalid when an inline fallback recipe halts for continuation permission' {
        $repo = New-FixtureRepo 'permission-gated-inline-recipe'
        Set-SyncedFixture -Repo $repo
        Add-Content -LiteralPath "$repo\orchestration\inline-fallback-recipes.md" `
            -Value 'On dirty state, halt with NEEDS USER INPUT.' -Encoding UTF8
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'inline fallback recipes'
    }

    It 'returns invalid when the max wrapper drops autonomous recovery inheritance' {
        $repo = New-FixtureRepo 'max-wrapper-permission-regression'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto-max.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'do not reintroduce permission-only continuation questions',
            'AskUserQuestion before continuing')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'obi-auto-max must inherit'
    }

    It 'returns invalid when three-strike exhaustion waits for the user' {
        $repo = New-FixtureRepo 'three-strike-permission-regression'
        Set-SyncedFixture -Repo $repo
        Add-Content -LiteralPath "$repo\policies\three-strike-rule.md" `
            -Value 'Waiting for the user direction.' -Encoding UTF8
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'three-strike policy'
    }

    It 'returns invalid when Discovery takeover can perform new source research' {
        $repo = New-FixtureRepo 'takeover-reresearch'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'uses settled evidence only and performs no new source research',
            'may continue source research')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'settled evidence only'
    }

    It 'accepts takeover markers wrapped across Markdown lines' {
        $repo = New-FixtureRepo 'wrapped-takeover-marker'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'no new source research',
            "no new source`r`nresearch")
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $true
        $result.Errors.Count | Should -Be 0
    }

    It 'returns invalid when normal lifecycle paths omit orphan-state archival' {
        $repo = New-FixtureRepo 'missing-orphan-archive'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'invoke archive-orphan-state.ps1 -Mode Archive',
            'leave orphan state untouched')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'orphan-state archive'
    }

    It 'returns invalid when completion omits the open lifecycle tracker block' {
        $repo = New-FixtureRepo 'missing-open-tracker-block'
        Set-SyncedFixture -Repo $repo
        $path = "$repo\orchestration\obi-auto.md"
        $content = [System.IO.File]::ReadAllText($path).Replace(
            'archive-run-state.ps1 returns blocked_open_trackers for an exact Status: OPEN line and must never rewrite the tracker.',
            'continue regardless of tracker state.')
        [System.IO.File]::WriteAllText($path, $content, $Utf8NoBom)
        $script:RepoRoot = $repo

        $result = Test-DispatchDocs -CheckOnly

        $result.Valid | Should -Be $false
        ($result.Errors -join "`n") | Should -Match 'lifecycle trackers'
    }

}

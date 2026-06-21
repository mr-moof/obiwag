<#
.SYNOPSIS
    Pester 3.4 tests for tools/log-codex-catch.ps1 (OPT-19 catch log).
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogCatch  = Join-Path $ScriptDir 'log-codex-catch.ps1'

function New-CatchFixture {
    <#
    .SYNOPSIS Helper: create a temp repo root under $TestDrive.
    #>
    param([string]$Slug)
    $root = Join-Path $TestDrive "catch-$Slug-$(Get-Random)"
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    return $root
}

Describe 'log-codex-catch.ps1' {

    It 'appends a valid JSONL line with all fields' {
        $root = New-CatchFixture 'valid'
        & $LogCatch -Repo 'test-repo' -Ref 'OPT-1' -Phase review `
            -Category 'test-gap' -Severity medium -Summary 'missing edge case test' `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $logFile | Should Exist

        $lines = @(Get-Content $logFile)
        $lines.Count | Should Be 1

        $obj = $lines[0] | ConvertFrom-Json
        $obj.repo     | Should Be 'test-repo'
        $obj.ref      | Should Be 'OPT-1'
        $obj.phase    | Should Be 'review'
        $obj.category | Should Be 'test-gap'
        $obj.severity | Should Be 'medium'
        $obj.summary  | Should Be 'missing edge case test'
        $obj.disputed | Should Be $false
        $obj.ts       | Should Not BeNullOrEmpty
    }

    It 'DryRun previews without writing a file' {
        $root = New-CatchFixture 'dryrun'
        $output = & $LogCatch -Repo 'test-repo' -Ref 'OPT-2' -Phase plan `
            -Category 'plan-gap' -Severity low -Summary 'dry run test' `
            -RepoRoot $root -DryRun

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $logFile | Should Not Exist

        # Output should be parseable JSON
        $obj = $output | ConvertFrom-Json
        $obj.repo | Should Be 'test-repo'
        $obj.phase | Should Be 'plan'
    }

    It 'rejects invalid Category' {
        $root = New-CatchFixture 'badcat'
        {
            & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
                -Category 'bogus' -Severity high -Summary 's' `
                -RepoRoot $root
        } | Should Throw
    }

    It 'rejects invalid Phase' {
        $root = New-CatchFixture 'badphase'
        {
            & $LogCatch -Repo 'r' -Ref 'x' -Phase 'nosuch' `
                -Category 'other' -Severity high -Summary 's' `
                -RepoRoot $root
        } | Should Throw
    }

    It 'rejects invalid Severity' {
        $root = New-CatchFixture 'badsev'
        {
            & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
                -Category 'other' -Severity 'critical' -Summary 's' `
                -RepoRoot $root
        } | Should Throw
    }

    It 'appends two lines without overwriting' {
        $root = New-CatchFixture 'append'
        & $LogCatch -Repo 'r' -Ref 'A' -Phase review `
            -Category 'security' -Severity high -Summary 'first' `
            -RepoRoot $root
        & $LogCatch -Repo 'r' -Ref 'B' -Phase freeform `
            -Category 'other' -Severity low -Summary 'second' `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $lines = @(Get-Content $logFile)
        $lines.Count | Should Be 2

        ($lines[0] | ConvertFrom-Json).ref | Should Be 'A'
        ($lines[1] | ConvertFrom-Json).ref | Should Be 'B'
    }

    It 'round-trips all schema fields through JSON' {
        $root = New-CatchFixture 'roundtrip'
        & $LogCatch -Repo 'myrepo' -Ref 'PR-42' -Phase review `
            -Category 'invented-api' -Severity high `
            -Summary 'Called nonexistent cmdlet' `
            -Disputed -DisputeResolution 'codex-right' `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $obj = @(Get-Content $logFile)[0] | ConvertFrom-Json

        # All nine schema fields present
        ($obj | Get-Member -Name 'ts' -MemberType NoteProperty)                 | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'repo' -MemberType NoteProperty)               | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'ref' -MemberType NoteProperty)                | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'phase' -MemberType NoteProperty)              | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'category' -MemberType NoteProperty)           | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'severity' -MemberType NoteProperty)           | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'summary' -MemberType NoteProperty)            | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'disputed' -MemberType NoteProperty)           | Should Not BeNullOrEmpty
        ($obj | Get-Member -Name 'dispute_resolution' -MemberType NoteProperty) | Should Not BeNullOrEmpty

        $obj.disputed           | Should Be $true
        $obj.dispute_resolution | Should Be 'codex-right'
    }

    It 'writes BOM-less UTF-8' {
        $root = New-CatchFixture 'bom'
        & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
            -Category 'other' -Severity low -Summary 'bom check' `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $bytes = [System.IO.File]::ReadAllBytes($logFile)
        # UTF-8 BOM is EF BB BF (239 187 191)
        if ($bytes.Length -ge 3) {
            $hasBom = ($bytes[0] -eq 0xEF) -and ($bytes[1] -eq 0xBB) -and ($bytes[2] -eq 0xBF)
            $hasBom | Should Be $false
        }
        # First byte should be '{' (0x7B) — start of JSON
        $bytes[0] | Should Be 0x7B
    }

    It 'creates .obi directory when it does not exist' {
        $root = New-CatchFixture 'mkdir'
        $obiDir = Join-Path $root '.obi'
        # Confirm .obi does not pre-exist
        $obiDir | Should Not Exist

        & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
            -Category 'other' -Severity low -Summary 'dir creation' `
            -RepoRoot $root

        $obiDir | Should Exist
        (Join-Path $obiDir 'codex-catches.jsonl') | Should Exist
    }
}

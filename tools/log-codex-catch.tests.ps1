<#
.SYNOPSIS
    Pester 5 tests for tools/log-codex-catch.ps1 (OPT-19 catch log).
#>
BeforeAll {

$ScriptDir  = $PSScriptRoot
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

}

Describe 'log-codex-catch.ps1' {

    It 'appends a valid JSONL line with all fields' {
        $root = New-CatchFixture 'valid'
        & $LogCatch -Repo 'test-repo' -Ref 'OPT-1' -Phase review `
            -Category 'test-gap' -Severity medium -Summary 'missing edge case test' `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $logFile | Should -Exist

        $lines = @(Get-Content $logFile)
        $lines.Count | Should -Be 1

        $obj = $lines[0] | ConvertFrom-Json
        $obj.repo     | Should -Be 'test-repo'
        $obj.ref      | Should -Be 'OPT-1'
        $obj.phase    | Should -Be 'review'
        $obj.category | Should -Be 'test-gap'
        $obj.severity | Should -Be 'medium'
        $obj.summary  | Should -Be 'missing edge case test'
        $obj.peer_provider | Should -Be 'codex'
        $obj.disputed | Should -Be $false
        $obj.ts       | Should -Not -BeNullOrEmpty
    }

    It 'DryRun previews without writing a file' {
        $root = New-CatchFixture 'dryrun'
        $output = & $LogCatch -Repo 'test-repo' -Ref 'OPT-2' -Phase plan `
            -Category 'plan-gap' -Severity low -Summary 'dry run test' `
            -RepoRoot $root -DryRun

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $logFile | Should -Not -Exist

        # Output should be parseable JSON
        $obj = $output | ConvertFrom-Json
        $obj.repo | Should -Be 'test-repo'
        $obj.phase | Should -Be 'plan'
    }

    It 'rejects invalid Category' {
        $root = New-CatchFixture 'badcat'
        {
            & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
                -Category 'bogus' -Severity high -Summary 's' `
                -RepoRoot $root
        } | Should -Throw
    }

    It 'rejects invalid Phase' {
        $root = New-CatchFixture 'badphase'
        {
            & $LogCatch -Repo 'r' -Ref 'x' -Phase 'nosuch' `
                -Category 'other' -Severity high -Summary 's' `
                -RepoRoot $root
        } | Should -Throw
    }

    It 'rejects invalid Severity' {
        $root = New-CatchFixture 'badsev'
        {
            & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
                -Category 'other' -Severity 'critical' -Summary 's' `
                -RepoRoot $root
        } | Should -Throw
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
        $lines.Count | Should -Be 2

        ($lines[0] | ConvertFrom-Json).ref | Should -Be 'A'
        ($lines[1] | ConvertFrom-Json).ref | Should -Be 'B'
    }

    It 'records a zero-finding attempt with timing and pass identity' {
        $root = New-CatchFixture 'attempt-zero'
        & $LogCatch -Repo 'r' -Ref 'RUN-1' -Phase plan -Attempt `
            -PassId 'peer-001' -DurationMs 1234 -FindingCount 0 -AcceptedCount 0 `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $obj = @(Get-Content $logFile)[0] | ConvertFrom-Json
        $obj.record_type | Should -Be 'attempt'
        $obj.pass_id | Should -Be 'peer-001'
        $obj.duration_ms | Should -Be 1234
        $obj.finding_count | Should -Be 0
        $obj.accepted_count | Should -Be 0
        $obj.outcome | Should -Be 'zero_findings'
    }

    It 'appends attempt rows beside findings and rejects impossible counts' {
        $root = New-CatchFixture 'attempt-append'
        & $LogCatch -Repo 'r' -Ref 'F-1' -Phase review `
            -Category 'other' -Severity low -Summary 'finding' -RepoRoot $root
        & $LogCatch -Repo 'r' -Ref 'RUN-2' -Phase review -Attempt `
            -PassId 'peer-002' -DurationMs 50 -FindingCount 2 -AcceptedCount 1 `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $rows = @(Get-Content $logFile | ForEach-Object { $_ | ConvertFrom-Json })
        $rows.Count | Should -Be 2
        $rows[0].record_type | Should -Be 'finding'
        $rows[1].record_type | Should -Be 'attempt'
        $rows[1].outcome | Should -Be 'findings'

        {
            & $LogCatch -Repo 'r' -Ref 'RUN-3' -Phase plan -Attempt `
                -PassId 'peer-003' -DurationMs 1 -FindingCount 0 -AcceptedCount 1 `
                -RepoRoot $root
        } | Should -Throw '*cannot exceed*'
    }

    It 'round-trips all schema fields through JSON' {
        $root = New-CatchFixture 'roundtrip'
        & $LogCatch -Repo 'myrepo' -Ref 'MR-42' -Phase review `
            -Category 'invented-api' -Severity high `
            -Summary 'Called nonexistent cmdlet' `
            -Disputed -DisputeResolution 'codex-right' `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $obj = @(Get-Content $logFile)[0] | ConvertFrom-Json

        # All ten schema fields present
        ($obj | Get-Member -Name 'ts' -MemberType NoteProperty)                 | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'repo' -MemberType NoteProperty)               | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'ref' -MemberType NoteProperty)                | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'phase' -MemberType NoteProperty)              | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'category' -MemberType NoteProperty)           | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'severity' -MemberType NoteProperty)           | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'summary' -MemberType NoteProperty)            | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'peer_provider' -MemberType NoteProperty)      | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'disputed' -MemberType NoteProperty)           | Should -Not -BeNullOrEmpty
        ($obj | Get-Member -Name 'dispute_resolution' -MemberType NoteProperty) | Should -Not -BeNullOrEmpty

        $obj.disputed           | Should -Be $true
        $obj.dispute_resolution | Should -Be 'codex-right'
    }

    It 'records Claude peer provenance without changing the historical path' {
        $root = New-CatchFixture 'claude-provider'
        & $LogCatch -Repo 'myrepo' -Ref 'RUN-1' -Phase review `
            -Category 'regression-risk' -Severity medium `
            -Summary 'Claude found a lifecycle race' -PeerProvider claude `
            -RepoRoot $root

        $logFile = Join-Path (Join-Path $root '.obi') 'codex-catches.jsonl'
        $obj = @(Get-Content $logFile)[0] | ConvertFrom-Json
        $obj.peer_provider | Should -Be 'claude'
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
            $hasBom | Should -Be $false
        }
        # First byte should be '{' (0x7B) — start of JSON
        $bytes[0] | Should -Be 0x7B
    }

    It 'creates .obi directory when it does not exist' {
        $root = New-CatchFixture 'mkdir'
        $obiDir = Join-Path $root '.obi'
        # Confirm .obi does not pre-exist
        $obiDir | Should -Not -Exist

        & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
            -Category 'other' -Severity low -Summary 'dir creation' `
            -RepoRoot $root

        $obiDir | Should -Exist
        (Join-Path $obiDir 'codex-catches.jsonl') | Should -Exist
    }

    # -- default RepoRoot resolution -------------------------------------------
    # Every test above passes -RepoRoot explicitly, so the DEFAULT was untested.
    # That default is what policies/peer-review.md actually exercises: it invokes
    # the script via $env:OBI_HOME, and the old "parent of my own tools/ dir"
    # default therefore wrote every catch into obi-tools instead of the repo being
    # worked on. Ten real catches were misfiled that way before it was caught.

    It 'defaults to the git top-level of the current directory' {
        $repo = New-CatchFixture 'gitroot'
        Push-Location $repo
        try {
            & git init --quiet 2>&1 | Out-Null
            $sub = Join-Path $repo 'nested\deeper'
            New-Item -ItemType Directory -Force -Path $sub | Out-Null

            # Run from a SUBDIRECTORY: resolution must walk up to the repo root,
            # not write into the working directory.
            Push-Location $sub
            try {
                & $LogCatch -Repo 'target-repo' -Ref 'gitroot' -Phase review `
                    -Category 'other' -Severity low -Summary 'resolved via git'
            } finally {
                Pop-Location
            }

            (Join-Path (Join-Path $repo '.obi') 'codex-catches.jsonl') | Should -Exist
            (Join-Path (Join-Path $sub '.obi') 'codex-catches.jsonl') | Should -Not -Exist
        } finally {
            Pop-Location
        }
    }

    It 'falls back to the script parent when cwd is not a git repo' {
        $outside = New-CatchFixture 'nogit'
        Push-Location $outside
        try {
            # $TestDrive is not inside a git repo, so resolution must fall back
            # rather than fail. The fallback target is the script's own parent,
            # i.e. the obiwag-agents checkout this test runs from.
            & $LogCatch -Repo 'fallback-repo' -Ref 'nogit' -Phase review `
                -Category 'other' -Severity low -Summary 'fallback path' `
                -DryRun | Should -Not -BeNullOrEmpty
        } finally {
            Pop-Location
        }

        # -DryRun means nothing was written anywhere, including here.
        (Join-Path (Join-Path $outside '.obi') 'codex-catches.jsonl') | Should -Not -Exist
    }

    It 'still honors an explicit -RepoRoot over git discovery' {
        $repo = New-CatchFixture 'explicit'
        $other = New-CatchFixture 'explicit-target'
        Push-Location $repo
        try {
            & git init --quiet 2>&1 | Out-Null
            & $LogCatch -Repo 'r' -Ref 'x' -Phase review `
                -Category 'other' -Severity low -Summary 'explicit wins' `
                -RepoRoot $other
        } finally {
            Pop-Location
        }

        (Join-Path (Join-Path $other '.obi') 'codex-catches.jsonl') | Should -Exist
        (Join-Path (Join-Path $repo '.obi') 'codex-catches.jsonl') | Should -Not -Exist
    }
}

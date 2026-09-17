<# Pester 5 tests for the move-only fresh-run archive helper (DF-01). #>
BeforeAll {

$script:ArchiveScript = Join-Path $PSScriptRoot 'archive-run-state.ps1'
$script:PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$script:RunId = '20260807T180925Z'

function New-ArchiveFixture {
    param([switch]$RootContainsRunId)
    $slug = if ($RootContainsRunId) {
        "archive-repo-$script:RunId-$(Get-Random)"
    } else {
        'archive-repo-' + (Get-Random)
    }
    $root = Join-Path $TestDrive $slug
    New-Item -ItemType Directory -Path (Join-Path $root '.obi\state') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root '.obi\reports') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root '.obi\reviews') -Force | Out-Null
    $script:RunId | Set-Content -LiteralPath (Join-Path $root '.obi\state\run-id.txt') -Encoding ascii
    @{ schema_version = 1; run_id = $script:RunId; per_phase = @{}; per_run = 0 } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root '.obi\state\dispatch-state.json') -Encoding UTF8
    return $root
}

function Invoke-ArchiveChild {
    param([string]$Root, [string]$RequestedRunId = $script:RunId, [switch]$WhatIf)
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script:ArchiveScript,
              '-RunId', $RequestedRunId)
    if ($WhatIf) { $args += '-WhatIf' }
    Push-Location $Root
    try {
        $output = & $script:PowerShellExe @args 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    return [pscustomobject]@{ Code = $code; Output = $output }
}

}

Describe 'archive-run-state.ps1' {
    It 'WhatIf plans the archive without moving or creating anything' {
        $root = New-ArchiveFixture
        $worker = Join-Path $root ".obi\state\worker-$RunId-1-a1.jsonl"
        'stream' | Set-Content -LiteralPath $worker -Encoding UTF8

        $result = Invoke-ArchiveChild -Root $root -WhatIf

        $result.Code | Should -Be 0
        $plan = $result.Output | ConvertFrom-Json
        $plan.status | Should -Be 'what_if'
        @($plan.files.original) | Should -Contain ".obi/state/worker-$RunId-1-a1.jsonl"
        (Test-Path -LiteralPath $worker) | Should -Be $true
        (Test-Path -LiteralPath (Join-Path $root ".obi\archive\state-$RunId")) | Should -Be $false
    }

    It 'moves only active-run and declared generic state with a complete manifest' {
        $root = New-ArchiveFixture
        $state = Join-Path $root '.obi\state'
        $reports = Join-Path $root '.obi\reports'
        $worker = Join-Path $state "worker-$RunId-1-a1.jsonl"
        $attemptStatus = Join-Path $state "dispatch-status-$RunId-1-a1.json"
        $currentStatus = Join-Path $state "dispatch-status-$RunId-1.json"
        $nativeStatus = Join-Path $state "native-phase-$RunId-1.json"
        $statusUpdates = Join-Path $state "status-updates-$RunId.jsonl"
        $taskBase = Join-Path $state "task-base-$RunId.txt"
        $otherWorker = Join-Path $state 'worker-20250101T000000Z-1.jsonl'
        $override = Join-Path $state 'codex-overrides.md'
        $task = Join-Path $state 'task-1.md'
        $marker = Join-Path $state 'phase-1-complete.marker'
        $runReport = Join-Path $reports "$RunId-learning.md"
        $readmeReport = Join-Path $reports "$RunId-readme.md"
        $readmeReview = Join-Path $root ".obi\reviews\$RunId-readme-review.md"
        $oldReport = Join-Path $reports '20250101T000000Z-learning.md'
        $author = Join-Path $reports 'author-report.md'
        $discovery = Join-Path $root '.obi\discovery-report.md'
        foreach ($path in @($worker, $attemptStatus, $currentStatus, $nativeStatus, $statusUpdates, $taskBase, $otherWorker, $override, $task, $marker,
                            $runReport, $readmeReport, $readmeReview, $oldReport, $author, $discovery)) {
            'data' | Set-Content -LiteralPath $path -Encoding UTF8
        }

        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Be 0
        $archive = Join-Path $root ".obi\archive\state-$RunId"
        $manifestPath = Join-Path $archive 'manifest.json'
        (Test-Path -LiteralPath $manifestPath) | Should -Be $true
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $manifest.status | Should -Be 'complete'
        $manifest.run_id | Should -Be $RunId
        @($manifest.files).Count | Should -BeGreaterThan 5
        @($manifest.files.original) | Should -Contain ".obi/state/worker-$RunId-1-a1.jsonl"
        @($manifest.files.original) | Should -Contain ".obi/state/dispatch-status-$RunId-1-a1.json"
        @($manifest.files.original) | Should -Contain ".obi/state/dispatch-status-$RunId-1.json"
        @($manifest.files.original) | Should -Contain ".obi/state/native-phase-$RunId-1.json"
        @($manifest.files.original) | Should -Contain ".obi/state/status-updates-$RunId.jsonl"
        @($manifest.files.original) | Should -Contain ".obi/state/task-base-$RunId.txt"
        @($manifest.files.original) | Should -Contain '.obi/reports/author-report.md'
        @($manifest.files.original) | Should -Contain '.obi/discovery-report.md'
        @($manifest.files.original) | Should -Contain ".obi/reports/$RunId-readme.md"
        @($manifest.files.original) | Should -Contain ".obi/reviews/$RunId-readme-review.md"

        (Test-Path -LiteralPath $worker) | Should -Be $false
        (Test-Path -LiteralPath $statusUpdates) | Should -Be $false
        (Test-Path -LiteralPath $taskBase) | Should -Be $false
        (Test-Path -LiteralPath $task) | Should -Be $false
        (Test-Path -LiteralPath $readmeReport) | Should -Be $false
        (Test-Path -LiteralPath $readmeReview) | Should -Be $false
        (Test-Path -LiteralPath (Join-Path $state 'run-id.txt')) | Should -Be $false
        (Test-Path -LiteralPath $otherWorker) | Should -Be $true
        (Test-Path -LiteralPath $oldReport) | Should -Be $true
        (Test-Path -LiteralPath $override) | Should -Be $true
    }

    It 'preserves artifacts whose identifier only extends the active RunId' {
        $root = New-ArchiveFixture
        $state = Join-Path $root '.obi\state'
        $owned = Join-Path $state "worker-$RunId-1.jsonl"
        $longer = Join-Path $state "worker-$RunId-extra-1.jsonl"
        'owned' | Set-Content -LiteralPath $owned -Encoding UTF8
        'unrelated' | Set-Content -LiteralPath $longer -Encoding UTF8

        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Be 0
        (Test-Path -LiteralPath $owned) | Should -Be $false
        (Test-Path -LiteralPath $longer) | Should -Be $true
    }

    It 'does not infer ownership from a checkout ancestor containing the RunId' {
        $root = New-ArchiveFixture -RootContainsRunId
        $state = Join-Path $root '.obi\state'
        $owned = Join-Path $state "worker-$RunId-1.jsonl"
        $override = Join-Path $state 'codex-overrides.md'
        'owned' | Set-Content -LiteralPath $owned -Encoding UTF8
        'keep' | Set-Content -LiteralPath $override -Encoding UTF8

        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Be 0
        (Test-Path -LiteralPath $owned) | Should -Be $false
        (Test-Path -LiteralPath $override) | Should -Be $true
    }

    It 'refuses a requested RunId that does not match active state' {
        $root = New-ArchiveFixture
        $result = Invoke-ArchiveChild -Root $root -RequestedRunId '20250101T000000Z'

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'RunId mismatch'
        (Test-Path -LiteralPath (Join-Path $root '.obi\state\run-id.txt')) | Should -Be $true
    }

    It 'refuses a dispatch-state RunId mismatch before moving anything' {
        $root = New-ArchiveFixture
        @{ schema_version = 1; run_id = '20250101T000000Z' } | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $root '.obi\state\dispatch-state.json') -Encoding UTF8
        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'dispatch-state.json RunId does not match'
        (Test-Path -LiteralPath (Join-Path $root '.obi\state\run-id.txt')) | Should -Be $true
    }

    It 'blocks on an open lifecycle tracker before moving any state' {
        $root = New-ArchiveFixture
        $state = Join-Path $root '.obi\state'
        $tracker = Join-Path $root ".obi\reports\$RunId-dogfood-failures.md"
        $worker = Join-Path $state "worker-$RunId-1.jsonl"
        "# Ledger`n`nStatus: OPEN`n" | Set-Content -LiteralPath $tracker -Encoding UTF8
        'stream' | Set-Content -LiteralPath $worker -Encoding UTF8

        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'blocked_open_trackers'
        $result.Output | Should -Match ([regex]::Escape(".obi/reports/$RunId-dogfood-failures.md"))
        (Test-Path -LiteralPath $tracker) | Should -Be $true
        (Test-Path -LiteralPath $worker) | Should -Be $true
        (Test-Path -LiteralPath (Join-Path $state 'run-id.txt')) | Should -Be $true
        (Test-Path -LiteralPath (Join-Path $root ".obi\archive\state-$RunId")) | Should -Be $false
    }

    It 'archives a lifecycle tracker after its exact status line is complete' {
        $root = New-ArchiveFixture
        $tracker = Join-Path $root ".obi\reports\$RunId-dogfood-failures.md"
        "# Ledger`n`nStatus: COMPLETE`n" | Set-Content -LiteralPath $tracker -Encoding UTF8

        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Be 0
        (Test-Path -LiteralPath $tracker) | Should -Be $false
        (Test-Path -LiteralPath (Join-Path $root ".obi\archive\state-$RunId\reports\$RunId-dogfood-failures.md")) |
            Should -Be $true
    }

    It 'refuses an existing archive destination collision' {
        $root = New-ArchiveFixture
        New-Item -ItemType Directory -Path (Join-Path $root ".obi\archive\state-$RunId") -Force | Out-Null
        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'Archive destination already exists'
        (Test-Path -LiteralPath (Join-Path $root '.obi\state\run-id.txt')) | Should -Be $true
    }

    It 'refuses a junctioned archive parent before moving active state' {
        $root = New-ArchiveFixture
        $outside = Join-Path $TestDrive ('outside-archive-' + (Get-Random))
        New-Item -ItemType Directory -Path $outside -Force | Out-Null
        $archiveLink = Join-Path $root '.obi\archive'
        New-Item -ItemType Junction -Path $archiveLink -Target $outside | Out-Null
        $worker = Join-Path $root ".obi\state\worker-$RunId-1.jsonl"
        'stream' | Set-Content -LiteralPath $worker -Encoding UTF8

        $result = Invoke-ArchiveChild -Root $root

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'reparse-point archive path component'
        (Test-Path -LiteralPath (Join-Path $root '.obi\state\run-id.txt')) | Should -Be $true
        (Test-Path -LiteralPath $worker) | Should -Be $true
        (Test-Path -LiteralPath (Join-Path $outside "state-$RunId")) | Should -Be $false
    }
}

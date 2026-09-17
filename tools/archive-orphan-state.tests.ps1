<# Pester 5 tests for the reversible orphan-state audit/archive helper. #>
BeforeAll {

$script:ArchiveScript = Join-Path $PSScriptRoot 'archive-orphan-state.ps1'
$script:PowerShellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
$script:ActiveRunId = '20260810T122115Z'
$script:OldRunId = '20260803T153014Z'

function New-OrphanFixture {
    $root = Join-Path $TestDrive ('orphan-repo-' + (Get-Random))
    foreach ($relative in @('.obi\state', '.obi\reports', '.obi\reviews', '.obi\runtime',
                             '.obi\review\runs', '.obi\archive')) {
        New-Item -ItemType Directory -Path (Join-Path $root $relative) -Force | Out-Null
    }
    $script:ActiveRunId | Set-Content -LiteralPath (Join-Path $root '.obi\state\run-id.txt') -Encoding ascii
    return $root
}

function Set-OldArtifact {
    param([string]$Path, [string]$Content = 'old')
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $Content | Set-Content -LiteralPath $Path -Encoding UTF8
    (Get-Item -LiteralPath $Path).LastWriteTimeUtc = [datetime]::UtcNow.AddHours(-48)
}

function Invoke-OrphanChild {
    param(
        [string]$Root,
        [ValidateSet('Audit', 'Archive')][string]$Mode = 'Audit',
        [string]$RunId
    )
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $script:ArchiveScript,
              '-Mode', $Mode, '-MinimumAgeHours', '1')
    if ($RunId) { $args += @('-RunId', $RunId) }
    Push-Location $Root
    try {
        $output = & $script:PowerShellExe @args 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally { Pop-Location }
    return [pscustomobject]@{ Code = $code; Output = $output }
}

}

Describe 'archive-orphan-state.ps1' {
    It 'audits eligible artifacts without moving or creating anything' {
        $root = New-OrphanFixture
        $worker = Join-Path $root ".obi\state\worker-$OldRunId-1-a1.jsonl"
        Set-OldArtifact -Path $worker

        $result = Invoke-OrphanChild -Root $root -Mode Audit

        $result.Code | Should -Be 0
        $audit = $result.Output | ConvertFrom-Json
        $audit.status | Should -Be 'audit_complete'
        @($audit.eligible_run_ids) | Should -Contain $OldRunId
        (Test-Path -LiteralPath $worker) | Should -Be $true
        @(Get-ChildItem -LiteralPath (Join-Path $root '.obi\archive') -Force).Count | Should -Be 0
    }

    It 'never marks the active RunId eligible' {
        $root = New-OrphanFixture
        $worker = Join-Path $root ".obi\state\worker-$ActiveRunId-1-a1.jsonl"
        Set-OldArtifact -Path $worker

        $audit = (Invoke-OrphanChild -Root $root -Mode Audit).Output | ConvertFrom-Json
        $group = @($audit.groups | Where-Object run_id -eq $ActiveRunId)[0]

        $group.classification | Should -Be 'active'
        $group.eligible | Should -Be $false
    }

    It 'fails closed on a malformed active RunId before moving an orphan' {
        $root = New-OrphanFixture
        'not a valid run id' | Set-Content -LiteralPath (Join-Path $root '.obi\state\run-id.txt') -Encoding ascii
        $worker = Join-Path $root ".obi\state\worker-$OldRunId-1.jsonl"
        Set-OldArtifact -Path $worker

        $result = Invoke-OrphanChild -Root $root -Mode Archive -RunId $OldRunId

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'active RunId'
        (Test-Path -LiteralPath $worker) | Should -Be $true
        @(Get-ChildItem -LiteralPath (Join-Path $root '.obi\archive') -Force).Count | Should -Be 0
    }

    It 'excludes a running worker whose process and deadline are live' {
        $root = New-OrphanFixture
        $statusPath = Join-Path $root ".obi\state\dispatch-status-$OldRunId-1.json"
        $status = @{ status = 'running'; proc_pid = $PID; deadline_at = [datetime]::UtcNow.AddHours(1).ToString('o') }
        Set-OldArtifact -Path $statusPath -Content ($status | ConvertTo-Json)

        $audit = (Invoke-OrphanChild -Root $root -Mode Audit).Output | ConvertFrom-Json
        $group = @($audit.groups | Where-Object run_id -eq $OldRunId)[0]

        $group.classification | Should -Be 'live'
        $group.eligible | Should -Be $false
    }

    It 'keeps a recognized group that is newer than the minimum age' {
        $root = New-OrphanFixture
        $worker = Join-Path $root ".obi\state\worker-$OldRunId-1.jsonl"
        'recent' | Set-Content -LiteralPath $worker -Encoding UTF8

        $audit = (Invoke-OrphanChild -Root $root -Mode Audit).Output | ConvertFrom-Json
        $group = @($audit.groups | Where-Object run_id -eq $OldRunId)[0]

        $group.classification | Should -Be 'too_recent'
        $group.eligible | Should -Be $false
    }

    It 'groups README phase and review artifacts under the exact RunId' {
        $root = New-OrphanFixture
        $readmeReport = Join-Path $root ".obi\reports\$OldRunId-readme.md"
        $readmeReview = Join-Path $root ".obi\reviews\$OldRunId-readme-review.md"
        Set-OldArtifact -Path $readmeReport
        Set-OldArtifact -Path $readmeReview

        $audit = (Invoke-OrphanChild -Root $root -Mode Audit).Output | ConvertFrom-Json
        $group = @($audit.groups | Where-Object run_id -eq $OldRunId)[0]

        @($audit.groups.run_id) | Should -Contain $OldRunId
        @($audit.groups.run_id) | Should -Not -Contain "$OldRunId-readme"
        @($group.files) | Should -Contain ".obi/reports/$OldRunId-readme.md"
        @($group.files) | Should -Contain ".obi/reviews/$OldRunId-readme-review.md"
        @($audit.eligible_run_ids) | Should -Contain $OldRunId
    }

    It 'archives an eligible family with a complete reversible manifest' {
        $root = New-OrphanFixture
        $worker = Join-Path $root ".obi\state\worker-$OldRunId-1-a1.jsonl"
        $summary = Join-Path $root ".obi\state\worker-summary-$OldRunId-1-a1.json"
        $currentSummary = Join-Path $root ".obi\state\worker-summary-$OldRunId-1.json"
        $nativeStatus = Join-Path $root ".obi\state\native-phase-$OldRunId-1.json"
        $statusUpdates = Join-Path $root ".obi\state\status-updates-$OldRunId.jsonl"
        $taskBase = Join-Path $root ".obi\state\task-base-$OldRunId.txt"
        Set-OldArtifact -Path $worker
        Set-OldArtifact -Path $summary -Content '{"status":"killed"}'
        Set-OldArtifact -Path $currentSummary -Content '{"status":"killed"}'
        Set-OldArtifact -Path $nativeStatus -Content '{"terminal_state":"native_completion_stalled"}'
        Set-OldArtifact -Path $statusUpdates -Content '{"run_id":"old","disposition":"continue"}'
        Set-OldArtifact -Path $taskBase -Content '0123456789012345678901234567890123456789'

        $result = Invoke-OrphanChild -Root $root -Mode Archive -RunId $OldRunId

        $result.Code | Should -Be 0
        $archive = Join-Path $root ".obi\archive\orphan-state-$OldRunId"
        $manifest = Get-Content -LiteralPath (Join-Path $archive 'manifest.json') -Raw | ConvertFrom-Json
        $manifest.status | Should -Be 'complete'
        $manifest.run_id | Should -Be $OldRunId
        @($manifest.files.original) | Should -Contain ".obi/state/worker-$OldRunId-1-a1.jsonl"
        @($manifest.files.original) | Should -Contain ".obi/state/worker-summary-$OldRunId-1-a1.json"
        @($manifest.files.original) | Should -Contain ".obi/state/native-phase-$OldRunId-1.json"
        @($manifest.files.original) | Should -Contain ".obi/state/status-updates-$OldRunId.jsonl"
        @($manifest.files.original) | Should -Contain ".obi/state/task-base-$OldRunId.txt"
        (Test-Path -LiteralPath $worker) | Should -Be $false
        (Test-Path -LiteralPath $statusUpdates) | Should -Be $false
        (Test-Path -LiteralPath $taskBase) | Should -Be $false
        (Test-Path -LiteralPath (Join-Path $archive "state\worker-$OldRunId-1-a1.jsonl")) | Should -Be $true
    }

    It 'preserves unknown files while archiving recognized siblings' {
        $root = New-OrphanFixture
        $worker = Join-Path $root ".obi\state\worker-$OldRunId-1.jsonl"
        $unknown = Join-Path $root ".obi\state\operator-notes-$OldRunId.txt"
        Set-OldArtifact -Path $worker
        Set-OldArtifact -Path $unknown -Content 'keep me'

        $result = Invoke-OrphanChild -Root $root -Mode Archive -RunId $OldRunId

        $result.Code | Should -Be 0
        (Test-Path -LiteralPath $unknown) | Should -Be $true
    }

    It 'skips reparse-point trees and leaves their content untouched' {
        $root = New-OrphanFixture
        $outside = Join-Path $TestDrive ('orphan-outside-' + (Get-Random))
        New-Item -ItemType Directory -Path $outside -Force | Out-Null
        $outsideWorker = Join-Path $outside "worker-$OldRunId-1.jsonl"
        Set-OldArtifact -Path $outsideWorker
        $link = Join-Path $root '.obi\state\linked'
        New-Item -ItemType Junction -Path $link -Target $outside | Out-Null

        $audit = (Invoke-OrphanChild -Root $root -Mode Audit).Output | ConvertFrom-Json

        @($audit.unsafe_paths).Count | Should -BeGreaterThan 0
        @($audit.eligible_run_ids) | Should -Not -Contain $OldRunId
        (Test-Path -LiteralPath $outsideWorker) | Should -Be $true
    }

    It 'preflights destination collisions before moving a source file' {
        $root = New-OrphanFixture
        $worker = Join-Path $root ".obi\state\worker-$OldRunId-1.jsonl"
        Set-OldArtifact -Path $worker
        New-Item -ItemType Directory -Path (Join-Path $root ".obi\archive\orphan-state-$OldRunId") -Force | Out-Null

        $result = Invoke-OrphanChild -Root $root -Mode Archive -RunId $OldRunId

        $result.Code | Should -Not -Be 0
        $result.Output | Should -Match 'destination already exists'
        (Test-Path -LiteralPath $worker) | Should -Be $true
    }
}

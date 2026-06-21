<#
.SYNOPSIS
    Pester 3.4 tests for tools/dispatch-watchdog.ps1 (OPT-22 stall watchdog).
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Watchdog  = Join-Path $ScriptDir 'dispatch-watchdog.ps1'

function New-WatchdogFixture {
    <#
    .SYNOPSIS Helper: create .obi/state with a heartbeat file in a temp dir.
    #>
    param([string]$Slug)
    $root = Join-Path $TestDrive "wd-$Slug-$(Get-Random)"
    $state = Join-Path (Join-Path $root '.obi') 'state'
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    return $root
}

function Write-Heartbeat {
    param([string]$Root, [string]$RunId, [int]$AgeSeconds = 0)
    $state = Join-Path (Join-Path $Root '.obi') 'state'
    $hbPath = Join-Path $state "heartbeat-${RunId}.json"
    $ts = [datetime]::UtcNow.AddSeconds(-$AgeSeconds)
    @{ ts = $ts.ToString('o'); tool = 'Bash' } | ConvertTo-Json | Set-Content -Path $hbPath -Encoding UTF8
    # Backdate the file mtime so staleness detection works
    (Get-Item $hbPath).LastWriteTimeUtc = $ts
}

Describe 'dispatch-watchdog.ps1' {

    It 'writes stall marker when heartbeat is frozen past threshold' {
        $root = New-WatchdogFixture 'frozen'
        $runId = 'test-frozen'
        # Create a heartbeat that is already 120 seconds old
        Write-Heartbeat -Root $root -RunId $runId -AgeSeconds 120

        # Run watchdog with tiny threshold (0.01 min = 0.6s) and tiny ceiling (0.02 min = 1.2s)
        # PollSeconds=1 so it checks quickly
        Push-Location $root
        try {
            & $Watchdog -RunId $runId -Phase 2 -ThresholdMinutes 0.01 -CeilingMinutes 0.05 -PollSeconds 1
        } finally {
            Pop-Location
        }

        $marker = Join-Path (Join-Path (Join-Path $root '.obi') 'state') 'stall-2.marker'
        $marker | Should Exist
        $data = Get-Content $marker -Raw | ConvertFrom-Json
        $data.phase | Should Be 2
        $data.detected_at | Should Not BeNullOrEmpty
        $data.last_heartbeat | Should Not BeNullOrEmpty
    }

    It 'self-terminates when completion marker appears' {
        $root = New-WatchdogFixture 'complete'
        $runId = 'test-complete'
        # Fresh heartbeat — no stall
        Write-Heartbeat -Root $root -RunId $runId -AgeSeconds 0

        # Pre-create the completion marker so watchdog exits on first poll
        $completionPath = Join-Path (Join-Path (Join-Path $root '.obi') 'state') 'phase-5-complete.marker'
        Set-Content -Path $completionPath -Value 'done' -Encoding UTF8

        Push-Location $root
        try {
            & $Watchdog -RunId $runId -Phase 5 -ThresholdMinutes 0.01 -CeilingMinutes 0.05 -PollSeconds 1
        } finally {
            Pop-Location
        }

        # Should exit without writing a stall marker
        $stallMarker = Join-Path (Join-Path (Join-Path $root '.obi') 'state') 'stall-5.marker'
        $stallMarker | Should Not Exist
    }

    It 'self-terminates when heartbeat resumes after stall' {
        $root = New-WatchdogFixture 'resume'
        $runId = 'test-resume'
        # Stale heartbeat triggers alert on first poll
        Write-Heartbeat -Root $root -RunId $runId -AgeSeconds 300

        # Run watchdog in a background job so we can refresh the heartbeat
        Push-Location $root
        try {
            # ThresholdMinutes=0.5 (30s) so a fresh heartbeat (age ~0s) is clearly
            # below threshold, while the initial 300s-old heartbeat trips it.
            $job = Start-Job -ScriptBlock {
                param($wd, $root, $runId)
                Set-Location $root
                & $wd -RunId $runId -Phase 3 -ThresholdMinutes 0.5 -CeilingMinutes 2 -PollSeconds 1
            } -ArgumentList $Watchdog, $root, $runId

            # Wait for the stall marker to appear (proves watchdog detected the stall)
            $stallMarker = Join-Path (Join-Path (Join-Path $root '.obi') 'state') 'stall-3.marker'
            $deadline = [datetime]::UtcNow.AddSeconds(20)
            while (-not (Test-Path $stallMarker) -and [datetime]::UtcNow -lt $deadline) {
                Start-Sleep -Milliseconds 500
            }
            $stallMarker | Should Exist

            # Now refresh the heartbeat — watchdog should see resume and exit
            Write-Heartbeat -Root $root -RunId $runId -AgeSeconds 0

            # Wait for job to finish (poll=1s so at most ~2 polls after refresh)
            $null = Wait-Job $job -Timeout 30
            $job.State | Should Be 'Completed'

            # Stall marker should be cleaned up
            $stallMarker | Should Not Exist
        } finally {
            if ($job) { Remove-Job $job -Force -ErrorAction SilentlyContinue }
            Pop-Location
        }
    }

    It 'self-terminates at hard ceiling' {
        $root = New-WatchdogFixture 'ceiling'
        $runId = 'test-ceiling'
        # No heartbeat file at all — watchdog keeps waiting but hits ceiling

        Push-Location $root
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        try {
            & $Watchdog -RunId $runId -Phase 1 -ThresholdMinutes 999 -CeilingMinutes 0.02 -PollSeconds 1
        } finally {
            Pop-Location
        }
        $sw.Stop()

        # Should have exited within a few seconds (ceiling 0.02 min ~ 1.2s + 1 poll)
        $sw.Elapsed.TotalSeconds | Should BeLessThan 10
    }

    It 'stall marker JSON has correct structure' {
        $root = New-WatchdogFixture 'structure'
        $runId = 'test-structure'
        Write-Heartbeat -Root $root -RunId $runId -AgeSeconds 600

        Push-Location $root
        try {
            & $Watchdog -RunId $runId -Phase 7 -ThresholdMinutes 0.01 -CeilingMinutes 0.05 -PollSeconds 1
        } finally {
            Pop-Location
        }

        $marker = Join-Path (Join-Path (Join-Path $root '.obi') 'state') 'stall-7.marker'
        $marker | Should Exist
        $data = Get-Content $marker -Raw | ConvertFrom-Json
        # Verify all required fields
        ($data | Get-Member -Name 'phase' -MemberType NoteProperty) | Should Not BeNullOrEmpty
        ($data | Get-Member -Name 'detected_at' -MemberType NoteProperty) | Should Not BeNullOrEmpty
        ($data | Get-Member -Name 'last_heartbeat' -MemberType NoteProperty) | Should Not BeNullOrEmpty
        $data.phase | Should Be 7
    }
}

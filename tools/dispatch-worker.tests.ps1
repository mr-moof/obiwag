<#
.SYNOPSIS
    Pester 5 tests for tools/dispatch-worker.ps1 -- the bounded supervisor (issue #200 SS1).

.DESCRIPTION
    `claude` is mocked via a claude.cmd stub in a stub dir prepended to PATH. The supervisor is
    invoked as a child powershell.exe -File so its
    Start-Process / watchdog / scoped-kill mechanics run for real; results are observed via the
    status record and summary file it writes (not $LASTEXITCODE, which Pester / PS 5.1 do not
    reliably propagate from a call-operator script).

    Coverage:
      1. completed run       -> status completed, signal found, session id recorded, summary written
      2. hard-deadline hang  -> watchdog kills the tree, verifies it, status timed_out + resumable
      3. concurrency = 1      -> a second worker for the same run is refused while one is live
#>
BeforeAll {

$ScriptPath = Join-Path $PSScriptRoot 'dispatch-worker.ps1'

}

Describe 'dispatch-worker supervisor' {

    BeforeEach {
        $script:OldPath = $env:PATH
        $script:StubDir = Join-Path $TestDrive ('stubs-' + (Get-Random))
        New-Item -ItemType Directory -Path $StubDir -Force | Out-Null
        $env:PATH = $StubDir + ';' + $env:PATH

        $script:WorkDir = Join-Path $TestDrive ('wd-' + (Get-Random))
        $script:StateDir = Join-Path $WorkDir '.obi\state'
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null

        $script:PersonaFile = Join-Path $WorkDir 'persona.md'
        @'
---
name: obi-test
tools: Read, Grep
---

Test persona. Output DISCOVERY COMPLETE when done.
'@ | Set-Content $PersonaFile -Encoding UTF8

        $script:PromptFile = Join-Path $WorkDir 'task.md'
        'do the task' | Set-Content $PromptFile -Encoding UTF8

        $script:OutFile = Join-Path $StateDir 'worker-t-1.jsonl'
        $script:StatusFile = Join-Path $StateDir 'dispatch-status-t-1.json'
        $script:SummaryFile = Join-Path $StateDir 'worker-summary-t-1.json'
        $script:AttemptStatusFile = Join-Path $StateDir 'dispatch-status-t-1-a1.json'
        $script:AttemptSummaryFile = Join-Path $StateDir 'worker-summary-t-1-a1.json'
    }

    AfterEach { $env:PATH = $script:OldPath }

    # In BeforeAll because a function defined directly in a Describe body is created
    # during Pester 5's DISCOVERY pass and is not available when It bodies run.
    BeforeAll {
        function Set-ClaudeStub { param([string]$Body) Set-Content (Join-Path $StubDir 'claude.cmd') -Value $Body -Encoding ascii }

        function Invoke-Supervisor {
            param([string[]]$Extra, [switch]$UseDefaultOutFile)
            $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath,
                   '-Persona', 'obi-test', '-PersonaPath', $PersonaFile, '-PromptFile', $PromptFile,
                   '-RunId', 't', '-Phase', '1', '-ExpectSignal', 'DISCOVERY COMPLETE')
            if (-not $UseDefaultOutFile) { $a += @('-OutFile', $OutFile) }
            $a += $Extra
            Push-Location $WorkDir
            try { & powershell.exe @a *> $null }
            finally { Pop-Location }
        }
    }

    It 'completed run -> status completed, signal found, session recorded, summary written' {
        Set-ClaudeStub @'
@echo off
echo {"type":"system","subtype":"init","session_id":"stub-sid"}
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE stub done","is_error":false,"session_id":"stub-sid"}
'@
        Invoke-Supervisor -Extra @('-TimeoutSec', '30', '-IdleTimeoutSec', '15', '-PollSec', '1')

        (Test-Path $StatusFile) | Should -Be $true
        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status       | Should -Be 'completed'
        $st.is_error     | Should -Be $false
        $st.signal_found | Should -Be $true
        $st.session_id   | Should -Be 'stub-sid'
        $st.resumable    | Should -Be $false

        # result_preview must survive to the JSON (it is a real key, not an Add-Member note prop).
        $st.result_preview | Should -Match 'DISCOVERY COMPLETE'

        (Test-Path $SummaryFile) | Should -Be $true
        $sum = Get-Content $SummaryFile -Raw | ConvertFrom-Json
        $sum.status | Should -Be 'completed'
        $sum.result_preview | Should -Match 'DISCOVERY COMPLETE'
        $sum.status_file | Should -Be $StatusFile
        $sum.attempt_status_file | Should -Be $AttemptStatusFile
        (Test-Path $AttemptStatusFile) | Should -Be $true
        (Test-Path $AttemptSummaryFile) | Should -Be $true
    }

    It 'clean result without the expected signal is a resumable error, not completed' {
        Set-ClaudeStub @'
@echo off
echo {"type":"system","subtype":"init","session_id":"missing-signal-sid"}
echo {"type":"result","subtype":"success","result":"finished without canonical signal","is_error":false,"session_id":"missing-signal-sid"}
'@
        Invoke-Supervisor -Extra @('-TimeoutSec', '30', '-IdleTimeoutSec', '15', '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'error'
        $st.is_error | Should -Be $true
        $st.signal_found | Should -Be $false
        $st.resumable | Should -Be $true
        $st.session_id | Should -Be 'missing-signal-sid'
    }

    It 'hard-deadline hang -> tree killed + verified, status timed_out + resumable' {
        Set-ClaudeStub @'
@echo off
ping -n 30 127.0.0.1 >nul
'@
        # PollSec intentionally exceeds TimeoutSec: the wait itself must be clipped to the hard
        # deadline, or a late successful result could be accepted.
        Invoke-Supervisor -Extra @('-TimeoutSec', '2', '-IdleTimeoutSec', '30', '-PollSec', '10')

        (Test-Path $StatusFile) | Should -Be $true
        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status        | Should -Be 'timed_out'
        $st.timed_out     | Should -Be $true
        $st.is_error      | Should -Be $true
        $st.kill_verified | Should -Be $true
        # A session id was assigned up front, so a killed partial run is resumable.
        $st.resumable     | Should -Be $true
        $st.session_id    | Should -Not -BeNullOrEmpty
        ([datetime]$st.last_progress_at) | Should -BeLessThan ([datetime]$st.deadline_at)
        ([datetime]$st.finished_at) | Should -BeGreaterThan ([datetime]$st.deadline_at)
    }

    It 'productive checkpoint growth receives one extension and still stops at the absolute cap' {
        $reports = Join-Path $WorkDir '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $checkpoint = Join-Path $reports 'productive.md'
        $phaseTable = Join-Path $WorkDir 'phase-table.json'
        @{
            efficiency = @{ routing = @{ tiers = @{ strong = @{ claude = @{
                model = 'fable[1m]'; effort = 'xhigh'
            } } } } }
            phases = @(@{
                n = 1
                delegation_policy = @{ claude = @{
                    nominal_work_sec = 8; idle_sec = 20; productive_extension_sec = 2
                    finalization_sec = 3; absolute_sec = 13; recent_progress_sec = 5
                    cleanup_margin_sec = 1
                } }
            })
        } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $phaseTable -Encoding UTF8
        Set-ClaudeStub @"
@echo off
ping -n 4 127.0.0.1 >nul
echo first>"$checkpoint"
:again
echo {"type":"assistant","message":{"role":"assistant"}}
ping -n 2 127.0.0.1 >nul
echo more>>"$checkpoint"
goto again
"@

        Invoke-Supervisor -Extra @('-CheckpointPath', $checkpoint, '-PhaseTablePath', $phaseTable,
                                    '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'timed_out'
        $st.kill_reason | Should -Be 'timed_out'
        $st.kill_verified | Should -Be $true
        $st.extension_decision | Should -Be 'granted'
        $st.extension_duration_sec | Should -Be 2
        $st.progress_at_cutoff.checkpoint_grew | Should -Be $true
        $st.finalization_started_at | Should -Not -BeNullOrEmpty
        $st.final_duration_sec | Should -BeGreaterOrEqual 13
        $st.final_duration_sec | Should -BeLessThan 17
        $st.outer_timeout_sec | Should -Be 14
        (Get-Content $st.system_file -Raw) | Should -Match 'Stop all tool calls no later than 10 seconds'
    }

    It 'no-output no-CPU run -> idle watchdog kills it before the hard deadline' {
        Set-ClaudeStub @'
@echo off
ping -n 30 127.0.0.1 >nul
'@
        Invoke-Supervisor -Extra @('-TimeoutSec', '20', '-IdleTimeoutSec', '3', '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status        | Should -Be 'killed'
        $st.kill_reason   | Should -Be 'hung'
        $st.timed_out     | Should -Be $false
        $st.kill_verified | Should -Be $true
    }

    It 'artifact-only progress near the idle boundary is observed before idle termination' {
        $reports = Join-Path $WorkDir '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $checkpoint = Join-Path $reports 'artifact-only.md'
        Set-ClaudeStub @"
@echo off
ping -n 4 127.0.0.1 >nul
echo artifact>"$checkpoint"
ping -n 3 127.0.0.1 >nul
echo {"type":"system","subtype":"init","session_id":"artifact-only-sid"}
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE artifact only","is_error":false,"session_id":"artifact-only-sid"}
"@
        # Leave one scheduler second: probe ordering is under test, not ping's exact timer.
        Invoke-Supervisor -Extra @('-CheckpointPath', $checkpoint, '-TimeoutSec', '15',
                                    '-IdleTimeoutSec', '4', '-PollSec', '2')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'completed'
        $st.kill_reason | Should -BeNullOrEmpty
        $st.checkpoint_health | Should -Be 'healthy'
        $st.artifact_progress_count | Should -BeGreaterThan 0
    }

    It 'dispatch key isolates filenames while status retains the canonical phase' {
        $reports = Join-Path $WorkDir '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $checkpoint = Join-Path $reports 'chunk.md'
        'ok' | Set-Content -LiteralPath $checkpoint -Encoding UTF8

        Set-ClaudeStub @'
@echo off
echo {"type":"system","subtype":"init","session_id":"keyed-sid"}
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE keyed","is_error":false,"session_id":"keyed-sid"}
'@
        Invoke-Supervisor -Extra @('-DispatchKey', '1c2', '-CheckpointPath', $checkpoint,
                                    '-TimeoutSec', '30', '-IdleTimeoutSec', '15', '-PollSec', '1')

        $keyedStatus = Join-Path $StateDir 'dispatch-status-t-1c2.json'
        $keyedSummary = Join-Path $StateDir 'worker-summary-t-1c2.json'
        (Test-Path $keyedStatus) | Should -Be $true
        (Test-Path $keyedSummary) | Should -Be $true
        $st = Get-Content $keyedStatus -Raw | ConvertFrom-Json
        $st.status | Should -Be 'completed'
        $st.phase | Should -Be 1
        $st.dispatch_key | Should -Be '1c2'
        $st.checkpoint.path | Should -Be ([System.IO.Path]::GetFullPath($checkpoint))
        $st.checkpoint.bytes | Should -BeGreaterThan 0
        $st.checkpoint.bytes | Should -BeLessThan 1025
        $st.checkpoint_health | Should -Be 'healthy'
        $st.finished_at | Should -Not -BeNullOrEmpty
    }

    It 'stream-active missing checkpoint is advisory until the hard deadline' {
        $reports = Join-Path $WorkDir '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $checkpoint = Join-Path $reports 'missing.md'
        Set-ClaudeStub @'
@echo off
:again
echo {"type":"assistant","message":{"role":"assistant"}}
ping -n 2 127.0.0.1 >nul
goto again
'@
        Invoke-Supervisor -Extra @('-CheckpointPath', $checkpoint, '-TimeoutSec', '8',
                                    '-IdleTimeoutSec', '7', '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'timed_out'
        $st.kill_reason | Should -Be 'timed_out'
        $st.checkpoint_health | Should -Be 'checkpoint_missing'
        $st.failure_reason | Should -BeNullOrEmpty
        $st.timed_out | Should -Be $true
        $st.kill_verified | Should -Be $true
        $st.resumable | Should -Be $true
        $st.checkpoint.bytes | Should -Be 0
        ([datetime]$st.last_progress_at) | Should -BeLessThan ([datetime]$st.deadline_at)
        ([datetime]$st.finished_at) | Should -BeGreaterThan ([datetime]$st.last_progress_at)
    }

    It 'stream-active frozen checkpoint is advisory until the hard deadline' {
        $reports = Join-Path $WorkDir '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $checkpoint = Join-Path $reports 'frozen.md'
        (('x' * 1200) -join '') | Set-Content -LiteralPath $checkpoint -Encoding UTF8
        Set-ClaudeStub @'
@echo off
:again
echo {"type":"assistant","message":{"role":"assistant"}}
ping -n 2 127.0.0.1 >nul
goto again
'@
        Invoke-Supervisor -Extra @('-CheckpointPath', $checkpoint, '-TimeoutSec', '8',
                                    '-IdleTimeoutSec', '7', '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'timed_out'
        $st.kill_reason | Should -Be 'timed_out'
        $st.checkpoint_health | Should -Be 'artifact_stalled'
        $st.kill_verified | Should -Be $true
        $st.checkpoint.bytes | Should -BeGreaterThan 1024
        ([datetime]$st.last_progress_at) | Should -BeLessThan ([datetime]$st.deadline_at)
        ([datetime]$st.finished_at) | Should -BeGreaterThan ([datetime]$st.last_progress_at)
    }

    It 'clean result without a required artifact is a resumable error, not a kill' {
        $reports = Join-Path $WorkDir '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $checkpoint = Join-Path $reports 'never-written.md'
        Set-ClaudeStub @'
@echo off
echo {"type":"system","subtype":"init","session_id":"no-artifact-sid"}
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE no artifact","is_error":false,"session_id":"no-artifact-sid"}
'@
        Invoke-Supervisor -Extra @('-CheckpointPath', $checkpoint, '-TimeoutSec', '30',
                                    '-IdleTimeoutSec', '15', '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'error'
        $st.failure_reason | Should -Be 'checkpoint_missing'
        $st.kill_reason | Should -BeNullOrEmpty
        $st.kill_verified | Should -BeNullOrEmpty
        $st.timed_out | Should -Be $false
        $st.resumable | Should -Be $true
        $st.checkpoint_health | Should -Be 'checkpoint_missing'
    }

    It 'resume preserves immutable attempt streams, statuses, summaries, and system prompts' {
        Set-ClaudeStub @'
@echo off
echo {"type":"system","subtype":"init","session_id":"attempt-sid"}
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE attempt","is_error":false,"session_id":"attempt-sid"}
'@
        Invoke-Supervisor -UseDefaultOutFile -Extra @('-TimeoutSec', '30', '-IdleTimeoutSec', '15', '-PollSec', '1')
        Invoke-Supervisor -UseDefaultOutFile -Extra @('-ResumeSessionId', 'attempt-sid', '-Attempt', '2',
                                                       '-TimeoutSec', '30', '-IdleTimeoutSec', '15', '-PollSec', '1')

        $a1Status = Join-Path $StateDir 'dispatch-status-t-1-a1.json'
        $a2Status = Join-Path $StateDir 'dispatch-status-t-1-a2.json'
        $a1Summary = Join-Path $StateDir 'worker-summary-t-1-a1.json'
        $a2Summary = Join-Path $StateDir 'worker-summary-t-1-a2.json'
        foreach ($path in @(
            (Join-Path $StateDir 'worker-t-1-a1.jsonl'),
            (Join-Path $StateDir 'worker-t-1-a2.jsonl'),
            (Join-Path $StateDir 'worker-sys-t-1-a1.txt'),
            (Join-Path $StateDir 'worker-sys-t-1-a2.txt'),
            $a1Status, $a2Status, $a1Summary, $a2Summary, $StatusFile, $SummaryFile
        )) { (Test-Path -LiteralPath $path) | Should -Be $true }

        (Get-Content $a1Status -Raw | ConvertFrom-Json).attempt | Should -Be 1
        (Get-Content $a2Status -Raw | ConvertFrom-Json).attempt | Should -Be 2
        (Get-Content $StatusFile -Raw | ConvertFrom-Json).attempt | Should -Be 2
        (Get-Content $SummaryFile -Raw | ConvertFrom-Json).attempt_status_file | Should -Be $a2Status
    }

    It 'pre-launch failure (missing prompt) still records a launch_error terminal status' {
        # Opus review #1: a throw before the first status write must NOT leave the orchestrator
        # with no status. A bad prompt path records launch_error, not nothing.
        Set-ClaudeStub @'
@echo off
echo {"type":"result","result":"never runs","is_error":false}
'@
        $bogus = Join-Path $WorkDir 'no-such-task.md'
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath,
               '-Persona', 'obi-test', '-PersonaPath', $PersonaFile, '-PromptFile', $bogus,
               '-RunId', 't', '-Phase', '1', '-OutFile', $OutFile) + @('-TimeoutSec', '10')
        & powershell.exe @a *> $null

        (Test-Path $StatusFile) | Should -Be $true
        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status   | Should -Be 'launch_error'
        $st.is_error | Should -Be $true
    }

    It 'zombie running status (deadline passed) does NOT wedge the run' {
        # Opus review #6: a 'running' status whose supervisor was outer-cap-killed leaves a live
        # child pid but a past deadline. It must be treated as stale, not block a new dispatch.
        $zombie = Join-Path $StateDir 'dispatch-status-t-97.json'
        (@{ schema_version = 1; run_id = 't'; phase = 97; status = 'running'; proc_pid = $PID
            deadline_at = '2020-01-01T00:00:00.0000000Z' } | ConvertTo-Json) |
            Set-Content $zombie -Encoding UTF8

        Set-ClaudeStub @'
@echo off
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE past zombie","is_error":false,"session_id":"z"}
'@
        Invoke-Supervisor -Extra @('-TimeoutSec', '30', '-IdleTimeoutSec', '15', '-PollSec', '1')

        $st = Get-Content $StatusFile -Raw | ConvertFrom-Json
        $st.status | Should -Be 'completed'   # not refused_concurrency
    }

    It 'atomically refuses an overlapping worker and permits resume after release' {
        $started = Join-Path $WorkDir 'started.txt'
        $release = Join-Path $WorkDir 'release.txt'
        Set-ClaudeStub @"
@echo off
echo started>>"$started"
:wait
if exist "$release" goto done
ping -n 2 127.0.0.1 >nul
goto wait
:done
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE winner","is_error":false,"session_id":"winner-sid"}
"@

        $workers = @()
        try {
            $commonArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ScriptPath,
                            '-Persona', 'obi-test', '-PersonaPath', $PersonaFile,
                            '-PromptFile', $PromptFile, '-RunId', 't', '-Phase', '1',
                            '-ExpectSignal', '"DISCOVERY COMPLETE"', '-TimeoutSec', '30',
                            '-IdleTimeoutSec', '15', '-PollSec', '1')
            foreach ($key in @('1c1', '1c2')) {
                $workers += Start-Process powershell.exe `
                    -ArgumentList ($commonArgs + @('-DispatchKey', $key)) `
                    -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru
            }

            $statusPaths = @('1c1', '1c2') | ForEach-Object {
                Join-Path $StateDir "dispatch-status-t-$_.json"
            }
            $states = @()
            $deadline = [datetime]::UtcNow.AddSeconds(30)
            do {
                $states = @($statusPaths | Where-Object { Test-Path $_ } | ForEach-Object {
                    Get-Content $_ -Raw | ConvertFrom-Json
                })
                if (@($states | Where-Object status -eq 'refused_concurrency').Count -eq 1) { break }
                Start-Sleep -Milliseconds 50
            } while ([datetime]::UtcNow -lt $deadline)

            @($states | Where-Object status -eq 'refused_concurrency').Count | Should -Be 1
        } finally {
            New-Item -ItemType File -Path $release -Force | Out-Null
            foreach ($worker in $workers) {
                if (-not $worker.WaitForExit(20000)) {
                    & taskkill.exe /T /F /PID $worker.Id *> $null
                }
            }
        }

        @(Get-Content $started).Count | Should -Be 1
        $final = @('1c1', '1c2') | ForEach-Object {
            Get-Content (Join-Path $StateDir "dispatch-status-t-$_.json") -Raw | ConvertFrom-Json
        }
        @($final | Where-Object status -eq 'completed').Count | Should -Be 1
        @($final | Where-Object status -eq 'refused_concurrency').Count | Should -Be 1

        Set-ClaudeStub @'
@echo off
echo {"type":"result","subtype":"success","result":"DISCOVERY COMPLETE resumed","is_error":false,"session_id":"winner-sid"}
'@
        Invoke-Supervisor -UseDefaultOutFile -Extra @('-DispatchKey', '1c3',
            '-ResumeSessionId', 'winner-sid', '-Attempt', '2', '-TimeoutSec', '30',
            '-IdleTimeoutSec', '15', '-PollSec', '1')
        $resumed = Get-Content (Join-Path $StateDir 'dispatch-status-t-1c3.json') -Raw | ConvertFrom-Json
        $resumed.status | Should -Be 'completed'
        $resumed.attempt | Should -Be 2
    }
}

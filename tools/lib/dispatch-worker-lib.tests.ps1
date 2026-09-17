<#
.SYNOPSIS
    Pester 5 tests for tools/lib/dispatch-worker-lib.ps1 (OPT-23 headless dispatch helpers).
#>
BeforeAll {

$ScriptDir  = $PSScriptRoot
. (Join-Path $ScriptDir 'dispatch-worker-lib.ps1')

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$PersonaFixture = @'
---
name: obi-discovery
description: Research specialist.
tools: Read, Grep, Glob, Bash, Write
model: claude-opus-4-6[1m]
effort: xhigh
---

# Discovery Agent

You are a senior research analyst. Output DISCOVERY COMPLETE when done.
'@

}

Describe 'Get-PersonaParts' {
    It 'extracts tools, model, effort, and body from frontmatter' {
        $p = Get-PersonaParts -Raw $PersonaFixture
        $p.Tools | Should -Be 'Read, Grep, Glob, Bash, Write'
        $p.Model | Should -Be 'claude-opus-4-6[1m]'
        $p.Effort | Should -Be 'xhigh'
        $p.Body  | Should -Match 'Discovery Agent'
        $p.Body  | Should -Not -Match 'description:'
    }

    It 'returns the whole text as Body when there is no frontmatter' {
        $p = Get-PersonaParts -Raw "no frontmatter here`njust body"
        $p.Tools | Should -BeNullOrEmpty
        $p.Model | Should -BeNullOrEmpty
        $p.Effort | Should -BeNullOrEmpty
        $p.Body  | Should -Match 'just body'
    }
}

Describe 'Canonical effort routing' {
    BeforeAll {
        $routingTable = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'phases/phase-table.json'
    }
    It 'routes routine discovery from policy and defaults unknown author to strong' {
        $routine = Resolve-DispatchRouting -Phase 1 -RoutingTier routine -PhaseTablePath $routingTable
        $routine.model | Should -Be 'sonnet'
        $routine.effort | Should -Be 'medium'
        $strong = Resolve-DispatchRouting -Phase 2 -PhaseTablePath $routingTable
        $strong.model | Should -Be 'fable[1m]'
        $strong.effort | Should -Be 'xhigh'
    }
    It 'preserves other phase personas and refuses missing policy' {
        $other = Resolve-DispatchRouting -Phase 10 -RoutingTier strong -PhaseTablePath 'absent' -PersonaModel sonnet -PersonaEffort medium
        $other.model | Should -Be 'sonnet'
        { Resolve-DispatchRouting -Phase 1 -PhaseTablePath 'absent' } | Should -Throw
    }
}

Describe 'Build-ClaudeArgs' {
    It 'always includes -p, json output, and the system prompt' {
        $a = Build-ClaudeArgs -Prompt 'do it' -Body 'persona'
        $a -contains '-p' | Should -Be $true
        $a -contains '--output-format' | Should -Be $true
        $a -contains 'json' | Should -Be $true
        $a -contains '--append-system-prompt' | Should -Be $true
        $a -contains '--max-turns' | Should -Be $false
    }

    It 'restricts tools via --tools (split on commas), not --allowedTools' {
        $a = Build-ClaudeArgs -Prompt 'p' -Body 'b' -Tools 'Read, Grep, Bash'
        $a -contains '--tools' | Should -Be $true
        $a -contains 'Read' | Should -Be $true
        $a -contains 'Grep' | Should -Be $true
        $a -contains 'Bash' | Should -Be $true
        $a -contains '--allowedTools' | Should -Be $false
    }

    It 'adds --model only when a model is given' {
        (Build-ClaudeArgs -Prompt 'p' -Body 'b' -Model 'opus') -contains '--model' | Should -Be $true
        (Build-ClaudeArgs -Prompt 'p' -Body 'b') -contains '--model' | Should -Be $false
    }

    It 'adds --effort only when an effort is given' {
        $withEffort = Build-ClaudeArgs -Prompt 'p' -Body 'b' -Effort 'medium'
        ($withEffort -join ' ') | Should -Match '(?:^| )--effort medium(?: |$)'
        (Build-ClaudeArgs -Prompt 'p' -Body 'b') -contains '--effort' | Should -Be $false
    }

    It 'stream mode uses stream-json + verbose + partial messages (issue #200)' {
        $a = Build-ClaudeArgs -Prompt 'p' -Body 'b' -StreamJson
        ($a -join ' ') | Should -Match 'stream-json'
        $a -contains '--verbose' | Should -Be $true
        $a -contains '--include-partial-messages' | Should -Be $true
    }

    It 'assigns a session id via --session-id' {
        $a = Build-ClaudeArgs -Prompt 'p' -Body 'b' -SessionId 'abc-123'
        $a -contains '--session-id' | Should -Be $true
        $a -contains 'abc-123' | Should -Be $true
    }

    It 'resume mode uses --resume and omits append-system-prompt + session-id' {
        $a = Build-ClaudeArgs -Prompt 'continue' -Body 'b' -ResumeSessionId 'sid-9' -Effort 'medium' -StreamJson
        $a -contains '--resume' | Should -Be $true
        $a -contains 'sid-9' | Should -Be $true
        $a -contains '--append-system-prompt' | Should -Be $false
        $a -contains '--session-id' | Should -Be $false
        ($a -join ' ') | Should -Match '(?:^| )--effort medium(?: |$)'
    }
}

Describe 'Effort propagation wiring' {
    It 'passes parsed persona effort through the supervisor and runner' {
        $supervisor = Get-Content -LiteralPath (Join-Path $ScriptDir '..\dispatch-worker.ps1') -Raw
        $runner = Get-Content -LiteralPath (Join-Path $ScriptDir 'dispatch-worker-runner.ps1') -Raw

        $routing = Get-Content -LiteralPath (Join-Path $ScriptDir 'dispatch-routing.ps1') -Raw
        $routing | Should -Match '-PersonaEffort \$parts\.Effort'
        $supervisor | Should -Match '\$actualEffort = \$inputInfo\.Effort'
        $supervisor | Should -Match "'-Effort'"
        $runner | Should -Match '\[string\]\$Effort'
        $runner | Should -Match '-Effort \$Effort'
    }
}

Describe 'Get-StreamResult' {
    It 'extracts the terminal result event + session id from a stream-json file' {
        $f = Join-Path $env:TEMP "dw-stream-$(Get-Random).jsonl"
        @(
            '{"type":"system","subtype":"init","session_id":"sid-1"}',
            '{"type":"assistant","message":{"role":"assistant"}}',
            '{"type":"result","subtype":"success","result":"DISCOVERY COMPLETE found it","is_error":false,"session_id":"sid-1"}'
        ) | Set-Content $f -Encoding UTF8
        $r = Get-StreamResult -OutFile $f -ExpectSignal 'DISCOVERY COMPLETE'
        $r.ResultFound | Should -Be $true
        $r.IsError | Should -Be $false
        $r.SessionId | Should -Be 'sid-1'
        $r.SignalFound | Should -Be $true
        Remove-Item $f -Force
    }

    It 'reports not-found + is_error on a partial (killed) stream but recovers the session id' {
        $f = Join-Path $env:TEMP "dw-partial-$(Get-Random).jsonl"
        @(
            '{"type":"system","subtype":"init","session_id":"sid-2"}',
            '{"type":"assistant","message":{"role":"assistant"}}'
        ) | Set-Content $f -Encoding UTF8
        $r = Get-StreamResult -OutFile $f
        $r.ResultFound | Should -Be $false
        $r.IsError | Should -Be $true
        $r.SessionId | Should -Be 'sid-2'
        Remove-Item $f -Force
    }

    It 'reports is_error from a result event flagged is_error' {
        $f = Join-Path $env:TEMP "dw-err-$(Get-Random).jsonl"
        '{"type":"result","result":"boom","is_error":true,"session_id":"s"}' | Set-Content $f -Encoding UTF8
        (Get-StreamResult -OutFile $f).IsError | Should -Be $true
        Remove-Item $f -Force
    }

    It 'ignores malformed lines without throwing' {
        $f = Join-Path $env:TEMP "dw-bad-$(Get-Random).jsonl"
        @('not json', '{"type":"result","result":"ok","is_error":false}') | Set-Content $f -Encoding UTF8
        (Get-StreamResult -OutFile $f).ResultFound | Should -Be $true
        Remove-Item $f -Force
    }
}

Describe 'Dispatch status record' {
    It 'New-DispatchStatus seeds the launching state with a deadline' {
        $s = New-DispatchStatus -RunId 'r1' -Phase 4 -Persona 'obi-reviewer' -SessionId 'sid' `
            -DeadlineSec 270 -CurrentStatusPath 'current.json' -AttemptStatusPath 'attempt.json'
        $s.schema_version | Should -Be 3
        $s.status     | Should -Be 'launching'
        $s.run_id     | Should -Be 'r1'
        $s.phase      | Should -Be 4
        $s.session_id | Should -Be 'sid'
        $s.resumable  | Should -Be $false
        $s.deadline_at | Should -Not -BeNullOrEmpty
        $s.dispatch_key | Should -Be '4'
        $s.finished_at | Should -BeNullOrEmpty
        $s.checkpoint | Should -BeNullOrEmpty
        $s.progress_count | Should -Be 0
        $s.stream_progress_count | Should -Be 0
        $s.cpu_progress_count | Should -Be 0
        $s.artifact_progress_count | Should -Be 0
        $s.current_status_file | Should -Be 'current.json'
        $s.attempt_status_file | Should -Be 'attempt.json'
        $s.checkpoint_health | Should -Be 'not_required'
        $s.failure_reason | Should -BeNullOrEmpty
        $s.poll_sec | Should -Be 10
    }

    It 'records a real checkpoint block and explicit dispatch key' {
        $p = Join-Path $TestDrive 'checkpoint.md'
        'done' | Set-Content -LiteralPath $p -Encoding UTF8
        $s = New-DispatchStatus -RunId 'r1' -Phase 4 -CheckpointPath $p -DispatchKey '4c2'
        $s.dispatch_key | Should -Be '4c2'
        $s.checkpoint.path | Should -Be ([System.IO.Path]::GetFullPath($p))
        $s.checkpoint.exists | Should -Be $true
        $s.checkpoint.bytes | Should -BeGreaterThan 0
        $s.checkpoint.mtime | Should -Not -BeNullOrEmpty
    }

    It 'Write/Read round-trips atomically' {
        $p = Join-Path $env:TEMP "dw-status-$(Get-Random).json"
        $s = New-DispatchStatus -RunId 'r2' -Phase 1
        Write-DispatchStatus -Status $s -Path $p
        (Test-Path $p) | Should -Be $true
        (Test-Path "$p.tmp") | Should -Be $false
        $back = Read-DispatchStatus -Path $p
        $back.run_id | Should -Be 'r2'
        $back.status | Should -Be 'launching'
        Remove-Item $p -Force
    }

    It 'Read-DispatchStatus returns null for a missing file' {
        (Read-DispatchStatus -Path (Join-Path $env:TEMP "dw-none-$(Get-Random).json")) | Should -BeNullOrEmpty
    }
}

Describe 'Phase-aware dispatch budget policy' {
    BeforeAll {
        $script:PhaseTablePath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) `
            'phases\phase-table.json'
    }

    It 'resolves distinct measured Discovery, Author, and Learning budgets' {
        $discovery = Get-DispatchPhasePolicy -Phase 1 -PhaseTablePath $PhaseTablePath
        $author = Get-DispatchPhasePolicy -Phase 2 -PhaseTablePath $PhaseTablePath
        $learning = Get-DispatchPhasePolicy -Phase 10 -PhaseTablePath $PhaseTablePath
        $compatibility = Get-DispatchPhasePolicy -Phase 3 -PhaseTablePath $PhaseTablePath

        @($discovery.nominal_work_sec, $author.nominal_work_sec, $learning.nominal_work_sec) |
            Sort-Object -Unique | Should -HaveCount 3
        $discovery.absolute_sec | Should -Be 540
        $author.absolute_sec | Should -Be 540
        $learning.absolute_sec | Should -Be 420
        $compatibility.source | Should -Be 'phase-table-default'
        $compatibility.absolute_sec | Should -Be 270
        (Get-DispatchOuterTimeoutSec -Policy $author) | Should -Be 570
    }

    It 'keeps every phase outer call inside the 600 s host ceiling' {
        # Issue #200 T1: outer call = absolute_sec + cleanup_margin_sec. Above the
        # host ceiling the harness kills the supervisor before it can persist a
        # terminal status, leaving a stale 'running' record and an orphan tree.
        foreach ($phase in 1..10) {
            $policy = Get-DispatchPhasePolicy -Phase $phase -PhaseTablePath $PhaseTablePath
            (Get-DispatchOuterTimeoutSec -Policy $policy) | Should -BeLessOrEqual 570
        }
    }

    It 'allows a smaller explicit diagnostic timeout but rejects one beyond the phase cap' {
        $override = Get-DispatchPhasePolicy -Phase 1 -PhaseTablePath $PhaseTablePath `
            -TimeoutOverrideSec 30 -IdleTimeoutOverrideSec 90
        $override.source | Should -Be 'explicit-timeout-override'
        $override.nominal_work_sec | Should -Be 30
        $override.productive_extension_sec | Should -Be 0
        $override.finalization_sec | Should -Be 0
        { Get-DispatchPhasePolicy -Phase 1 -PhaseTablePath $PhaseTablePath `
            -TimeoutOverrideSec 601 } | Should -Throw '*exceeds phase 1 absolute cap*'
    }

    It 'grants one bounded extension only with recent progress and checkpoint growth' {
        $at = [datetime]'2026-08-19T12:51:44Z'
        $status = [pscustomobject]@{
            stream_last_activity_at = $at.AddSeconds(-3).ToString('o')
            cpu_last_activity_at = $null
        }
        $policy = [pscustomobject]@{
            recent_progress_sec = 15; idle_sec = 90; productive_extension_sec = 120
        }
        $decision = Get-ProductiveExtensionDecision -Status $status -Policy $policy `
            -ObservedAtUtc $at -IdleForSec 3 -CheckpointStartBytes 100 `
            -CheckpointBytes 200 -CheckpointContained $true
        $decision.grant | Should -Be $true
        $decision.reason | Should -Be 'granted'
        $decision.extension_duration_sec | Should -Be 120
        $decision.progress_at_cutoff.checkpoint_grew | Should -Be $true
    }

    It 'denies extension for missing, unchanged, idle, or already-used evidence' {
        $at = [datetime]'2026-08-19T12:51:44Z'
        $status = [pscustomobject]@{
            stream_last_activity_at = $at.AddSeconds(-3).ToString('o')
            cpu_last_activity_at = $null
        }
        $policy = [pscustomobject]@{
            recent_progress_sec = 15; idle_sec = 90; productive_extension_sec = 120
        }
        (Get-ProductiveExtensionDecision -Status $status -Policy $policy -ObservedAtUtc $at `
            -IdleForSec 3 -CheckpointStartBytes 0 -CheckpointBytes 0 `
            -CheckpointContained $true).reason | Should -Be 'checkpoint_empty'
        (Get-ProductiveExtensionDecision -Status $status -Policy $policy -ObservedAtUtc $at `
            -IdleForSec 3 -CheckpointStartBytes 200 -CheckpointBytes 200 `
            -CheckpointContained $true).reason | Should -Be 'checkpoint_did_not_grow'
        (Get-ProductiveExtensionDecision -Status $status -Policy $policy -ObservedAtUtc $at `
            -IdleForSec 90 -CheckpointStartBytes 100 -CheckpointBytes 200 `
            -CheckpointContained $true).reason | Should -Be 'idle_at_nominal_cutoff'
        (Get-ProductiveExtensionDecision -Status $status -Policy $policy -ObservedAtUtc $at `
            -IdleForSec 3 -CheckpointStartBytes 100 -CheckpointBytes 200 `
            -CheckpointContained $true -ExtensionAlreadyUsed $true).reason |
            Should -Be 'extension_already_used'
    }

    It 'instructs the worker to stop tools and reserve finalization time' {
        $policy = Get-DispatchPhasePolicy -Phase 1 -PhaseTablePath $PhaseTablePath
        $instruction = Get-DispatchBudgetInstruction -Policy $policy -CheckpointPath '.obi\discovery-report.md'
        $instruction | Should -Match 'Stop all tool calls no later than 480 seconds'
        $instruction | Should -Match 'Reserve the final 60 seconds'
        $instruction | Should -Match 'absolute 540-second cap'
    }
}

Describe 'Checkpoint deliverable contract' {
    It 'allows the first quarter-window as grace' {
        (Test-CheckpointDeliverable -ElapsedSec 24 -DeadlineSec 100 -Bytes 0) | Should -BeNullOrEmpty
    }

    It 'reports a missing non-empty checkpoint at the first quarter-window' {
        (Test-CheckpointDeliverable -ElapsedSec 25 -DeadlineSec 100 -Bytes 0) |
            Should -Be 'checkpoint_missing'
    }

    It 'accepts a one-byte first checkpoint and does not call it stalled' {
        (Test-CheckpointDeliverable -ElapsedSec 25 -DeadlineSec 100 -Bytes 1 `
            -SinceArtifactProgressSec 25 -CheckpointEstablished $false) | Should -BeNullOrEmpty
    }

    It 'flags an established checkpoint that stops moving for a full window' {
        (Test-CheckpointDeliverable -ElapsedSec 50 -DeadlineSec 100 -Bytes 2048 `
            -SinceArtifactProgressSec 25 -CheckpointEstablished $true) | Should -Be 'artifact_stalled'
    }
}

Describe 'Checkpoint path and dispatch-key validation' {
    It 'accepts a repository-contained checkpoint and normalizes it' {
        $root = Join-Path $TestDrive 'repo'
        $reports = Join-Path $root '.obi\reports'
        New-Item -ItemType Directory -Path $reports -Force | Out-Null
        $v = Test-CheckpointPath -Path '.obi\reports\phase.md' -RepositoryRoot $root
        $v.Valid | Should -Be $true
        $v.Full | Should -Be (Join-Path $reports 'phase.md')
    }

    It 'rejects checkpoint traversal outside the repository' {
        $root = Join-Path $TestDrive 'repo'
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        $v = Test-CheckpointPath -Path '..\escaped.md' -RepositoryRoot $root
        $v.Valid | Should -Be $false
        $v.Reason | Should -Match 'escapes repository root'
    }

    It 'rejects a checkpoint whose existing parent crosses a directory junction' {
        $root = Join-Path $TestDrive 'junction-repo'
        $outside = Join-Path $TestDrive 'junction-outside'
        New-Item -ItemType Directory -Path $root, $outside -Force | Out-Null
        $link = Join-Path $root 'linked-outside'
        New-Item -ItemType Junction -Path $link -Target $outside | Out-Null

        $v = Test-CheckpointPath -Path (Join-Path $link 'phase.md') -RepositoryRoot $root

        $v.Valid | Should -Be $false
        $v.Reason | Should -Match 'reparse-point component'
    }

    It 'rejects an existing checkpoint leaf that is a file reparse point' {
        $root = Join-Path $TestDrive 'leaf-link-repo'
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        $link = Join-Path $root 'checkpoint.md'
        'placeholder' | Set-Content -LiteralPath $link -Encoding UTF8
        Mock Get-Item {
            [pscustomobject]@{
                FullName = $link
                Attributes = [System.IO.FileAttributes]::ReparsePoint
            }
        } -ParameterFilter {
            $LiteralPath -eq $link
        }

        $v = Test-CheckpointPath -Path $link -RepositoryRoot $root

        $v.Valid | Should -Be $false
        $v.Reason | Should -Match 'reparse-point leaf'
    }

    It 'accepts only matching phase chunk keys one through three' {
        (Test-DispatchKey -Key '2c3' -Phase 2).Valid | Should -Be $true
        (Test-DispatchKey -Key '2c4' -Phase 2).Valid | Should -Be $false
        (Test-DispatchKey -Key '3c1' -Phase 2).Valid | Should -Be $false
    }
}

Describe 'Capacity-mismatch evidence' {
    It 'accepts a verified timeout with observed progress inside the final poll window' {
        $s = [pscustomobject]@{
            status = 'timed_out'; kill_verified = $true; progress_count = 4; poll_sec = 10
            last_progress_at = '2026-08-07T19:04:29Z'; deadline_at = '2026-08-07T19:04:35Z'
            finished_at = '2026-08-07T19:04:40Z'
        }
        (Test-CapacityMismatchEvidence -Status $s) | Should -Be $true
    }

    It 'rejects a no-progress timeout even when its initial timestamp is near a short deadline' {
        $s = [pscustomobject]@{
            status = 'timed_out'; kill_verified = $true; progress_count = 0; poll_sec = 10
            last_progress_at = '2026-08-07T19:04:33Z'; deadline_at = '2026-08-07T19:04:35Z'
            finished_at = '2026-08-07T19:04:40Z'
        }
        (Test-CapacityMismatchEvidence -Status $s) | Should -Be $false
    }

    It 'rejects progress that stopped before the final poll window' {
        $s = [pscustomobject]@{
            status = 'timed_out'; kill_verified = $true; progress_count = 1; poll_sec = 10
            last_progress_at = '2026-08-07T19:04:20Z'; deadline_at = '2026-08-07T19:04:35Z'
            finished_at = '2026-08-07T19:04:40Z'
        }
        (Test-CapacityMismatchEvidence -Status $s) | Should -Be $false
    }
}

Describe 'Read-WorkerResult' {
    It 'parses result + is_error from a valid worker JSON and detects the expected signal' {
        $f = Join-Path $env:TEMP "dw-ok-$(Get-Random).json"
        '{"result":"DISCOVERY COMPLETE\nfound stuff","is_error":false}' | Set-Content $f -Encoding UTF8
        $r = Read-WorkerResult -OutFile $f -TimedOut $false -ExpectSignal 'DISCOVERY COMPLETE'
        $r.IsError | Should -Be $false
        $r.SignalFound | Should -Be $true
        Remove-Item $f -Force
    }

    It 'reports is_error on timeout regardless of file' {
        $r = Read-WorkerResult -OutFile 'nope.json' -TimedOut $true -ExpectSignal 'X'
        $r.IsError | Should -Be $true
        $r.SignalFound | Should -Be $false
    }

    It 'reports is_error when the output file is missing' {
        $r = Read-WorkerResult -OutFile (Join-Path $env:TEMP "dw-missing-$(Get-Random).json")
        $r.IsError | Should -Be $true
    }

    It 'does not find the signal when the result lacks it' {
        $f = Join-Path $env:TEMP "dw-nosig-$(Get-Random).json"
        '{"result":"AUTHOR COMPLETE","is_error":false}' | Set-Content $f -Encoding UTF8
        (Read-WorkerResult -OutFile $f -ExpectSignal 'DISCOVERY COMPLETE').SignalFound | Should -Be $false
        Remove-Item $f -Force
    }
}

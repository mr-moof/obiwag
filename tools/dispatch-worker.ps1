<#
.SYNOPSIS
    Bounded supervisor for one headless Claude phase, with durable terminal status.
.DESCRIPTION
    Observes stream, CPU and artifact progress; enforces phase/idle deadlines and scoped kills.
    Preserves both attempt records and permits one same-session resume, one live worker per run.
.PARAMETER TimeoutSec
    Zero uses the canonical phase budget. An override disables extension/finalization and cannot
    exceed the configured absolute cap; callers include cleanup_margin_sec in their outer bound.
.PARAMETER IdleTimeoutSec
    Zero uses the phase policy. Overrides the no-progress window.
.PARAMETER PhaseTablePath
    Explicit policy path for isolated validation; normally resolved beside the tools directory.
.PARAMETER CheckpointPath
    Required deliverable when supplied. Stream/CPU prove provider liveness; artifact progress is
    distinct. Missing output on exit is a resumable error with failure_reason=checkpoint_missing.
.PARAMETER DispatchKey
    Optional phase chunk key <phase>c<k>, k=1..3, isolating filenames without changing phase identity.
.PARAMETER RoutingTier
    Discovery/Author use routine or strong (default). Other phases retain persona model/effort.
.PARAMETER ResumeSessionId
    Resume the same Claude session once. Attempt is recorded in status.
.OUTPUTS
    Summary JSON; terminal status and full stream files are named in the summary.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Persona,
    [Parameter(Mandatory)][string]$PromptFile,
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string]$RunId = 'adhoc',
    [ValidateRange(0, 999)]
    [int]$Phase = 0,
    [ValidateRange(0, 3600)]
    [int]$TimeoutSec = 0,
    [ValidateRange(0, 3600)]
    [int]$IdleTimeoutSec = 0,
    [ValidateRange(1, 30)]
    [int]$PollSec = 10,
    [string]$ExpectSignal,
    [string]$OutFile,
    [string]$PersonaPath,
    [string]$CheckpointPath,
    [string]$PhaseTablePath,
    [ValidateSet('routine', 'strong')]
    [string]$RoutingTier,
    [string]$DispatchKey,
    [string]$ResumeSessionId,
    [ValidateRange(1, 2)]
    [int]$Attempt = 1
)

$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'lib\dispatch-worker-lib.ps1')
. (Join-Path $ScriptDir 'lib\dispatch-worker-process.ps1')   # tree CPU / PID / kill helpers

if (-not $PhaseTablePath) {
    $PhaseTablePath = Join-Path (Split-Path -Parent $ScriptDir) 'phases\phase-table.json'
}
$phasePolicy = $null
$hardTimeoutSec = if ($TimeoutSec -gt 0) { $TimeoutSec } else { 270 }
$idleLimitSec = if ($IdleTimeoutSec -gt 0) { $IdleTimeoutSec } else { 90 }
$actualRoutingTier = ''
$actualModel = ''
$controlledInputBytes = $null
$actualEffort = ''

# --- files + session id (computed FIRST, so a pre-launch failure can still record a terminal
#     status -- issue #200 SS1: ALWAYS reach a recorded terminal state; Opus review #1) ---------
$repositoryRoot = [System.IO.Path]::GetFullPath([string]$PWD)
$dispatchKeyCheck = Test-DispatchKey -Key $DispatchKey -Phase $Phase
$fileKey = if ($dispatchKeyCheck.Valid -and $DispatchKey) { $DispatchKey } else { [string]$Phase }
if (-not $OutFile) { $OutFile = Join-Path $PWD ".obi\state\worker-$RunId-$fileKey-a$Attempt.jsonl" }
$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$errFile     = "$OutFile.err"
$exitFile    = "$OutFile.exit"
$statusFile  = Join-Path $outDir "dispatch-status-$RunId-$fileKey.json"
$attemptStatusFile = Join-Path $outDir "dispatch-status-$RunId-$fileKey-a$Attempt.json"
$summaryFile = Join-Path $outDir "worker-summary-$RunId-$fileKey.json"
$attemptSummaryFile = Join-Path $outDir "worker-summary-$RunId-$fileKey-a$Attempt.json"
$bodyFile    = Join-Path $outDir "worker-sys-$RunId-$fileKey-a$Attempt.txt"
$resolvedCheckpointPath = ''

# Session identity: assign up front (or resume an existing one). issue #200 SS1/SS2.
$sessionId = if ($ResumeSessionId) { $ResumeSessionId } else { [guid]::NewGuid().ToString() }

function Write-WorkerSummary {
    param($Status)
    $result = ''
    if ($Status.result_preview) { $result = [string]$Status.result_preview }
    $summary = [ordered]@{
        persona        = $Persona
        phase          = $Phase
        routing_tier   = $Status.routing_tier
        model          = $Status.model
        effort         = $Status.effort
        input_usage    = $Status.input_usage
        dispatch_key   = $Status.dispatch_key
        attempt        = $Attempt
        status         = $Status.status
        exit_code      = $Status.exit_code
        timed_out      = $Status.timed_out
        is_error       = $Status.is_error
        signal_found   = $Status.signal_found
        expect_signal  = $ExpectSignal
        session_id     = $Status.session_id
        resumable      = $Status.resumable
        out_file       = $OutFile
        err_file       = $errFile
        status_file    = $statusFile
        attempt_status_file = $attemptStatusFile
        summary_file   = $summaryFile
        attempt_summary_file = $attemptSummaryFile
        system_file    = $bodyFile
        controlled_input_bytes = $controlledInputBytes
        checkpoint     = $Status.checkpoint
        checkpoint_health = $Status.checkpoint_health
        last_progress_at = $Status.last_progress_at
        progress_count = $Status.progress_count
        stream_last_activity_at = $Status.stream_last_activity_at
        stream_progress_count = $Status.stream_progress_count
        stream_bytes   = $Status.stream_bytes
        cpu_last_activity_at = $Status.cpu_last_activity_at
        cpu_progress_count = $Status.cpu_progress_count
        cpu_time_ms    = $Status.cpu_time_ms
        artifact_last_activity_at = $Status.artifact_last_activity_at
        artifact_progress_count = $Status.artifact_progress_count
        poll_sec       = $Status.poll_sec
        phase_policy   = $Status.phase_policy
        nominal_deadline_at = $Status.nominal_deadline_at
        finalization_due_at = $Status.finalization_due_at
        finalization_started_at = $Status.finalization_started_at
        absolute_deadline_at = $Status.absolute_deadline_at
        lease_expires_at = $Status.lease_expires_at
        outer_timeout_sec = $Status.outer_timeout_sec
        extension_decision = $Status.extension_decision
        extension_reason = $Status.extension_reason
        extension_duration_sec = $Status.extension_duration_sec
        progress_at_cutoff = $Status.progress_at_cutoff
        initial_duration_sec = $Status.initial_duration_sec
        final_duration_sec = $Status.final_duration_sec
        finished_at    = $Status.finished_at
        kill_reason    = $Status.kill_reason
        kill_verified  = $Status.kill_verified
        failure_reason = $Status.failure_reason
        result_preview = $result
    }
    $json = $summary | ConvertTo-Json -Depth 4
    Write-DispatchStatus -Status $summary -Path $attemptSummaryFile
    Write-DispatchStatus -Status $summary -Path $summaryFile
    return $json
}

function Write-WorkerStatus {
    param([Parameter(Mandatory)]$Status)
    # Persist the attempt snapshot first. The unscoped path is only the atomic current view.
    Write-DispatchStatus -Status $Status -Path $attemptStatusFile
    Write-DispatchStatus -Status $Status -Path $statusFile
}

# Emit a terminal FAILURE status + summary and exit (never leave the orchestrator with no status).
function Write-TerminalFailure {
    param([string]$Message, [string]$StatusName = 'launch_error', [int]$Code = 5)
    $s = New-DispatchStatus -RunId $RunId -Phase $Phase -Persona $Persona -Attempt $Attempt `
        -SessionId $sessionId -OutFile $OutFile -ErrFile $errFile -DeadlineSec $hardTimeoutSec -PollSec $PollSec `
        -CheckpointPath $resolvedCheckpointPath -DispatchKey $DispatchKey `
        -CurrentStatusPath $statusFile -AttemptStatusPath $attemptStatusFile `
        -CurrentSummaryPath $summaryFile -AttemptSummaryPath $attemptSummaryFile -SystemPath $bodyFile `
        -RoutingTier $actualRoutingTier -Model $actualModel -Effort $actualEffort `
        -PhasePolicy $phasePolicy
    $s.status = $StatusName; $s.is_error = $true; $s.result_preview = $Message
    $s.failure_reason = 'launch_failure'
    $s.finished_at = ([datetime]::UtcNow).ToString('o')
    Write-WorkerStatus -Status $s
    Write-WorkerSummary -Status $s | Out-Null
    exit $Code
}

# --- resolve + validate persona/prompt INSIDE try/catch so a failure records launch_error ------
try {
    $phasePolicy = Get-DispatchPhasePolicy -Phase $Phase -PhaseTablePath $PhaseTablePath `
        -TimeoutOverrideSec $TimeoutSec -IdleTimeoutOverrideSec $IdleTimeoutSec
    $hardTimeoutSec = [int]$phasePolicy.absolute_sec
    $idleLimitSec = [int]$phasePolicy.idle_sec
    if (-not $dispatchKeyCheck.Valid) { throw $dispatchKeyCheck.Reason }
    $checkpointCheck = Test-CheckpointPath -Path $CheckpointPath -RepositoryRoot $repositoryRoot
    if (-not $checkpointCheck.Valid) { throw $checkpointCheck.Reason }
    $resolvedCheckpointPath = $checkpointCheck.Full
    $inputInfo = Initialize-DispatchInput -Persona $Persona -PersonaPath $PersonaPath `
        -PromptFile $PromptFile -ScriptDir $ScriptDir -Phase $Phase -RoutingTier $RoutingTier `
        -PhaseTablePath $PhaseTablePath -PhasePolicy $phasePolicy `
        -CheckpointPath $resolvedCheckpointPath -BodyFile $bodyFile
    $parts = $inputInfo.Parts
    $actualRoutingTier = $inputInfo.Tier
    $actualModel = $inputInfo.Model
    $actualEffort = $inputInfo.Effort
    $controlledInputBytes = $inputInfo.ControlledInputBytes
} catch {
    Write-TerminalFailure -Message "pre-launch: $_"
}

# --- atomic concurrency = 1 guard (issue #200 SS1) --------------------------------------------
# The process-owned mutex closes the old status-scan race. It is held through terminal publication;
# Windows releases it automatically if the supervisor dies, so stale status cannot wedge a run.
$mutexKey = ($repositoryRoot.TrimEnd('\') + '|' + $RunId).ToLowerInvariant()
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try { $mutexHash = [BitConverter]::ToString($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($mutexKey))).Replace('-', '') }
finally { $sha256.Dispose() }
$dispatchMutex = [System.Threading.Mutex]::new($false, "Local\ObiDispatch-$mutexHash")
try { $mutexHeld = $dispatchMutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $mutexHeld = $true }
catch { Write-TerminalFailure -Message "concurrency guard failed: $_" }
if (-not $mutexHeld) {
    $dispatchMutex.Dispose()
    Write-TerminalFailure -Message "refused: another worker already owns run $RunId" `
        -StatusName 'refused_concurrency' -Code 9
}

# --- initial status: launching --------------------------------------------------------------
$status = New-DispatchStatus -RunId $RunId -Phase $Phase -Persona $Persona -Attempt $Attempt `
    -SessionId $sessionId -OutFile $OutFile -ErrFile $errFile -DeadlineSec $hardTimeoutSec -PollSec $PollSec `
    -CheckpointPath $resolvedCheckpointPath -DispatchKey $DispatchKey `
    -CurrentStatusPath $statusFile -AttemptStatusPath $attemptStatusFile `
    -CurrentSummaryPath $summaryFile -AttemptSummaryPath $attemptSummaryFile -SystemPath $bodyFile `
    -RoutingTier $actualRoutingTier -Model $actualModel -Effort $actualEffort `
    -PhasePolicy $phasePolicy
Write-WorkerStatus -Status $status

# --- launch the child (clean PID for a scoped tree-kill; parent redirects stdout/err to files) ---
$runner  = Join-Path $ScriptDir 'lib\dispatch-worker-runner.ps1'
$libPath = Join-Path $ScriptDir 'lib\dispatch-worker-lib.ps1'
$psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner,
            '-BodyFile', $bodyFile, '-TaskFile', $PromptFile, '-LibPath', $libPath,
            '-ExitFile', $exitFile)
if ($ResumeSessionId) { $psArgs += @('-ResumeSessionId', $sessionId) }
else                  { $psArgs += @('-SessionId', $sessionId) }
if ($parts.Tools) { $psArgs += @('-Tools', (($parts.Tools -split '\s*,\s*' | Where-Object { $_ }) -join ',')) }
if ($actualModel) { $psArgs += @('-Model', $actualModel) }
if ($actualEffort) { $psArgs += @('-Effort', $actualEffort) }

$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -PassThru -NoNewWindow `
        -RedirectStandardOutput $OutFile -RedirectStandardError $errFile
} catch {
    Write-TerminalFailure -Message "launch failed: $_"
}

# The worker budget starts when the child exists, not while the supervisor is publishing its
# launching lease. Keep the persisted deadline and the watchdog stopwatch on the same clock.
$sw.Restart()
$launchUtc = [datetime]::UtcNow
$nominalSec = [int]$phasePolicy.nominal_work_sec
$extensionSec = [int]$phasePolicy.productive_extension_sec
$finalizationSec = [int]$phasePolicy.finalization_sec
$absoluteSec = [int]$phasePolicy.absolute_sec
$finalizationBoundarySec = $nominalSec + $extensionSec
$effectiveDeadlineSec = $nominalSec + $finalizationSec
$status.started_at = $launchUtc.ToString('o')
$status.nominal_deadline_at = $launchUtc.AddSeconds($nominalSec).ToString('o')
$status.finalization_due_at = $launchUtc.AddSeconds($finalizationBoundarySec).ToString('o')
$status.absolute_deadline_at = $launchUtc.AddSeconds($absoluteSec).ToString('o')
$status.lease_expires_at = $status.absolute_deadline_at
$status.deadline_at = $launchUtc.AddSeconds($effectiveDeadlineSec).ToString('o')
$status.proc_pid = $proc.Id
$status.status = 'running'
Write-WorkerStatus -Status $status

# --- supervisor fault boundary (issue #200 T2): everything to terminal publication runs inside
# try/catch/finally so a terminating error can never leave status 'running', an orphan tree, or an
# undisposed mutex. Body deliberately not re-indented (small diff, not a reflow).
$supervisorFaulted = $false
try {

# --- watchdog loop (output-growth + tree-CPU liveness) -----------------------------------------
# Idle protection starts at launch so a provider that emits nothing and consumes no measurable
# CPU is caught by IdleTimeoutSec instead of occupying the entire hard-deadline budget.
$lastBytes = 0; $lastCpuMs = -1.0
$idleSw = [System.Diagnostics.Stopwatch]::StartNew()
$artifactSw = [System.Diagnostics.Stopwatch]::StartNew()
$checkpointEnabled = [bool]$resolvedCheckpointPath
$checkpointInfo = if ($checkpointEnabled) { Get-CheckpointInfo -Path $resolvedCheckpointPath } else { $null }
$checkpointStartBytes = if ($checkpointInfo) { [long]$checkpointInfo.bytes } else { 0 }
$lastCheckpointExists = if ($checkpointInfo) { [bool]$checkpointInfo.exists } else { $false }
$lastCheckpointBytes = if ($checkpointInfo) { [long]$checkpointInfo.bytes } else { 0 }
$lastCheckpointMtime = if ($checkpointInfo) { [string]$checkpointInfo.mtime } else { '' }
$checkpointEstablished = ($checkpointEnabled -and $lastCheckpointBytes -gt 0)
$extensionEvaluated = $false
$extensionGranted = $false
$killed = $false; $killReason = ''

while ($true) {
    $nextBoundarySec = $effectiveDeadlineSec
    if (-not $extensionEvaluated) {
        $nextBoundarySec = [Math]::Min($nextBoundarySec, $nominalSec)
    } elseif ($extensionGranted -and -not $status.finalization_started_at) {
        $nextBoundarySec = [Math]::Min($nextBoundarySec, $finalizationBoundarySec)
    }
    $remainingMs = [int][Math]::Floor(($nextBoundarySec - $sw.Elapsed.TotalSeconds) * 1000)
    if ($remainingMs -lt 1) { $remainingMs = 1 }
    $waitMs = [Math]::Max(1, [Math]::Min($PollSec * 1000, $remainingMs))
    if ($proc.WaitForExit($waitMs)) { break }
    # Record the finalization window the moment its boundary passes, BEFORE any hard-boundary
    # break below: a CIM probe can cost more than the window itself, and a run killed at the
    # absolute cap must still show that finalization was due (it is what the worker was told).
    if ($extensionGranted -and -not $status.finalization_started_at -and
        $sw.Elapsed.TotalSeconds -ge $finalizationBoundarySec) {
        $status.finalization_started_at = ([datetime]::UtcNow).ToString('o')
        Write-WorkerStatus -Status $status
    }
    if (-not $extensionEvaluated -and $extensionSec -eq 0 -and $finalizationSec -eq 0 -and
        $sw.Elapsed.TotalSeconds -ge $nominalSec) {
        # An explicit diagnostic override has no decision/finalization runway. Preserve the legacy
        # hard-boundary invariant: do not publish a late progress sample beyond deadline_at.
        $killed = $true; $killReason = 'timed_out'; break
    }
    # At the nominal boundary the evidence probe below decides extension/finalization. Once that
    # decision exists, however, the effective hard boundary is terminal and cannot be extended.
    if (($extensionEvaluated -and $sw.Elapsed.TotalSeconds -ge $effectiveDeadlineSec) -or
        $sw.Elapsed.TotalSeconds -ge $absoluteSec) {
        $killed = $true; $killReason = 'timed_out'; break
    }

    $bytes = (Get-FileLenSafe $OutFile) + (Get-FileLenSafe $errFile)
    $cpuMs = Get-ProcTreeCpuMs -RootPid $proc.Id
    # CIM process-tree sampling can itself take seconds. Do not publish a progress observation
    # after the hard boundary merely because the probe began before it.
    if (($extensionEvaluated -and $sw.Elapsed.TotalSeconds -ge $effectiveDeadlineSec) -or
        $sw.Elapsed.TotalSeconds -ge $absoluteSec) {
        $killed = $true; $killReason = 'timed_out'; break
    }

    $now = [datetime]::UtcNow
    $streamProgressed = ($bytes -gt $lastBytes)
    $cpuProgressed = ($cpuMs -ge 0 -and $lastCpuMs -ge 0 -and ($cpuMs - $lastCpuMs) -gt 200)
    $progressed = ($streamProgressed -or $cpuProgressed)

    $statusChanged = $false
    if ($streamProgressed) {
        $status.stream_last_activity_at = $now.ToString('o')
        $status.stream_progress_count = [int]$status.stream_progress_count + 1
        $status.stream_bytes = [long]$bytes
        $statusChanged = $true
    }
    if ($cpuProgressed) {
        $status.cpu_last_activity_at = $now.ToString('o')
        $status.cpu_progress_count = [int]$status.cpu_progress_count + 1
        $status.cpu_time_ms = [double]$cpuMs
        $statusChanged = $true
    }
    if ($progressed) {
        $idleSw.Restart()
        $status.last_progress_at = $now.ToString('o')
        $status.progress_count = [int]$status.progress_count + 1
        $statusChanged = $true
    }

    if ($checkpointEnabled) {
        $checkpointInfo = Update-DispatchCheckpoint -Status $status -Path $resolvedCheckpointPath
        $artifactChanged = (
            ([bool]$checkpointInfo.exists -ne $lastCheckpointExists) -or
            ([long]$checkpointInfo.bytes -ne $lastCheckpointBytes) -or
            ([string]$checkpointInfo.mtime -ne $lastCheckpointMtime)
        )
        if ($artifactChanged) {
            # Artifact movement is useful work and keeps the idle watchdog honest. Capacity
            # evidence remains stream/tree-CPU-only through last_progress_at.
            $idleSw.Restart()
            $artifactSw.Restart()
            $status.artifact_last_activity_at = $now.ToString('o')
            $status.artifact_progress_count = [int]$status.artifact_progress_count + 1
            $statusChanged = $true
        }
        if (-not $checkpointEstablished -and [long]$checkpointInfo.bytes -gt 0) {
            $checkpointEstablished = $true
            $artifactSw.Restart()
        }
        $lastCheckpointExists = [bool]$checkpointInfo.exists
        $lastCheckpointBytes = [long]$checkpointInfo.bytes
        $lastCheckpointMtime = [string]$checkpointInfo.mtime

        $checkpointVerdict = Test-CheckpointDeliverable `
            -ElapsedSec $sw.Elapsed.TotalSeconds -DeadlineSec $absoluteSec `
            -Bytes $lastCheckpointBytes -SinceArtifactProgressSec $artifactSw.Elapsed.TotalSeconds `
            -CheckpointEstablished $checkpointEstablished
        $status.checkpoint_health = if ($checkpointVerdict) {
            $checkpointVerdict
        } elseif ($lastCheckpointBytes -gt 0) {
            'healthy'
        } else {
            'grace'
        }
        $statusChanged = $true
    }
    # Observe every liveness source before applying the idle rule. In particular, a checkpoint
    # write near the boundary must restart the idle clock before it can be classified as hung.
    if ($idleSw.Elapsed.TotalSeconds -ge $idleLimitSec) {
        $killed = $true; $killReason = 'hung'; break
    }

    if (-not $extensionEvaluated -and $sw.Elapsed.TotalSeconds -ge $nominalSec) {
        $decision = Get-ProductiveExtensionDecision -Status $status -Policy $phasePolicy `
            -ObservedAtUtc $now -IdleForSec $idleSw.Elapsed.TotalSeconds `
            -CheckpointStartBytes $checkpointStartBytes -CheckpointBytes $lastCheckpointBytes `
            -CheckpointContained $checkpointEnabled -ExtensionAlreadyUsed:$false
        $extensionEvaluated = $true
        $extensionGranted = [bool]$decision.grant
        $status.extension_decision = if ($extensionGranted) { 'granted' } else { 'denied' }
        $status.extension_reason = [string]$decision.reason
        $status.extension_duration_sec = [int]$decision.extension_duration_sec
        $status.progress_at_cutoff = $decision.progress_at_cutoff
        $status.initial_duration_sec = [Math]::Round($sw.Elapsed.TotalSeconds, 3)
        if ($extensionGranted) {
            $effectiveDeadlineSec = $absoluteSec
        } else {
            # Even without productive-work extension, preserve the configured reconciliation
            # reserve unless an idle kill already fired. The launch instruction tells the worker
            # to stop tools and use this time for checkpoint/signal finalization.
            $effectiveDeadlineSec = $nominalSec + $finalizationSec
            $status.finalization_started_at = $now.ToString('o')
        }
        $status.deadline_at = $launchUtc.AddSeconds($effectiveDeadlineSec).ToString('o')
        $statusChanged = $true
    }
    if ($extensionGranted -and -not $status.finalization_started_at -and
        $sw.Elapsed.TotalSeconds -ge $finalizationBoundarySec) {
        $status.finalization_started_at = $now.ToString('o')
        $statusChanged = $true
    }
    if ($statusChanged) { Write-WorkerStatus -Status $status }
    $lastBytes = $bytes; $lastCpuMs = $cpuMs

    if (($extensionEvaluated -and $sw.Elapsed.TotalSeconds -ge $effectiveDeadlineSec) -or
        $sw.Elapsed.TotalSeconds -ge $absoluteSec) {
        $killed = $true; $killReason = 'timed_out'; break
    }
}

# A provider may exit before the first polling interval. Missing required output is a terminal
# validation error, not a kill: the child is already gone and no tree termination occurred.
$completionFailureReason = ''
if ($checkpointEnabled) {
    $checkpointInfo = Update-DispatchCheckpoint -Status $status -Path $resolvedCheckpointPath
    if ([long]$checkpointInfo.bytes -lt 1) {
        $status.checkpoint_health = 'checkpoint_missing'
        if (-not $killed) { $completionFailureReason = 'checkpoint_missing' }
    } elseif ($status.checkpoint_health -eq 'grace') {
        $status.checkpoint_health = 'healthy'
    }
}

# --- terminate + verify -----------------------------------------------------------------------
$killVerified = $null
if ($killed) {
    if ($proc.WaitForExit(0)) {
        # The deadline/idle boundary was already crossed. Preserve that terminal classification
        # even if the process exits in the narrow interval before taskkill.
        $killVerified = $true
    } else {
        & taskkill.exe /T /F /PID $proc.Id *> $null
        try { $proc.WaitForExit(5000) | Out-Null } catch {}
        $killVerified = -not (Test-PidAlive -ProcId $proc.Id)
    }
}

$elapsed = $sw.Elapsed.TotalSeconds
if ($null -eq $status.initial_duration_sec) {
    $status.initial_duration_sec = [Math]::Round([Math]::Min($elapsed, $nominalSec), 3)
}
$status.final_duration_sec = [Math]::Round($elapsed, 3)

# Exit code: prefer the file the runner recorded (Start-Process ExitCode is unreliable on PS 5.1).
$exitCode = $null
if (Test-Path -LiteralPath $exitFile) {
    $raw = (Get-Content -LiteralPath $exitFile -Raw -ErrorAction SilentlyContinue)
    if ($raw) { $raw = $raw.Trim() }
    if ($raw -match '^-?\d+$') { $exitCode = [int]$raw }
}
if ($null -eq $exitCode) { try { if ($proc.HasExited) { $exitCode = $proc.ExitCode } } catch {} }

# --- parse the streamed result + classify terminal state --------------------------------------
$r = Get-StreamResult -OutFile $OutFile -ExpectSignal $ExpectSignal
$parsedSession = if ($r.SessionId) { [string]$r.SessionId } else { $sessionId }

# Resumable requires a session id AND that the prior child is confirmed gone: never resume a
# session whose old child might still be alive holding it (Opus review #5). On the error path the
# child exited on its own (kill_verified stays $null -ne $false), so that remains resumable.
$deadOrNotKilled = ($killVerified -ne $false)
if ($killed) {
    $status.status    = if ($killReason -eq 'timed_out') { 'timed_out' } else { 'killed' }
    $status.timed_out = ($killReason -eq 'timed_out')
    $status.is_error  = $true
    $status.resumable = ([bool]$parsedSession -and $deadOrNotKilled)
} elseif ($completionFailureReason) {
    $status.status    = 'error'
    $status.is_error  = $true
    $status.failure_reason = $completionFailureReason
    $status.resumable = ([bool]$parsedSession -and $deadOrNotKilled)
} elseif ($r.ResultFound -and -not $r.IsError -and
          (-not $ExpectSignal -or $r.SignalFound) -and
          ($null -eq $exitCode -or $exitCode -eq 0)) {
    $status.status    = 'completed'
    $status.is_error  = $false
    $status.resumable = $false
} else {
    $status.status    = 'error'
    $status.is_error  = $true
    $status.resumable = ([bool]$parsedSession -and $deadOrNotKilled)   # partial + session -> resumable once
    if (-not $r.ResultFound) { $status.failure_reason = 'worker_result_missing' }
    elseif ($r.IsError) { $status.failure_reason = 'worker_error' }
    elseif ($ExpectSignal -and -not $r.SignalFound) { $status.failure_reason = 'signal_missing' }
    elseif ($null -ne $exitCode -and $exitCode -ne 0) { $status.failure_reason = 'worker_exit_nonzero' }
    else { $status.failure_reason = 'worker_result_invalid' }
}

$status.exit_code     = $exitCode
$status.kill_reason   = $killReason
$status.kill_verified = $killVerified
$status.signal_found  = $r.SignalFound
$status.session_id    = $parsedSession
$status.finished_at   = ([datetime]::UtcNow).ToString('o')
if ($checkpointEnabled) {
    Update-DispatchCheckpoint -Status $status -Path $resolvedCheckpointPath | Out-Null
}

$preview = [string]$r.Result
if ($preview.Length -gt 280) { $preview = $preview.Substring(0, 280) + '...' }
$status.result_preview = $preview

Write-WorkerStatus -Status $status
$json = Write-WorkerSummary -Status $status
$json

} catch {
    # A supervisor fault is still a terminal outcome: record it rather than exiting silently.
    $supervisorFaulted = $true
    $faultMessage = $_.Exception.Message
    # Kill and VERIFY the whole child tree BEFORE publishing (peer findings PR-004, COD-003): a
    # session is only resumable when its original process tree -- root runner AND any orphaned
    # claude descendant -- is provably gone, otherwise a resume could run beside a live orphan.
    $childGone = $true
    try {
        if ($proc) {
            if (Test-PidAlive -ProcId $proc.Id) { try { $proc.WaitForExit(1) | Out-Null } catch {} }
            $childGone = Stop-ProcTree -RootPid $proc.Id
        }
    } catch { $childGone = $false }
    $status.status = 'error'
    $status.is_error = $true
    $status.failure_reason = 'supervisor_fault'
    $status.kill_verified = $childGone
    # The fault is in the supervisor, not the worker: with the tree verified dead, its assigned
    # session can be resumed once and the worker's partial checkpoint is not thrown away.
    $status.resumable = ([bool]$sessionId) -and $childGone
    $status.finished_at = ([datetime]::UtcNow).ToString('o')
    $status.result_preview = "supervisor fault: $faultMessage" + $(if (-not $childGone) { ' (child tree NOT verified dead; not resumable)' } else { '' })
    try { Write-WorkerStatus -Status $status } catch {
        # Last resort: the atomic writer itself failed. Leave a plain terminal record rather than
        # a stale 'running' one; a partially written JSON is still better than a lie.
        try { [IO.File]::WriteAllText($statusFile, ($status | ConvertTo-Json -Depth 6)) } catch {}
    }
    try { Write-WorkerSummary -Status $status | Out-Null } catch {}
} finally {
    # No survivor may outlive the supervisor: the orphan tree is what makes a lost handoff
    # unrecoverable. Kill the root tree AND orphaned descendants (a dead runner does not mean a
    # dead claude), then always release the concurrency guard.
    try {
        if ($proc) { $null = Stop-ProcTree -RootPid $proc.Id }
    } catch {}
    try { $dispatchMutex.ReleaseMutex() } catch {} finally { $dispatchMutex.Dispose() }
}

# Exit code contract: 0 completed clean | 124 timed_out/killed | else the worker's own code (or 1).
if ($supervisorFaulted) { exit 5 }
if ($status.status -eq 'completed') { exit 0 }
if ($killed) { exit 124 }
if ($null -ne $exitCode -and $exitCode -ne 0) { exit $exitCode }
exit 1

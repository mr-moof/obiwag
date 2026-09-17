<#
.SYNOPSIS
    Pure (side-effect-free) helpers for tools/dispatch-worker.ps1 (OPT-23 headless dispatch).

.DESCRIPTION
    Factored out so the persona-parsing, arg-building, and result-parsing logic is unit-testable
    without spawning a real `claude` worker. dispatch-worker.ps1 dot-sources this and owns the
    Start-Process / heartbeat / timeout / process-tree termination mechanics.
#>

$pathSafetyPath = Join-Path $PSScriptRoot 'path-safety.ps1'
if (-not (Test-Path -LiteralPath $pathSafetyPath -PathType Leaf)) {
    throw "dispatch worker path-safety library not found: $pathSafetyPath"
}
. $pathSafetyPath

function Get-PersonaParts {
    <#
    .SYNOPSIS Parse an agent persona .md into @{ Tools; Model; Effort; Body }.
    .DESCRIPTION Splits YAML frontmatter (between leading --- fences) from the body and extracts the
    `tools:`, `model:`, and `effort:` keys. Returns the whole text as Body when there is no
    frontmatter.
    #>
    param([Parameter(Mandatory)][string]$Raw)

    $tools = $null; $model = $null; $effort = $null; $body = $Raw
    if ($Raw -match '(?s)^\xEF?\xBB?\xBF?---\r?\n(.*?)\r?\n---\r?\n(.*)$') {
        $front = $Matches[1]; $body = $Matches[2]
        if ($front -match '(?m)^\s*tools:\s*(.+?)\s*$') { $tools = $Matches[1].Trim() }
        if ($front -match '(?m)^\s*model:\s*(.+?)\s*$') { $model = $Matches[1].Trim() }
        if ($front -match '(?m)^\s*effort:\s*(.+?)\s*$') { $effort = $Matches[1].Trim() }
    }
    return [ordered]@{ Tools = $tools; Model = $model; Effort = $effort; Body = $body }
}

. (Join-Path $PSScriptRoot 'dispatch-routing.ps1')

function Build-ClaudeArgs {
    <#
    .SYNOPSIS Build the `claude` argument array for a headless worker (OPT-23 / issue #200 SS1).
    .DESCRIPTION Uses --tools to restrict the toolset and --append-system-prompt for the persona
    body. Adds --model and --effort only when the persona declares them. Wall-clock and idle limits
    are enforced by dispatch-worker.ps1; Claude Code has no --max-turns option.

    Output mode:
      * default        -> --output-format json (one blob at the end; back-compat for Read-WorkerResult)
      * -StreamJson     -> --output-format stream-json --verbose --include-partial-messages, so the
        supervisor can watch OUTPUT GROW during the run (liveness) instead of a single terminal blob.

    Session identity (issue #200 SS1):
      * -SessionId <uuid>       -> --session-id, so the supervisor ASSIGNS the id up front and knows it
        without scraping the stream (robust). Recorded in the dispatch status for resume.
      * -ResumeSessionId <uuid> -> --resume <id>; the existing session already carries its system
        prompt, so --append-system-prompt / --session-id are NOT re-sent. $Prompt is a short
        continue-instruction (issue #200 SS2: resume from checkpoint, never blind prompt replay).
    #>
    param(
        [string]$Prompt,
        [Parameter(Mandatory)][string]$Body,
        [string]$Tools,
        [string]$Model,
        [string]$Effort,
        [switch]$StreamJson,
        [string]$SessionId,
        [string]$ResumeSessionId
    )
    if ($ResumeSessionId) {
        $a = @('--resume', $ResumeSessionId, '-p', $Prompt)
    } else {
        $a = @('-p', $Prompt, '--append-system-prompt', $Body)
        if ($SessionId) { $a += @('--session-id', $SessionId) }
    }
    if ($StreamJson) {
        $a += @('--output-format', 'stream-json', '--verbose', '--include-partial-messages')
    } else {
        $a += @('--output-format', 'json')
    }
    if ($Tools) { $a += @('--tools') + ($Tools -split '\s*,\s*' | Where-Object { $_ }) }
    if ($Model) { $a += @('--model', $Model) }
    if ($Effort) { $a += @('--effort', $Effort) }
    return $a
}

function Read-WorkerResult {
    <#
    .SYNOPSIS Parse a claude --output-format json worker output file into @{ Result; IsError; SignalFound }.
    .DESCRIPTION On timeout or unparseable/missing output, returns IsError=$true. When ExpectSignal is
    given, SignalFound is true iff the worker's result text contains it.
    #>
    param([string]$OutFile, [bool]$TimedOut = $false, [string]$ExpectSignal)

    $result = ''; $isError = $true; $signalFound = $false
    if (-not $TimedOut -and $OutFile -and (Test-Path $OutFile)) {
        try {
            $j = Get-Content $OutFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $result = [string]$j.result
            $isError = [bool]$j.is_error
        } catch {
            $result = '<unparseable worker output>'; $isError = $true
        }
    }
    if ($ExpectSignal -and $result -and $result.Contains($ExpectSignal)) { $signalFound = $true }
    return [ordered]@{ Result = $result; IsError = $isError; SignalFound = $signalFound }
}

function Get-StreamResult {
    <#
    .SYNOPSIS Parse a `claude --output-format stream-json` output file (issue #200 SS1).
    .DESCRIPTION Reads newline-delimited JSON events and returns the terminal `result` event's
    text + is_error, plus any session_id seen (the init/system event echoes it). On a partial /
    killed stream with no `result` event, ResultFound is $false and IsError is $true -- but the
    session id may still be recovered here (or was assigned a priori via --session-id), which is
    what makes the run resumable (issue #200 SS2). Never throws on a malformed line.
    #>
    param([string]$OutFile, [string]$ExpectSignal)

    $result = ''; $isError = $true; $sessionId = ''; $resultFound = $false; $signalFound = $false
    if ($OutFile -and (Test-Path -LiteralPath $OutFile)) {
        foreach ($line in (Get-Content -LiteralPath $OutFile -Encoding UTF8)) {
            $line = $line.Trim()
            if (-not $line -or $line[0] -ne '{') { continue }
            try { $obj = $line | ConvertFrom-Json } catch { continue }
            if ($obj.session_id) { $sessionId = [string]$obj.session_id }
            if ($obj.type -eq 'result') {
                $resultFound = $true
                $result = [string]$obj.result
                $isError = [bool]$obj.is_error
            }
        }
    }
    if ($ExpectSignal -and $result -and $result.Contains($ExpectSignal)) { $signalFound = $true }
    return [ordered]@{
        Result = $result; IsError = $isError; SessionId = $sessionId
        ResultFound = $resultFound; SignalFound = $signalFound
    }
}

function Get-CheckpointInfo {
    <#
    .SYNOPSIS Observed state of the worker's DELIVERABLE (dogfood DF-07).
    .DESCRIPTION Stream growth proves the provider is alive; it does not prove the required
    artifact moved. This returns the normalized path plus existence/bytes/mtime so the supervisor
    can measure artifact progress and the status record can carry real evidence instead of the
    old always-empty `checkpoint: ''`. Returns $null when no path was supplied -- the status must
    never carry a meaningless empty checkpoint field.
    #>
    param([string]$Path)
    if (-not $Path) { return $null }
    $full = $Path
    try { $full = [System.IO.Path]::GetFullPath($Path) } catch { }
    $info = [ordered]@{ path = $full; exists = $false; bytes = 0; mtime = '' }
    try {
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $f = Get-Item -LiteralPath $full -ErrorAction Stop
            $info.exists = $true
            $info.bytes  = [long]$f.Length
            $info.mtime  = $f.LastWriteTimeUtc.ToString('o')
        }
    } catch { }
    return $info
}

function Update-DispatchCheckpoint {
    <# .SYNOPSIS Refresh a status record's checkpoint block in place; returns the new info. #>
    param([Parameter(Mandatory)]$Status, [string]$Path)
    $info = Get-CheckpointInfo -Path $Path
    if ($null -ne $info) { $Status.checkpoint = $info }
    return $info
}

function Get-CheckpointWindowSec {
    <#
    .SYNOPSIS The artifact-progress window: 25% of the run's deadline (never below 1 s).
    .DESCRIPTION Proportional so a short test run and a real 270 s phase use the same rule.
    #>
    param([Parameter(Mandatory)][int]$DeadlineSec)
    return [Math]::Max(1.0, [double]$DeadlineSec * 0.25)
}

function Test-CheckpointDeliverable {
    <#
    .SYNOPSIS Pure advisory artifact-health verdict (dogfood DF-05/DF-06).
    .DESCRIPTION Returns '' while the deliverable is healthy or inside its grace window, else:
      checkpoint_missing - past the first window with no non-empty checkpoint;
      artifact_stalled   - a non-empty checkpoint has not advanced within a full window.
    These are observability signals. The supervisor decides liveness from stream, CPU, and
    artifact activity and never kills a productive worker merely because this function reports
    checkpoint debt.
    #>
    param(
        [Parameter(Mandatory)][double]$ElapsedSec,
        [Parameter(Mandatory)][int]$DeadlineSec,
        [long]$Bytes = 0,
        [double]$SinceArtifactProgressSec = 0,
        [bool]$CheckpointEstablished = $false
    )
    $window = Get-CheckpointWindowSec -DeadlineSec $DeadlineSec
    if ($Bytes -lt 1) {
        if ($ElapsedSec -ge $window) { return 'checkpoint_missing' }
        return ''
    }
    # The supervisor marks a checkpoint established when it first observes non-empty content and
    # restarts the artifact-progress clock at that boundary. Do not call the first write stalled.
    if ($CheckpointEstablished -and $SinceArtifactProgressSec -ge $window) {
        return 'artifact_stalled'
    }
    return ''
}

function Test-CheckpointPath {
    <#
    .SYNOPSIS Validate an optional deliverable-checkpoint path before launch.
    .DESCRIPTION The supervisor must never enforce artifact progress against a path the worker
    could not possibly write. Rejects wildcards, a path that already exists as a directory, a
    missing parent directory, an existing reparse-point leaf, and (when RepositoryRoot is supplied)
    a path whose resolved parent escapes that root. Returns @{ Valid; Reason; Full }.
    #>
    param([string]$Path, [string]$RepositoryRoot)
    if (-not $Path) { return [ordered]@{ Valid = $true; Reason = ''; Full = '' } }
    if ($Path -match '[\*\?]') {
        return [ordered]@{ Valid = $false; Reason = "checkpoint path '$Path' contains wildcards"; Full = '' }
    }
    $full = $null
    try {
        $candidate = if ([System.IO.Path]::IsPathRooted($Path) -or -not $RepositoryRoot) {
            $Path
        } else {
            Join-Path $RepositoryRoot $Path
        }
        $full = [System.IO.Path]::GetFullPath($candidate)
    }
    catch { return [ordered]@{ Valid = $false; Reason = "checkpoint path '$Path' is not a valid path: $($_.Exception.Message)"; Full = '' } }
    if (Test-Path -LiteralPath $full -PathType Container) {
        return [ordered]@{ Valid = $false; Reason = "checkpoint path '$full' is a directory"; Full = $full }
    }
    $leafItem = Get-Item -LiteralPath $full -Force -ErrorAction SilentlyContinue
    if ($leafItem -and
        ($leafItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        return [ordered]@{ Valid = $false; Reason = "checkpoint path has reparse-point leaf '$full'"; Full = $full }
    }
    $parent = Split-Path -Parent $full
    if ($parent -and -not (Test-Path -LiteralPath $parent -PathType Container)) {
        return [ordered]@{ Valid = $false; Reason = "checkpoint parent directory does not exist: $parent"; Full = $full }
    }
    if ($RepositoryRoot) {
        try {
            $resolvedRoot = ConvertTo-NormalPath -Path $RepositoryRoot
            $resolvedParent = ConvertTo-NormalPath -Path $parent
            if (-not (Test-NormalPathContained -Candidate $resolvedParent -Root $resolvedRoot)) {
                return [ordered]@{ Valid = $false; Reason = "checkpoint path '$full' escapes repository root '$resolvedRoot'"; Full = $full }
            }
            $reparsePoint = Get-ReparsePointInPath -Path $resolvedParent -Root $resolvedRoot
            if ($reparsePoint) {
                return [ordered]@{ Valid = $false; Reason = "checkpoint path crosses reparse-point component '$reparsePoint'"; Full = $full }
            }
            $full = Join-Path $resolvedParent (Split-Path -Leaf $full)
        } catch {
            return [ordered]@{ Valid = $false; Reason = "checkpoint containment could not be verified: $($_.Exception.Message)"; Full = $full }
        }
    }
    return [ordered]@{ Valid = $true; Reason = ''; Full = $full }
}

function Test-DispatchKey {
    <#
    .SYNOPSIS Validate an optional capacity-decomposition dispatch key such as `1c1`.
    .DESCRIPTION A chunk key isolates FILENAMES and the status record for one chunk of a
    decomposed phase while the canonical numeric phase identity is unchanged. Shape is
    `<phase>c<k>` with k in 1..3 -- the three-chunk cap is mechanical, so a fourth chunk cannot be
    dispatched at all. The numeric prefix MUST equal the canonical phase or the key is rejected.
    Returns @{ Valid; Reason }.
    #>
    param([string]$Key, [Parameter(Mandatory)][int]$Phase)
    if (-not $Key) { return [ordered]@{ Valid = $true; Reason = '' } }
    if ($Key -notmatch '^([0-9]{1,3})c([1-3])$') {
        return [ordered]@{ Valid = $false; Reason = "dispatch key '$Key' is not <phase>c<1-3> (max three chunks per phase)" }
    }
    if ([int]$Matches[1] -ne $Phase) {
        return [ordered]@{ Valid = $false; Reason = "dispatch key '$Key' does not match canonical phase $Phase" }
    }
    return [ordered]@{ Valid = $true; Reason = '' }
}

function Test-CapacityMismatchEvidence {
    <#
    .SYNOPSIS Decide whether one terminal status proves deadline-edge provider progress.
    .DESCRIPTION Terminal finalization no longer fabricates progress at/after the deadline. A real
    watchdog observes only on poll boundaries, so capacity evidence is: timed_out, verified kill,
    at least one observed stream/CPU progress event, and the last event within one poll interval
    plus one second of the deadline. Missing/new fields fail closed.
    #>
    param([Parameter(Mandatory)]$Status)
    try {
        if ([string]$Status.status -ne 'timed_out' -or -not [bool]$Status.kill_verified) { return $false }
        if ([int]$Status.progress_count -lt 1 -or [int]$Status.poll_sec -lt 1) { return $false }
        if (-not $Status.last_progress_at -or -not $Status.deadline_at -or -not $Status.finished_at) { return $false }
        $last = ([datetime]$Status.last_progress_at).ToUniversalTime()
        $deadline = ([datetime]$Status.deadline_at).ToUniversalTime()
        $finished = ([datetime]$Status.finished_at).ToUniversalTime()
        $threshold = $deadline.AddSeconds(-([int]$Status.poll_sec + 1))
        return ($last -ge $threshold -and $last -le $finished)
    } catch { return $false }
}

function Get-DispatchPhasePolicy {
    <# .SYNOPSIS Resolve and fail-closed validate one phase's Claude budget. #>
    param(
        [Parameter(Mandatory)][int]$Phase,
        [Parameter(Mandatory)][string]$PhaseTablePath,
        [int]$TimeoutOverrideSec = 0,
        [int]$IdleTimeoutOverrideSec = 0
    )
    $policy = [ordered]@{
        nominal_work_sec = 270
        idle_sec = 90
        productive_extension_sec = 0
        finalization_sec = 0
        absolute_sec = 270
        recent_progress_sec = 15
        cleanup_margin_sec = 30
        source = 'compatibility'
    }
    if ($Phase -gt 0) {
        if (-not (Test-Path -LiteralPath $PhaseTablePath -PathType Leaf)) {
            throw "phase policy source not found: $PhaseTablePath"
        }
        try { $table = Get-Content -LiteralPath $PhaseTablePath -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { throw "phase policy source is invalid JSON: $($_.Exception.Message)" }
        $row = @($table.phases | Where-Object { [int]$_.n -eq $Phase }) | Select-Object -First 1
        if (-not $row) { throw "phase $Phase is not defined in phase-table.json" }
        $p = if ($row.delegation_policy -and $row.delegation_policy.claude) {
            $row.delegation_policy.claude
        } else {
            $table.delegation_policy_defaults.claude
        }
        if (-not $p) {
            throw "phase $Phase has no Claude phase policy or delegation_policy_defaults fallback"
        }
        $required = @('nominal_work_sec','idle_sec','productive_extension_sec','finalization_sec',
                      'absolute_sec','recent_progress_sec','cleanup_margin_sec')
        $missing = @($required | Where-Object { $_ -notin $p.PSObject.Properties.Name })
        if ($missing.Count -gt 0) {
            throw "phase $Phase Claude policy is missing: $($missing -join ', ')"
        }
        $policy = [ordered]@{
            nominal_work_sec = [int]$p.nominal_work_sec
            idle_sec = [int]$p.idle_sec
            productive_extension_sec = [int]$p.productive_extension_sec
            finalization_sec = [int]$p.finalization_sec
            absolute_sec = [int]$p.absolute_sec
            recent_progress_sec = [int]$p.recent_progress_sec
            cleanup_margin_sec = [int]$p.cleanup_margin_sec
            source = if ($row.delegation_policy -and $row.delegation_policy.claude) {
                'phase-table'
            } else {
                'phase-table-default'
            }
        }
    }
    foreach ($name in @('nominal_work_sec','idle_sec','absolute_sec','recent_progress_sec','cleanup_margin_sec')) {
        if ([int]$policy[$name] -lt 1) { throw "phase $Phase Claude policy $name must be positive" }
    }
    foreach ($name in @('productive_extension_sec','finalization_sec')) {
        if ([int]$policy[$name] -lt 0) { throw "phase $Phase Claude policy $name cannot be negative" }
    }
    $sum = [int]$policy.nominal_work_sec + [int]$policy.productive_extension_sec + [int]$policy.finalization_sec
    if ($sum -ne [int]$policy.absolute_sec) {
        throw "phase $Phase Claude policy absolute_sec must equal nominal + extension + finalization"
    }
    if ($TimeoutOverrideSec -gt 0) {
        if ($Phase -gt 0 -and $TimeoutOverrideSec -gt [int]$policy.absolute_sec) {
            throw "TimeoutSec $TimeoutOverrideSec exceeds phase $Phase absolute cap $($policy.absolute_sec)"
        }
        $policy.nominal_work_sec = $TimeoutOverrideSec
        $policy.productive_extension_sec = 0
        $policy.finalization_sec = 0
        $policy.absolute_sec = $TimeoutOverrideSec
        $policy.source = 'explicit-timeout-override'
    }
    if ($IdleTimeoutOverrideSec -gt 0) { $policy.idle_sec = $IdleTimeoutOverrideSec }
    return $policy
}

function Get-ProductiveExtensionDecision {
    <# .SYNOPSIS Fail-closed nominal-cutoff decision for the one productive extension. #>
    param(
        [Parameter(Mandatory)]$Status,
        [Parameter(Mandatory)]$Policy,
        [Parameter(Mandatory)][datetime]$ObservedAtUtc,
        [Parameter(Mandatory)][double]$IdleForSec,
        [Parameter(Mandatory)][long]$CheckpointStartBytes,
        [Parameter(Mandatory)][long]$CheckpointBytes,
        [bool]$CheckpointContained = $false,
        [bool]$ExtensionAlreadyUsed = $false
    )
    $recentCutoff = $ObservedAtUtc.AddSeconds(-[int]$Policy.recent_progress_sec)
    $streamRecent = $false; $cpuRecent = $false
    try { if ($Status.stream_last_activity_at) { $streamRecent = ([datetime]$Status.stream_last_activity_at).ToUniversalTime() -ge $recentCutoff } } catch { }
    try { if ($Status.cpu_last_activity_at) { $cpuRecent = ([datetime]$Status.cpu_last_activity_at).ToUniversalTime() -ge $recentCutoff } } catch { }
    $checkpointGrew = ($CheckpointBytes -gt $CheckpointStartBytes)
    $reason = 'granted'
    $grant = $true
    if ($ExtensionAlreadyUsed) { $grant = $false; $reason = 'extension_already_used' }
    elseif ([int]$Policy.productive_extension_sec -lt 1) { $grant = $false; $reason = 'extension_disabled' }
    elseif ($IdleForSec -ge [int]$Policy.idle_sec) { $grant = $false; $reason = 'idle_at_nominal_cutoff' }
    elseif (-not ($streamRecent -or $cpuRecent)) { $grant = $false; $reason = 'no_recent_stream_or_cpu_progress' }
    elseif (-not $CheckpointContained) { $grant = $false; $reason = 'checkpoint_not_contained' }
    elseif ($CheckpointBytes -lt 1) { $grant = $false; $reason = 'checkpoint_empty' }
    elseif (-not $checkpointGrew) { $grant = $false; $reason = 'checkpoint_did_not_grow' }
    return [ordered]@{
        grant = $grant; reason = $reason
        extension_duration_sec = if ($grant) { [int]$Policy.productive_extension_sec } else { 0 }
        progress_at_cutoff = [ordered]@{
            observed_at = $ObservedAtUtc.ToString('o'); stream_recent = $streamRecent
            cpu_recent = $cpuRecent; idle_for_sec = [Math]::Round($IdleForSec, 3)
            checkpoint_start_bytes = $CheckpointStartBytes; checkpoint_bytes = $CheckpointBytes
            checkpoint_grew = $checkpointGrew; checkpoint_contained = $CheckpointContained
        }
    }
}

function Get-DispatchOuterTimeoutSec {
    param([Parameter(Mandatory)]$Policy)
    return ([int]$Policy.absolute_sec + [int]$Policy.cleanup_margin_sec)
}

function Get-DispatchBudgetInstruction {
    <# .SYNOPSIS Build the worker's bounded work/extension/finalization instruction. #>
    param([Parameter(Mandatory)]$Policy, [string]$CheckpointPath = '')
    $nominal = [int]$Policy.nominal_work_sec
    $extension = [int]$Policy.productive_extension_sec
    $finalization = [int]$Policy.finalization_sec
    $absolute = [int]$Policy.absolute_sec
    $finalizationStart = $nominal + $extension
    $checkpointText = if ($CheckpointPath) {
        "Keep the required checkpoint non-empty and growing before the nominal boundary: $CheckpointPath"
    } else {
        'No productive extension is available unless the dispatch has a repository-contained required checkpoint.'
    }
    $lines = @(
        'DELEGATED PHASE BUDGET (supervisor-enforced):'
        "- Nominal work window: $nominal seconds. $checkpointText"
    )
    if ($extension -gt 0) {
        $lines += "- One extension of at most $extension seconds is possible only with recent stream/CPU progress and observed checkpoint growth."
        $lines += '- At the nominal boundary, stop tools immediately and finalize if that extension evidence is not all present.'
        $lines += "- Stop all tool calls no later than $finalizationStart seconds after launch."
    } else {
        $lines += "- Stop all tool calls before the $absolute-second absolute deadline."
    }
    if ($finalization -gt 0) {
        $lines += "- Reserve the final $finalization seconds for reconciling source/tests, writing the checkpoint, and returning the exact accepted signal."
    }
    $lines += "- Return by the absolute $absolute-second cap; idle detection can end the run earlier."
    return ($lines -join "`n")
}

function New-DispatchStatus {
    <#
    .SYNOPSIS Build the authoritative dispatch STATUS record (issue #200 SS1).
    .DESCRIPTION The orchestrator reads this to drive state-driven recovery (issue #200 SS2)
    instead of blindly replaying a prompt. Fields cover the full state machine:
    launch -> running lease -> streamed progress -> completed, and
    running lease -> idle/deadline -> scoped kill -> kill verified -> resume once or halt.

    `last_progress_at` is the last observed STREAM/CPU progress and is never overwritten by
    terminal finalization; terminal wall-clock lives in `finished_at` (dogfood DF-10). Without
    that split every hard timeout looked like it progressed past its own deadline, which is the
    capacity-mismatch predicate.

    `checkpoint` is $null when no deliverable path was supplied and a real {path, exists, bytes,
    mtime} object when one was (dogfood DF-07) -- never an empty string.
    #>
    param(
        [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][int]$Phase,
        [string]$Persona = '',
        [int]$Attempt = 1,
        [string]$SessionId = '',
        [string]$OutFile = '',
        [string]$ErrFile = '',
        [int]$DeadlineSec = 270,
        [int]$PollSec = 10,
        [string]$CheckpointPath = '',
        [string]$DispatchKey = '',
        [string]$CurrentStatusPath = '',
        [string]$AttemptStatusPath = '',
        [string]$CurrentSummaryPath = '',
        [string]$AttemptSummaryPath = '',
        [string]$SystemPath = '',
        [string]$RoutingTier = '',
        [string]$Model = '',
        [string]$Effort = '',
        $PhasePolicy = $null
    )
    $now = [datetime]::UtcNow
    $checkpoint = Get-CheckpointInfo -Path $CheckpointPath
    $checkpointHealth = 'not_required'
    if ($null -ne $checkpoint) {
        $checkpointHealth = if ([long]$checkpoint.bytes -gt 0) { 'healthy' } else { 'grace' }
    }
    return [ordered]@{
        schema_version   = 3
        run_id           = $RunId
        phase            = $Phase
        dispatch_key     = if ($DispatchKey) { $DispatchKey } else { [string]$Phase }
        persona          = $Persona
        routing_tier     = if ($RoutingTier) { $RoutingTier } else { $null }
        model            = $Model
        effort           = $Effort
        input_usage      = 'unknown'
        attempt          = $Attempt
        proc_pid         = $null
        session_id       = $SessionId
        status           = 'launching'
        started_at       = $now.ToString('o')
        deadline_at      = $now.AddSeconds($(if ($PhasePolicy) { [int]$PhasePolicy.nominal_work_sec + [int]$PhasePolicy.finalization_sec } else { $DeadlineSec })).ToString('o')
        lease_expires_at = $now.AddSeconds($(if ($PhasePolicy) { [int]$PhasePolicy.absolute_sec } else { $DeadlineSec })).ToString('o')
        nominal_deadline_at = $now.AddSeconds($(if ($PhasePolicy) { [int]$PhasePolicy.nominal_work_sec } else { $DeadlineSec })).ToString('o')
        finalization_due_at = $now.AddSeconds($(if ($PhasePolicy) { [int]$PhasePolicy.nominal_work_sec + [int]$PhasePolicy.productive_extension_sec } else { $DeadlineSec })).ToString('o')
        finalization_started_at = $null
        absolute_deadline_at = $now.AddSeconds($(if ($PhasePolicy) { [int]$PhasePolicy.absolute_sec } else { $DeadlineSec })).ToString('o')
        phase_policy     = $PhasePolicy
        outer_timeout_sec = if ($PhasePolicy) { Get-DispatchOuterTimeoutSec -Policy $PhasePolicy } else { $DeadlineSec + 30 }
        extension_decision = 'not_reached'
        extension_reason = ''
        extension_duration_sec = 0
        progress_at_cutoff = $null
        initial_duration_sec = $null
        final_duration_sec = $null
        last_progress_at = $now.ToString('o')
        progress_count   = 0
        stream_last_activity_at = $null
        stream_progress_count = 0
        stream_bytes     = 0
        cpu_last_activity_at = $null
        cpu_progress_count = 0
        cpu_time_ms      = $null
        artifact_last_activity_at = $null
        artifact_progress_count = 0
        poll_sec         = $PollSec
        finished_at      = $null
        out_file         = $OutFile
        err_file         = $ErrFile
        current_status_file = $CurrentStatusPath
        attempt_status_file = $AttemptStatusPath
        current_summary_file = $CurrentSummaryPath
        attempt_summary_file = $AttemptSummaryPath
        system_file      = $SystemPath
        checkpoint       = $checkpoint
        checkpoint_health = $checkpointHealth
        exit_code        = $null
        is_error         = $false
        timed_out        = $false
        kill_reason      = ''
        failure_reason   = ''
        kill_verified    = $null
        signal_found     = $false
        resumable        = $false
        result_preview   = ''
    }
}

function Write-DispatchStatus {
    <# .SYNOPSIS Persist a dispatch status record ATOMICALLY (temp file + rename).
       Every durability step is TERMINATING: the supervisor runs under
       $ErrorActionPreference = 'Continue', so a plain non-terminating cmdlet error here would
       be printed and ignored, the caller would believe the terminal record landed, and the
       status could stay 'running' forever (peer finding PR-002, run 20260901T221915Z). #>
    param([Parameter(Mandatory)]$Status, [Parameter(Mandatory)][string]$Path)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
    }
    $tmp = "$Path.tmp"
    ($Status | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $tmp -Encoding UTF8 -ErrorAction Stop
    Move-Item -LiteralPath $tmp -Destination $Path -Force -ErrorAction Stop
}

function Read-DispatchStatus {
    <# .SYNOPSIS Read a dispatch status record; $null when absent or unparseable. #>
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { return $null }
}

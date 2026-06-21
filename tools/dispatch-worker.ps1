<#
.SYNOPSIS
    Run an Obi phase as a backgrounded headless `claude -p` worker (OPT-23, #196).

.DESCRIPTION
    Non-blocking dispatch: instead of the orchestrator blocking inside an opaque `Agent` tool call
    (where a silent IPC drop has no timer and no recovery — the OPT-22 stall class), a dispatch
    phase runs as a headless `claude -p` subprocess that this script BOUNDS with a hard timeout and
    kills on expiry. The orchestrator launches it via `run_in_background` and is freed; the harness
    notifies on completion. A hang becomes a timeout result, never a silent freeze.

    Isolation: workers run WITHOUT `--bare` (which disables OAuth on this box — see memory
    `claude-bare-strips-oauth`) and instead set `OBI_WORKER=1`, which makes the five state-touching
    hooks no-op (see `hooks/core/worker_guard.py`) so the worker does not collide with the parent
    run's heartbeat / session state.

    The persona's declared tools are passed via `--tools` (RESTRICTS the toolset — distinct from
    `--allowedTools`, which only auto-approves). The persona body becomes the system prompt
    (`--append-system-prompt`); the task is the `-p` prompt.

.PARAMETER Persona
    Agent name, e.g. `obi-discovery`. Resolved from the deployed `~/.claude/agents/<name>.md` first,
    then the source `platforms/claude-code/agents/<name>.md`.

.PARAMETER PromptFile
    Path to a file containing the task prompt (the per-phase dispatch context).

.PARAMETER RunId / .PARAMETER Phase
    Run id + phase number — used to name the run-scoped output file.

.PARAMETER TimeoutSec
    Hard wall-clock budget. On expiry the worker job is stopped and best-effort killed; the script
    reports `timed_out: true` so the orchestrator can retry or halt. Default 900 (15 min).

.PARAMETER MaxTurns
    `--max-turns` cap (runaway backstop). Default 80.

.PARAMETER ExpectSignal
    Optional completion signal (e.g. `DISCOVERY COMPLETE`) — the script reports whether the worker's
    result contains it (`signal_found`). The orchestrator still applies its own Step-1 detection.

.PARAMETER OutFile / .PARAMETER PersonaPath
    Optional overrides for the worker JSON output path and the persona file path.

.OUTPUTS
    A summary JSON object on stdout: persona, phase, exit_code, timed_out, is_error, signal_found,
    result_preview, out_file. The full `claude` JSON is written to OutFile.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Persona,
    [Parameter(Mandatory)][string]$PromptFile,
    [string]$RunId = 'adhoc',
    [int]$Phase = 0,
    [int]$TimeoutSec = 900,
    [int]$MaxTurns = 80,
    [string]$ExpectSignal,
    [string]$OutFile,
    [string]$PersonaPath
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'lib\dispatch-worker-lib.ps1')

# --- Resolve persona file (deployed runtime location first, then source layout) ---
if (-not $PersonaPath) {
    $candidates = @(
        (Join-Path $HOME ".claude\agents\$Persona.md"),
        (Join-Path (Split-Path -Parent $ScriptDir) "platforms\claude-code\agents\$Persona.md")
    )
    $PersonaPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $PersonaPath -or -not (Test-Path $PersonaPath)) {
    throw "persona '$Persona' not found (looked in ~/.claude/agents and platforms/claude-code/agents)"
}
if (-not (Test-Path $PromptFile)) { throw "prompt file not found: $PromptFile" }

# --- Parse persona (pure helper, unit-tested in the lib). Body -> system prompt (injected ONCE by
#     the runner via --append-system-prompt); task -> -p prompt. No double-injection. ---
$parts = Get-PersonaParts -Raw (Get-Content $PersonaPath -Raw -Encoding UTF8)

if (-not $OutFile) { $OutFile = Join-Path $PWD ".obi\state\worker-$RunId-$Phase.json" }
$outDir = Split-Path -Parent $OutFile
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$errFile     = "$OutFile.err"
$summaryFile = Join-Path $outDir "worker-summary-$RunId-$Phase.json"
$bodyFile    = Join-Path $outDir "worker-sys-$RunId-$Phase.txt"
Set-Content -LiteralPath $bodyFile -Value $parts.Body -Encoding UTF8

# --- Run bounded via a child powershell.exe (clean PID for a SCOPED tree-kill on timeout) ---
# pwsh may be absent; use Windows PowerShell. The runner sets OBI_WORKER=1 and
# separates stdout(JSON)/stderr(logs). Large strings travel via files, never the arg line.
$runner  = Join-Path $ScriptDir 'lib\dispatch-worker-runner.ps1'
$libPath = Join-Path $ScriptDir 'lib\dispatch-worker-lib.ps1'
$psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner,
            '-BodyFile', $bodyFile, '-TaskFile', $PromptFile, '-OutFile', $OutFile,
            '-ErrFile', $errFile, '-LibPath', $libPath, '-MaxTurns', $MaxTurns)
if ($parts.Tools) { $psArgs += @('-Tools', (($parts.Tools -split '\s*,\s*' | Where-Object { $_ }) -join ',')) }
if ($parts.Model) { $psArgs += @('-Model', $parts.Model) }

$proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -PassThru -WindowStyle Hidden
$exited = $proc.WaitForExit($TimeoutSec * 1000)
$timedOut = $false; $exitCode = $null
if (-not $exited) {
    $timedOut = $true
    # Scoped kill: ONLY this worker's process tree (the child powershell + its claude/node), by PID.
    # No broad node.exe match — that could kill the parent orchestrator or a concurrent run.
    & taskkill.exe /T /F /PID $proc.Id *> $null
} else {
    $exitCode = $proc.ExitCode
}

# --- Parse the worker result (pure helper); fold a nonzero exit / timeout into is_error ---
$r = Read-WorkerResult -OutFile $OutFile -TimedOut $timedOut -ExpectSignal $ExpectSignal
$result = [string]$r.Result
$isError = [bool]$r.IsError -or $timedOut -or ($null -ne $exitCode -and $exitCode -ne 0)

$summary = [ordered]@{
    persona        = $Persona
    phase          = $Phase
    exit_code      = $exitCode
    timed_out      = $timedOut
    is_error       = $isError
    signal_found   = $r.SignalFound
    expect_signal  = $ExpectSignal
    out_file       = $OutFile
    err_file       = $errFile
    summary_file   = $summaryFile
    result_preview = if ($result.Length -gt 280) { $result.Substring(0, 280) + '...' } else { $result }
}
$json = $summary | ConvertTo-Json -Depth 4
Set-Content -LiteralPath $summaryFile -Value $json -Encoding UTF8
$json

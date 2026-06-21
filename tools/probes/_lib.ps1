<#
.SYNOPSIS
    Shared probe library for /obi-auto-max prereq lock-in (Gate 3).

.DESCRIPTION
    Provides:
    - Invoke-GhApi: gh api wrapper with stderr -> structured status classification.
    - Write-ProbeOutput: appends a fixed-schema JSON line to .obi/runtime/probes-<UTC>.jsonl
      and echoes a single-line summary on stdout for the orchestrator.
    - New-ProbeResult: helper for probe scripts to assemble the result hashtable.

    Status enum (returned in 'status' field of every probe result):
        ok | auth_failure | not_found | network_failure | unknown

    Probe scripts dot-source this file:
        . "$PSScriptRoot\_lib.ps1"

.NOTES
    Requires PowerShell 5.1+ and gh on PATH. Uses ASCII-only source (no em-dashes)
    to stay safe under PS 5.1 BOM-less UTF-8 decoding.
#>

Set-StrictMode -Version Latest

function Invoke-GhApi {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $gh = Get-Command -Name gh -ErrorAction SilentlyContinue
    if (-not $gh) {
        return @{
            ok       = $false
            status   = 'unknown'
            stdout   = ''
            stderr   = 'gh not on PATH'
            exitCode = -1
        }
    }

    # Save / restore EAP because PS 5.1 wraps native stderr as ErrorRecord
    # under EAP=Stop and crashes even on successful native exits.
    # Use a per-invocation unique stderr file so concurrent probes (or
    # concurrent /obi-auto-max sessions) cannot clobber each other.
    $oldEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $stderrFile = Join-Path $env:TEMP ("gh-probe-stderr-" + [guid]::NewGuid().ToString('N') + ".txt")
    try {
        $stdout = & gh api $Path 2>$stderrFile
        $exitCode = $LASTEXITCODE
        $stderr = ''
        if (Test-Path -LiteralPath $stderrFile) {
            $stderr = (Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue)
            if ($null -eq $stderr) { $stderr = '' }
        }
    } finally {
        if (Test-Path -LiteralPath $stderrFile) {
            Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
        }
        $ErrorActionPreference = $oldEap
    }

    $status = Resolve-ProbeStatus -ExitCode $exitCode -Stderr $stderr
    return @{
        ok       = ($exitCode -eq 0)
        status   = $status
        stdout   = ($stdout -join "`n")
        stderr   = $stderr
        exitCode = $exitCode
    }
}

function Resolve-ProbeStatus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [int]$ExitCode,
        [string]$Stderr = ''
    )
    if ($ExitCode -eq 0) { return 'ok' }
    $s = $Stderr.ToLower()
    if ($s -match 'not authorized|401|403|unauthorized|forbidden') { return 'auth_failure' }
    if ($s -match '404|not found|does not exist')                   { return 'not_found' }
    if ($s -match 'connection refused|timeout|dns|network')         { return 'network_failure' }
    return 'unknown'
}

function New-ProbeResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string]$Probe,
        [Parameter(Mandatory)] [string]$Status,
        [hashtable]$InputData = @{},
        [hashtable]$Data      = @{},
        [string]$ErrorText    = ''
    )
    # Note: parameter is $InputData (not $Input) because $Input is a
    # PowerShell automatic variable for pipeline input. Clobbering it
    # causes pipeline output to coalesce with the returned hashtable.
    $hashtable = @{
        probe  = $Probe
        ts     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        status = $Status
        input  = $InputData
        data   = $Data
        error  = $ErrorText
    }
    # Wrap in unary array operator and unwrap to suppress empty-pipeline objects.
    , $hashtable | Select-Object -First 1
}

function Format-ProbeSummary {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [hashtable]$Result
    )
    "PROBE $($Result.probe) status=$($Result.status) ts=$($Result.ts)"
}

function Write-ProbeOutput {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [hashtable]$Result,
        [string]$JsonlPath = '',
        [switch]$Quiet
    )
    if (-not $JsonlPath) {
        $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $runtimeDir = Join-Path $repoRoot '.obi\runtime'
        if (-not (Test-Path $runtimeDir)) {
            New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
        }
        $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
        $JsonlPath = Join-Path $runtimeDir "probes-$stamp.jsonl"
    }

    $jsonLine = $Result | ConvertTo-Json -Compress -Depth 6
    Add-Content -Path $JsonlPath -Value $jsonLine -Encoding UTF8

    # Print single-line summary on the host stream (not the output stream)
    # so the function's only pipeline output is the path string.
    if (-not $Quiet) {
        $summary = Format-ProbeSummary -Result $Result
        Write-Host $summary
    }

    # Pipeline output: just the path
    $JsonlPath
}

<#
.SYNOPSIS
    Resolve one delegated Claude phase budget for orchestration/tool timeout setup.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateRange(0, 999)][int]$Phase,
    [ValidateRange(0, 3600)][int]$TimeoutSec = 0,
    [ValidateRange(0, 3600)][int]$IdleTimeoutSec = 0,
    [string]$PhaseTablePath
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir 'lib\dispatch-worker-lib.ps1')

if (-not $PhaseTablePath) {
    $PhaseTablePath = Join-Path (Split-Path -Parent $scriptDir) 'phases\phase-table.json'
}
$policy = Get-DispatchPhasePolicy -Phase $Phase -PhaseTablePath $PhaseTablePath `
    -TimeoutOverrideSec $TimeoutSec -IdleTimeoutOverrideSec $IdleTimeoutSec
$result = [ordered]@{
    phase = $Phase
    policy = $policy
    outer_timeout_sec = Get-DispatchOuterTimeoutSec -Policy $policy
    outer_timeout_ms = (Get-DispatchOuterTimeoutSec -Policy $policy) * 1000
}
$result | ConvertTo-Json -Depth 4

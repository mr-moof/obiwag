<#
.SYNOPSIS
    Run Obi headless tasks via Claude Code non-interactive mode.

.PARAMETER Task
    The task to run: drift, health, or sync.

.EXAMPLE
    .\tools\headless\run-headless.ps1 -Task health
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('drift', 'health', 'sync')]
    [string]$Task
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$taskMap = @{
    'drift'  = @{ File = 'drift-check.md';  Tools = 'Read,Glob,Grep,Bash'; Turns = 15 }
    'health' = @{ File = 'health-check.md'; Tools = 'Read,Glob,Bash';      Turns = 10 }
    'sync'   = @{ File = 'version-sync.md'; Tools = 'Read,Glob,Grep';      Turns = 8  }
}

$config = $taskMap[$Task]
$promptFile = Join-Path $ScriptDir $config.File

if (-not (Test-Path $promptFile)) {
    Write-Error "Prompt file not found: $promptFile"
    exit 1
}

$prompt = Get-Content $promptFile -Raw -Encoding UTF8

Write-Host "Running headless task: $Task" -ForegroundColor Cyan
Write-Host "  Prompt: $($config.File)" -ForegroundColor Gray
Write-Host "  Tools:  $($config.Tools)" -ForegroundColor Gray
Write-Host "  Turns:  $($config.Turns)" -ForegroundColor Gray
Write-Host ''

claude -p $prompt --allowedTools $config.Tools --max-turns $config.Turns --output-format json

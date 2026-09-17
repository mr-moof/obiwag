<#
.SYNOPSIS
    Deploys Obi Wag configuration to Claude Code and/or Codex.
.DESCRIPTION
    Thin dispatcher; deploy behavior lives in tools/lib/deploy-*.ps1.
.PARAMETER ClaudeOnly
    Deploy only Claude Code configuration.
.PARAMETER CodexOnly
    Deploy only Codex configuration.
.PARAMETER DryRun
    Report deployment actions without writing targets.
.PARAMETER SyncProjectPermissions
    Opt in to shared project permission synchronization.
.PARAMETER Force
    Back up and replace user-modified managed targets after collision detection.
.EXAMPLE
    .\tools\deploy.ps1 -ClaudeOnly -DryRun
#>

[CmdletBinding()]
param(
    [switch]$ClaudeOnly,
    [switch]$CodexOnly,
    [switch]$DryRun,
    [switch]$SyncProjectPermissions,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
# Put the running PowerShell's module directory first so PS 5.1 cannot load PS 7 modules.
$selfModules = Join-Path $PSHOME 'Modules'
if (Test-Path $selfModules) {
    $otherParts = @($env:PSModulePath -split ';' | Where-Object { $_ -and $_ -ne $selfModules })
    $env:PSModulePath = (@($selfModules) + $otherParts) -join ';'
}
# Shared script scope is the contract consumed by the dot-sourced deploy modules.
$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot  = Split-Path -Parent $ScriptDir
# Module order is significant.
. (Join-Path $ScriptDir 'lib\common.ps1')
. (Join-Path $ScriptDir 'lib\deploy-common.ps1')
. (Join-Path $ScriptDir 'lib\deploy-manifest.ps1')
. (Join-Path $ScriptDir 'lib\deploy-claude.ps1')
. (Join-Path $ScriptDir 'lib\deploy-codex.ps1')
# Fail-closed shared state.
$script:deployFailures = [System.Collections.Generic.List[object]]::new()
$script:manifest       = @{}
$script:obiCollisions  = @()
# Source roots.
$script:PhasesDir        = Join-Path $RepoRoot 'phases'
$script:OrchestrationDir = Join-Path $RepoRoot 'orchestration'
$script:PoliciesDir      = Join-Path $RepoRoot 'policies'
$script:DocsDir          = Join-Path $RepoRoot 'docs'
$script:SkillsDir        = Join-Path $RepoRoot 'skills'
$script:HooksDir         = Join-Path $RepoRoot 'hooks'
$script:UsersDir         = Join-Path $RepoRoot 'users'
$script:PlatformsDir     = Join-Path $RepoRoot 'platforms'
# Deployment roots.
$script:ClaudeTarget  = Join-Path $env:USERPROFILE '.claude'
$script:CodexTarget   = Join-Path $env:USERPROFILE '.codex'
# User-local executable deployment root.
$script:ToolsTarget = if ($env:OBI_HOME) { $env:OBI_HOME } else { Join-Path $env:USERPROFILE '.obi-tools' }
# Main execution.
Write-Header 'Obi Wag Deployment Script'
if (-not $CodexOnly) {
    $cleanupApproved = Invoke-ClaudeCleanup -DryRun:$DryRun -Force:$Force -DeployScriptPath $PSCommandPath
    if (-not $cleanupApproved) { exit 2 }
    Remove-RetiredObiTools -ToolsTarget $ToolsTarget -DryRun:$DryRun | Out-Null
    Remove-RetiredObiAgentSkills -UserProfileRoot $env:USERPROFILE -DryRun:$DryRun | Out-Null
    Invoke-ClaudeDeploy  -DryRun:$DryRun -SyncProjectPermissions:$SyncProjectPermissions
} else {
    Remove-RetiredObiTools -ToolsTarget $ToolsTarget -DryRun:$DryRun | Out-Null
}
if (-not $ClaudeOnly) {
    Invoke-CodexDeploy -DryRun:$DryRun
}
# Fail closed after both platform routes.
if ($script:deployFailures.Count -gt 0) {
    Write-Host ''
    Write-Host ('=' * 60) -ForegroundColor Red
    Write-Host "  DEPLOYMENT FAILED - $($script:deployFailures.Count) error(s)" -ForegroundColor Red
    Write-Host ('=' * 60) -ForegroundColor Red
    foreach ($f in $script:deployFailures) {
        Write-Host "  [$($f.Stage)] $($f.Detail)" -ForegroundColor Red
    }
    Write-Host ''
    Write-Host '  Deploy is idempotent: fix the underlying issue and re-run.' -ForegroundColor Yellow
    exit 1
}
# Summary.
Write-Header 'Deployment Summary'
if (-not $CodexOnly) {
    Write-Host '  Claude Code:' -ForegroundColor White
    Write-Host "    Commands: $ClaudeTarget\commands\" -ForegroundColor Gray
    Write-Host "    Agents:   $ClaudeTarget\agents\" -ForegroundColor Gray
    Write-Host "    Config:   $ClaudeTarget\CLAUDE.md" -ForegroundColor Gray
    Write-Host "    Hooks:    $ClaudeTarget\hooks\" -ForegroundColor Gray
    Write-Host "    Docs:     $ClaudeTarget\docs\" -ForegroundColor Gray
    Write-Host "    Skills:   $ClaudeTarget\skills\" -ForegroundColor Gray
    Write-Host "    Tools:    $ToolsTarget\tools\ (OBI_HOME)" -ForegroundColor Gray
    $userSettingsSource = Join-Path $UsersDir (Join-Path $env:USERNAME 'settings.json')
    if (Test-Path $userSettingsSource) {
        Write-Host "    Settings: $ClaudeTarget\settings.json (user: $env:USERNAME)" -ForegroundColor Gray
    }
    Write-Host ''
}
if (-not $ClaudeOnly) {
    Write-Host '  Codex:' -ForegroundColor White
    Write-Host "    Instructions: $RepoRoot\AGENTS.md" -ForegroundColor Gray
    Write-Host "    Hooks:        $RepoRoot\.codex\hooks.json" -ForegroundColor Gray
    Write-Host "    Skills:       $CodexTarget\skills\" -ForegroundColor Gray
    Write-Host "    Runtime:      $ToolsTarget\hooks\ (OBI_HOME)" -ForegroundColor Gray
    Write-Host ''
}
Write-Host '  Mode: Copy (production deployment)' -ForegroundColor Yellow
if ($DryRun) {
    Write-Host ''
    Write-Host '  *** DRY RUN - No changes were made ***' -ForegroundColor Magenta
}
Write-Host ''

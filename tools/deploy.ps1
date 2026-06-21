<#
.SYNOPSIS
    Deploys Obi Wag configuration to Claude Code and/or Codex.

.DESCRIPTION
    Slim dispatcher (OPT-12, #186). Collects commands, agents, policies, docs,
    skills, hooks, and user settings from the repo's lifecycle-centric structure
    and deploys them to the appropriate locations (~/.claude, ~/.codex). The
    actual deploy logic lives in dot-sourced lib modules:
      lib/deploy-common.ps1    -> Copy-SingleFile / Copy-DirectoryContents / etc.
      lib/deploy-manifest.ps1  -> Invoke-ClaudeCleanup (collision-aware cleanup)
      lib/deploy-claude.ps1    -> Invoke-ClaudeDeploy  (Step 2)
      lib/deploy-codex.ps1     -> Invoke-CodexDeploy   (Step 3.5)

    Source layout (repo):
      phases/0N-name/command.md      -> commands/<name>.md
      platforms/claude-code/agents/   -> agents/*.md
      orchestration/*.md             -> commands/*.md
      orchestration/utilities/*.md   -> commands/*.md
      policies/                      -> docs/policies/
      docs/                          -> docs/
      skills/                        -> skills/
      hooks/                         -> hooks/
      users/$USER/settings.json      -> settings.json
      config/permissions-allow.json  -> merged into project settings.local.json
      CLAUDE.md                      -> CLAUDE.md

.PARAMETER ClaudeOnly
    Only deploy Claude Code configuration.

.PARAMETER CodexOnly
    Only deploy Codex configuration.

.PARAMETER DryRun
    Show what would be deployed without actually deploying.

.EXAMPLE
    .\tools\deploy.ps1
    # Deploy to all platforms

.EXAMPLE
    .\tools\deploy.ps1 -ClaudeOnly -DryRun
    # Dry-run Claude Code deployment only
#>

[CmdletBinding()]
param(
    [switch]$ClaudeOnly,
    [switch]$CodexOnly,
    [switch]$DryRun,
    # Opt-in to syncing Obi's shared allow-list into every
    # ~/source/*/.claude/settings.local.json (#170). Default off — the
    # default deploy now reports what WOULD be synced and skips the
    # write. Pass -SyncProjectPermissions to retain pre-0.69.38 behavior.
    [switch]$SyncProjectPermissions,
    # Override collision-protection (#167). Default behavior: deploy aborts
    # if any manifest-tracked target file differs from its Obi source
    # (user has modified the Obi-deployed file). With -Force, the modified
    # file is copied to <target>.user-backup before being overwritten by
    # the redeployed Obi version. The first .user-backup is never
    # clobbered by subsequent -Force runs.
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# Paths - tools/ is one level down from repo root. These (and the source/target
# dirs below) are $script:-scoped because the dot-sourced lib-module functions
# read them from this caller scope; the explicit scope documents that contract
# and stops PSScriptAnalyzer flagging them as unused (it can't follow the
# cross-file parent-scope reads).
$script:ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:RepoRoot  = Split-Path -Parent $ScriptDir

# Dot-source lib modules (order matters: common -> deploy-common ->
# deploy-manifest -> deploy-claude/codex). Functions defined here run in this
# script's scope, so they see the $script: state and path variables below.
. (Join-Path $ScriptDir 'lib\common.ps1')
. (Join-Path $ScriptDir 'lib\deploy-common.ps1')
. (Join-Path $ScriptDir 'lib\deploy-manifest.ps1')
. (Join-Path $ScriptDir 'lib\deploy-claude.ps1')
. (Join-Path $ScriptDir 'lib\deploy-codex.ps1')

# Shared state, initialized BEFORE any module entry-point runs so the
# dot-sourced functions accumulate into the same objects (#136/#167):
#   - $script:deployFailures : fail-closed accumulator (exit 1 if non-empty)
#   - $script:manifest       : deployed-rel -> source-rel mappings
#   - $script:obiCollisions  : user-modified Obi files detected during cleanup
$script:deployFailures = [System.Collections.Generic.List[object]]::new()
$script:manifest       = @{}
$script:obiCollisions  = @()

# Source directories (new structure) — consumed by the dot-sourced lib modules
$script:PhasesDir        = Join-Path $RepoRoot 'phases'
$script:OrchestrationDir = Join-Path $RepoRoot 'orchestration'
$script:PoliciesDir      = Join-Path $RepoRoot 'policies'
$script:DocsDir          = Join-Path $RepoRoot 'docs'
$script:SkillsDir        = Join-Path $RepoRoot 'skills'
$script:HooksDir         = Join-Path $RepoRoot 'hooks'
$script:UsersDir         = Join-Path $RepoRoot 'users'
$script:PlatformsDir     = Join-Path $RepoRoot 'platforms'

# Target directories
$script:ClaudeTarget  = Join-Path $env:USERPROFILE '.claude'
$script:CodexTarget   = Join-Path $env:USERPROFILE '.codex'

# Tools target - separated from $ClaudeTarget so deployed PowerShell tools
# live under $OBI_HOME, a stable tools root set by deploy, rather than under
# the Claude config dir. Surface as $env:OBI_HOME so callers
# (orchestration/obi-auto.md rigor=max, CLAUDE.md, statusline) don't
# hardcode the path.
$script:ToolsTarget = if ($env:OBI_HOME) { $env:OBI_HOME } else { 'C:\src\obi-tools' }

# -- Main Execution ------------------------------------------------------------

Write-Header 'Obi Wag Deployment Script'

# Step 1 + 2: Claude Code (cleanup aborts with exit 2 on un-forced collisions)
if (-not $CodexOnly) {
    Invoke-ClaudeCleanup -DryRun:$DryRun -Force:$Force -DeployScriptPath $PSCommandPath
    Invoke-ClaudeDeploy  -DryRun:$DryRun -SyncProjectPermissions:$SyncProjectPermissions
}

# GitHub Copilot platform support was removed in v0.69.44 (OPT-13, #185).
# Existing installs may still have Obi content under ~/.github; print a
# one-time cleanup notice pointing at the deprecated uninstall path.
if (-not $CodexOnly) {
    $legacyCopilotTarget = Join-Path $env:USERPROFILE '.github'
    $legacyCopilotMarkers = @(
        (Join-Path $legacyCopilotTarget 'copilot-instructions.md')
        (Join-Path $legacyCopilotTarget 'agents\obi-wag.agent.md')
    )
    if (@($legacyCopilotMarkers | Where-Object { Test-Path -LiteralPath $_ }).Count -gt 0) {
        Write-Warning 'GitHub Copilot support was removed in v0.69.44 (OPT-13). Obi content remains under ~/.github.'
        Write-Warning 'Run tools/uninstall.ps1 -CopilotOnly to clean it up.'
    }
}

# Step 3.5: Codex
if (-not $ClaudeOnly) {
    Invoke-CodexDeploy -DryRun:$DryRun
}

# -- Fail-closed gate (issue #136) --------------------------------------------

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

# -- Summary -------------------------------------------------------------------

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

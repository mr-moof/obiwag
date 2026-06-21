<#
.SYNOPSIS
    Self-healing configuration validator for Obi Wag.

.DESCRIPTION
    Slim dispatcher (OPT-14, #187). Detects and repairs recurring failure modes:
    1. Missing hook files (deploy.ps1 deletes hooks/ before copying)
    2. Corrupted settings.local.json (heredoc pollution >200 chars)
    3. Circular dependency deadlocks (Python import cycles)

    The actual check/repair logic lives in dot-sourced lib modules:
      lib/common.ps1                     -> Output helpers
      lib/validation.ps1                 -> Core structural checks (CG-1..CG-6)
      lib/checks/check-quick.ps1         -> Quick env/CLI checks (VS-1..VS-11)
      lib/checks/check-auto-max.ps1      -> auto-max.yaml validation (AM-2..AM-5)
      lib/checks/check-dispatch-docs.ps1 -> phase-table + README sync (DD-1..DD-2)
      lib/checks/guardian-diagnostics.ps1-> Circular deps + backup health (CG-4, CG-7)
      lib/checks/guardian-repair.ps1     -> Repair + snapshot + safe-mode (CG-8..CG-14)

    Run before sessions or after deployment. Creates config snapshots
    and optionally files GitHub issues on recovery.

    Structural vs runtime boundary: guardian checks structural validity
    (parseable JSON, no pollution, hooks present, correct paths). Runtime
    state (are permissions sufficient, do hooks actually execute) is
    checked by healthcheck.py.

.PARAMETER CheckOnly
    Validate without repairing. Returns exit code 0 (healthy) or 1 (issues found).

.PARAMETER Quick
    Run core validation + quick environment checks only. Skips circular deps,
    backup health, auto-max config, dispatch-docs, and all repairs.
    This is what verify-setup.ps1 forwards to.

.PARAMETER NoSnapshot
    Skip snapshot creation after successful run.

.PARAMETER NoIssue
    Skip GitHub issue creation on recovery.

.PARAMETER RepoRoot
    Override auto-detected repo root. Used when running from a staged location
    (e.g., C:\src) where the script's parent directory is not the repo.

.EXAMPLE
    .\tools\config-guardian.ps1
    # Full validation + auto-repair

.EXAMPLE
    .\tools\config-guardian.ps1 -CheckOnly
    # Validate only (CI-friendly)

.EXAMPLE
    .\tools\config-guardian.ps1 -Quick -CheckOnly
    # Quick structural checks (replaces verify-setup.ps1)

.EXAMPLE
    .\tools\config-guardian.ps1 -CheckOnly -Verbose
    # Detailed diagnostic output
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$Quick,
    [switch]$NoSnapshot,
    [switch]$NoIssue,
    [string]$RepoRoot
)

$ErrorActionPreference = 'Stop'

# -- Paths ---------------------------------------------------------------------

$script:ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    $script:RepoRoot = Split-Path -Parent $ScriptDir
} else {
    $script:RepoRoot = $RepoRoot
}

# Dot-source lib modules (order matters: common -> validation -> checks modules).
# Functions defined here run in this script's scope, so they see the $script:
# state and path variables below.
. (Join-Path $ScriptDir 'lib\common.ps1')
. (Join-Path $ScriptDir 'lib\validation.ps1')
. (Join-Path $ScriptDir 'lib\checks\check-quick.ps1')
. (Join-Path $ScriptDir 'lib\checks\check-auto-max.ps1')
. (Join-Path $ScriptDir 'lib\checks\check-dispatch-docs.ps1')
. (Join-Path $ScriptDir 'lib\checks\guardian-diagnostics.ps1')
. (Join-Path $ScriptDir 'lib\checks\guardian-repair.ps1')

# Shared state, initialized BEFORE any module entry-point runs so the
# dot-sourced functions accumulate into the same objects:
$script:ClaudeDir     = Join-Path $env:USERPROFILE '.claude'
$script:HooksDir      = Join-Path $ClaudeDir 'hooks'
$script:CoreDir       = Join-Path $HooksDir 'core'
$script:SettingsJson  = Join-Path $ClaudeDir 'settings.json'
$script:SnapshotDir   = Join-Path (Join-Path $ClaudeDir '.obi') 'config-snapshots'
$script:SourceHooksDir = Join-Path $RepoRoot 'hooks'
$script:PythonExe     = if (Get-Command python -ErrorAction SilentlyContinue) { (Get-Command python).Source } else { 'C:\Python314\python.exe' }

# Load hook manifest (single source of truth for filenames)
$script:ManifestPath = Join-Path $ScriptDir 'lib\hook-manifest.json'
$script:Manifest     = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$script:HookFiles    = @($Manifest.hooks | ForEach-Object { $_.file })
$script:CoreModules  = @($Manifest.core_modules)

# -- Main Flow -----------------------------------------------------------------

Write-Header 'Obi Wag Config Guardian'

$anyFailure = $false

# Phase 1: Core structural checks (always run)
Write-Host ''
Write-Host '  Phase 1: Diagnostics' -ForegroundColor White
Write-Host '  --------------------' -ForegroundColor Gray

$settingsResult    = Test-SettingsJson
$heredocResult     = Test-HeredocPollution
$hookPathResult    = Test-HookPaths
$sharedPermsResult = Test-SharedPermissions
$depsResult        = Test-Dependencies

$anyFailure = (-not $settingsResult.Valid) -or
              (-not $heredocResult.Clean) -or
              (-not $hookPathResult.Valid) -or
              (-not $sharedPermsResult.Clean) -or
              (-not $depsResult.Valid)

# Quick-mode environment checks (VS-1..VS-11)
if ($Quick) {
    $quickResult = Test-QuickSetup
    if (-not $quickResult.Valid) { $anyFailure = $true }

    # Quick mode: skip extended checks + repairs
    if (-not $anyFailure) {
        Write-Host ''
        Write-Host '  All checks passed.' -ForegroundColor Green
        Write-Host ''
        exit 0
    } else {
        Write-Host ''
        Write-Host '  Issues detected.' -ForegroundColor Red
        Write-Host ''
        exit 1
    }
}

# Extended checks (full mode only)
$circularResult = Test-CircularDeps
$backupResult   = Test-BackupHealth

$dispatchDocsResult = Test-DispatchDocs -CheckOnly:$CheckOnly
$autoMaxResult      = Test-AutoMaxConfig -CheckOnly:$CheckOnly

if (-not $circularResult.Clean)   { $anyFailure = $true }
if (-not $backupResult.Valid)     { $anyFailure = $true }
if (-not $dispatchDocsResult.Valid) { $anyFailure = $true }
if (-not $autoMaxResult.Valid)    { $anyFailure = $true }

# Phase 2: Decision
if (-not $anyFailure) {
    Write-Host ''
    Write-Host '  All checks passed.' -ForegroundColor Green

    if (-not $NoSnapshot) {
        $null = Save-ConfigSnapshot
    }

    Write-Host ''
    exit 0
}

# Failures detected
Write-Host ''
Write-Host '  Issues detected.' -ForegroundColor Red

if ($CheckOnly) {
    Write-Host '  Running in check-only mode -no repairs attempted.' -ForegroundColor Yellow
    Write-Host ''
    exit 1
}

# Phase 3: Repair
Write-Host ''
Write-Host '  Phase 2: Repair' -ForegroundColor White
Write-Host '  ----------------' -ForegroundColor Gray

$allRepairs = @()
$unresolvedCycles = @()
$snapshotUsed = $null

# Enter safe mode (disable hooks during repair)
$originalHooks = Enter-SafeMode

try {
    # Repair heredoc pollution
    if (-not $heredocResult.Clean) {
        $repaired = Repair-HeredocPollution -PollutedFiles $heredocResult.PollutedFiles
        $allRepairs += $repaired
    }

    # Repair missing hooks
    if (-not $hookPathResult.Valid) {
        $repaired = Repair-MissingHooks -MissingFiles $hookPathResult.MissingFiles -MissingCoreModules $hookPathResult.MissingCoreModules
        $allRepairs += $repaired
    }

    # Report circular deps (cannot auto-fix)
    if (-not $circularResult.Clean) {
        $cycleDetails = Repair-CircularDeps -Cycles $circularResult.Cycles
        $unresolvedCycles += $cycleDetails
    }
} finally {
    # Always restore hooks
    Exit-SafeMode -OriginalHooks $originalHooks
}

# Phase 4: Verify repairs
Write-Host ''
Write-Host '  Phase 3: Verification' -ForegroundColor White
Write-Host '  ---------------------' -ForegroundColor Gray

$postSettings    = Test-SettingsJson
$postHeredoc     = Test-HeredocPollution
$postHooks       = Test-HookPaths
Test-CircularDeps      | Out-Null
Test-SharedPermissions | Out-Null

$stillBroken = (-not $postSettings.Valid) -or
               (-not $postHeredoc.Clean) -or
               (-not $postHooks.Valid)
               # Circular deps and shared perms drift excluded -they require manual fix (deploy.ps1 handles sync)

if (-not $stillBroken) {
    Write-Host ''
    Write-Check 'All repairable issues resolved.'

    if (-not $NoSnapshot) {
        $snapshotFile = Save-ConfigSnapshot
        $snapshotUsed = Split-Path -Leaf $snapshotFile
    }

    if (-not $NoIssue -and $allRepairs.Count -gt 0) {
        New-RecoveryIssue -Repairs $allRepairs -UnresolvedCycles $unresolvedCycles -SnapshotUsed $snapshotUsed
    }

    Write-Host ''
    exit 0
} else {
    Write-Host ''
    Write-Problem 'Some issues could not be repaired automatically.'
    Write-Host '  Manual intervention required. Check output above for details.' -ForegroundColor Yellow

    if (-not $NoIssue -and $allRepairs.Count -gt 0) {
        New-RecoveryIssue -Repairs $allRepairs -UnresolvedCycles $unresolvedCycles -SnapshotUsed $snapshotUsed
    }

    Write-Host ''
    exit 1
}

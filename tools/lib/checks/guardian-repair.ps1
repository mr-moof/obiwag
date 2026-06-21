<#
.SYNOPSIS
    Repair, snapshot, safe-mode, and GitHub issue functions for Config Guardian.

.DESCRIPTION
    Dot-sourced by config-guardian.ps1 AFTER lib/common.ps1 and lib/validation.ps1.
    Assumes the caller has defined these script-scope variables:
        $HooksDir, $CoreDir, $SettingsJson, $SnapshotDir, $SourceHooksDir,
        $HookFiles, $CoreModules, $PythonExe, $ScriptDir, $RepoRoot

    Provides:
    - Repair-HeredocPollution   (CG-8)
    - Repair-MissingHooks       (CG-9)
    - Repair-CircularDeps       (CG-10)
    - Save-ConfigSnapshot       (CG-11) + rotation
    - Restore-FromSnapshot      (CG-12)
    - New-RecoveryIssue         (CG-13)  GitHub issue via gh CLI
    - Enter-SafeMode            (CG-14)
    - Exit-SafeMode             (CG-14)
#>

function Repair-HeredocPollution {
    <#
    .SYNOPSIS
        Remove entries >200 chars from settings.local.json permissions.allow arrays.
    #>
    param([array]$PollutedFiles)

    $repaired = @()

    foreach ($entry in $PollutedFiles) {
        $filePath = $entry.Path
        Write-Repair "Cleaning heredoc pollution: $filePath"

        try {
            $raw = Get-Content $filePath -Raw
            $json = $raw | ConvertFrom-Json

            if ($json.permissions -and $json.permissions.allow) {
                $original = @($json.permissions.allow)
                $cleaned = @($original | Where-Object { $_.Length -le 200 })
                $removed = $original.Count - $cleaned.Count

                $json.permissions.allow = $cleaned
                $json | ConvertTo-Json -Depth 10 | Set-Content $filePath -Encoding UTF8
                Write-Repair "Removed $removed polluted entries from $filePath"
                $repaired += $filePath
            }
        } catch {
            # JSON is completely broken -delete the file
            Write-Repair "JSON unparseable, deleting: $filePath"
            Remove-Item $filePath -Force
            $repaired += "$filePath (deleted -unparseable)"
        }
    }

    return $repaired
}

function Repair-MissingHooks {
    <#
    .SYNOPSIS
        Restore missing hook files from the source repo.
    #>
    param(
        [array]$MissingFiles,
        [array]$MissingCoreModules
    )

    $repaired = @()

    # Ensure hooks/ directory exists
    if (-not (Test-Path $HooksDir)) {
        New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null
        Write-Repair "Created hooks directory: $HooksDir"
    }

    # Ensure hooks/core/ directory exists
    if ($MissingCoreModules.Count -gt 0 -and -not (Test-Path $CoreDir)) {
        New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null
        Write-Repair "Created core directory: $CoreDir"
    }

    # Restore missing hook files from source repo
    foreach ($hookFile in $MissingFiles) {
        # $hookFile might be a full path (from settings.json validation) or just a filename
        $fileName = Split-Path -Leaf $hookFile
        $sourcePath = Join-Path $SourceHooksDir $fileName
        $targetPath = Join-Path $HooksDir $fileName

        if (Test-Path $sourcePath) {
            Copy-Item -Path $sourcePath -Destination $targetPath -Force
            Write-Repair "Restored from source repo: $fileName"
            $repaired += $fileName
        } else {
            # Fallback: create minimal hook_wrapper.cmd
            if ($fileName -eq 'hook_wrapper.cmd') {
                $minimalWrapper = @'
@echo off
rem Minimal hook_wrapper.cmd -restored by Config Guardian
set "HOOK_NAME=%~1"
set "SCRIPT=%~dp0%HOOK_NAME%.py"
if not exist "%SCRIPT%" (
    echo {}
    exit /b 0
)
C:/Python314/python.exe "%SCRIPT%"
if errorlevel 1 (
    echo {}
    exit /b 0
)
exit /b 0
'@
                Set-Content -Path $targetPath -Value $minimalWrapper -Encoding ASCII
                Write-Repair "Created minimal hook_wrapper.cmd (source repo copy not found)"
                $repaired += "$fileName (minimal fallback)"
            } else {
                Write-Problem "Cannot restore $fileName -not found in source repo at $sourcePath"
            }
        }
    }

    # Restore missing core modules
    foreach ($module in $MissingCoreModules) {
        $sourcePath = Join-Path (Join-Path $SourceHooksDir 'core') $module
        $targetPath = Join-Path $CoreDir $module

        if (Test-Path $sourcePath) {
            Copy-Item -Path $sourcePath -Destination $targetPath -Force
            Write-Repair "Restored core module from source: $module"
            $repaired += "core/$module"
        } else {
            Write-Problem "Cannot restore core/$module -not in source repo"
        }
    }

    return $repaired
}

function Repair-CircularDeps {
    <#
    .SYNOPSIS
        Report circular dependencies. Cannot safely auto-fix Python import cycles.
    #>
    param([array]$Cycles)

    $details = @()
    foreach ($cycle in $Cycles) {
        $cycleStr = $cycle -join ' -> '
        Write-Repair "Circular dep detected (manual fix required): $cycleStr"
        $details += $cycleStr
    }

    return $details
}

# -- Snapshot Functions --------------------------------------------------------

function Save-ConfigSnapshot {
    <#
    .SYNOPSIS
        Save current config state for future restore reference.
    #>

    if (-not (Test-Path $SnapshotDir)) {
        New-Item -ItemType Directory -Path $SnapshotDir -Force | Out-Null
    }

    $timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $snapshotFile = Join-Path $SnapshotDir "snapshot-$timestamp.json"

    # Compute hashes
    function Get-FileHashSafe {
        param([string]$Path)
        if (Test-Path $Path) {
            return (Get-FileHash -Path $Path -Algorithm MD5).Hash.Substring(0, 12)
        }
        return $null
    }

    # Settings.json
    $settingsHash = Get-FileHashSafe $SettingsJson
    $settingsValid = $false
    if (Test-Path $SettingsJson) {
        try {
            $null = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $settingsValid = $true
        } catch { }
    }

    # Settings.local.json files
    $localFiles = @()
    $found = Get-LocalSettingsFiles
    foreach ($f in $found) {
        $valid = $false
        try {
            $null = Get-Content $f.FullName -Raw | ConvertFrom-Json
            $valid = $true
        } catch { }
        $localFiles += @{
            path  = $f.FullName
            hash  = Get-FileHashSafe $f.FullName
            valid = $valid
        }
    }

    # Hook files
    $hookEntries = @()
    foreach ($hf in $HookFiles) {
        $path = Join-Path $HooksDir $hf
        $hookEntries += @{
            name   = $hf
            hash   = Get-FileHashSafe $path
            exists = (Test-Path $path)
        }
    }

    # Core modules
    $coreEntries = @()
    foreach ($mod in $CoreModules) {
        $path = Join-Path $CoreDir $mod
        $coreEntries += @{
            name   = $mod
            hash   = Get-FileHashSafe $path
            exists = (Test-Path $path)
        }
    }

    # Python version
    $pyVersion = 'unknown'
    if (Test-Path $PythonExe) {
        try {
            $pyVersion = (& $PythonExe --version 2>&1) -replace 'Python ', ''
        } catch { }
    }

    # Obi version
    $obiVersion = 'unknown'
    $versionFile = Join-Path $ScriptDir 'version.yaml'
    if (Test-Path $versionFile) {
        $vContent = Get-Content $versionFile -Raw
        if ($vContent -match 'version:\s*"?([0-9.]+)"?') {
            $obiVersion = $Matches[1]
        }
    }

    $snapshot = @{
        timestamp           = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
        settings_json_hash  = $settingsHash
        settings_json_valid = $settingsValid
        settings_local_files = $localFiles
        hook_files          = $hookEntries
        core_modules        = $coreEntries
        python_version      = $pyVersion
        obi_version         = $obiVersion
    }

    $snapshot | ConvertTo-Json -Depth 5 | Set-Content $snapshotFile -Encoding UTF8
    Write-Check "Snapshot saved: $snapshotFile"

    # Rotation: keep last 5
    $snapshots = Get-ChildItem -Path $SnapshotDir -Filter 'snapshot-*.json' | Sort-Object Name -Descending
    if ($snapshots.Count -gt 5) {
        $snapshots | Select-Object -Skip 5 | ForEach-Object {
            Remove-Item $_.FullName -Force
            Write-Detail "Rotated old snapshot: $($_.Name)"
        }
    }

    return $snapshotFile
}

function Restore-FromSnapshot {
    <#
    .SYNOPSIS
        Find the newest valid snapshot and use it to identify what needs restoring.
        Actual files are copied from the source repo, not the snapshot.
    #>

    if (-not (Test-Path $SnapshotDir)) {
        Write-Detail "No snapshot directory found"
        return $null
    }

    $snapshots = Get-ChildItem -Path $SnapshotDir -Filter 'snapshot-*.json' | Sort-Object Name -Descending
    foreach ($snapFile in $snapshots) {
        try {
            $snap = Get-Content $snapFile.FullName -Raw | ConvertFrom-Json
            if ($snap.settings_json_valid) {
                Write-Detail "Using snapshot: $($snapFile.Name)"
                return $snap
            }
        } catch {
            continue
        }
    }

    Write-Detail "No valid snapshot found"
    return $null
}

# -- GitHub Issue --------------------------------------------------------------

function New-RecoveryIssue {
    <#
    .SYNOPSIS
        Create a GitHub issue documenting the auto-recovery.
    #>
    param(
        [array]$Repairs,
        [array]$UnresolvedCycles,
        [string]$SnapshotUsed
    )

    $ghCmd = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $ghCmd) {
        Write-Detail "gh not available -skipping issue creation"
        return
    }

    $date = Get-Date -Format 'yyyy-MM-dd HH:mm'
    $title = "fix: Config Guardian auto-recovery ($date)"

    $bodyLines = @(
        '## Auto-Recovery Report'
        ''
        "**Date:** $date"
        "**Snapshot used:** $(if ($SnapshotUsed) { $SnapshotUsed } else { 'none' })"
        ''
        '### Repairs Performed'
    )

    foreach ($repair in $Repairs) {
        $bodyLines += "- $repair"
    }

    if ($UnresolvedCycles.Count -gt 0) {
        $bodyLines += ''
        $bodyLines += '### Unresolved (manual fix required)'
        foreach ($cycle in $UnresolvedCycles) {
            $bodyLines += "- Circular import: $cycle"
        }
    }

    $bodyLines += ''
    $bodyLines += '### Follow-up'
    $bodyLines += '- [ ] Verify session starts correctly'
    $bodyLines += '- [ ] Run `config-guardian.ps1 -CheckOnly` to confirm clean state'

    $body = $bodyLines -join "`n"

    try {
        $issueOutput = gh issue create -R user/obiwag-agents --title $title --body $body 2>&1
        Write-Check "GitHub issue created: $issueOutput"
    } catch {
        Write-Problem "Failed to create GitHub issue: $_"
    }
}

# -- Safe Mode -----------------------------------------------------------------

function Enter-SafeMode {
    <#
    .SYNOPSIS
        Temporarily disable hooks in settings.json during repair.
        Returns the original hooks object for restoration.
    #>

    if (-not (Test-Path $SettingsJson)) {
        return $null
    }

    try {
        $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
        $originalHooks = $settings.hooks

        if ($originalHooks) {
            $settings.hooks = @{}
            $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8
            Write-Repair "Safe mode: hooks temporarily disabled"
        }

        return $originalHooks
    } catch {
        Write-Problem "Could not enter safe mode: $_"
        return $null
    }
}

function Exit-SafeMode {
    <#
    .SYNOPSIS
        Restore the original hooks configuration after repair.
    #>
    param($OriginalHooks)

    if ($null -eq $OriginalHooks) { return }

    try {
        $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
        $settings.hooks = $OriginalHooks
        $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8
        Write-Repair "Safe mode: hooks restored"
    } catch {
        Write-Problem "Failed to restore hooks -settings.json may need manual repair: $_"
    }
}

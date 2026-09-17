<#
.SYNOPSIS
    Shared validation primitives for Obi Wag tools. Currently consumed by config-guardian.ps1.

.DESCRIPTION
    Dot-source this file from tools/ scripts AFTER common.ps1:
        . (Join-Path $ScriptDir 'lib\common.ps1')
        . (Join-Path $ScriptDir 'lib\validation.ps1')

    Reserved for future use by deploy.ps1 and any other PS tool that needs structural
    validation. The caller must define the script-scope variables listed below.

    Provides:
    - Get-LocalSettingsFiles  -- find all settings.local.json under ~/source/
    - Test-SettingsJson       -- validate JSON syntax of settings files
    - Test-HeredocPollution   -- detect >200-char permission entries
    - Test-HookPaths          -- verify hook files and core modules on disk
    - Test-SharedPermissions  -- detect permission drift in managed files
    - Test-Dependencies       -- check runtime requirements (Python, gh, etc.)

    Assumes the caller has dot-sourced common.ps1 (for Write-Check, Write-Problem,
    Write-Detail, Write-Repair, Test-HookCommandPaths) and has defined these
    script-scope variables:
        $SettingsJson, $HooksDir, $CoreDir, $HookFiles, $CoreModules,
        $RepoRoot, $PythonExe
#>

# -- Helpers -------------------------------------------------------------------

function Get-LocalSettingsFiles {
    <#
    .SYNOPSIS
        Find all settings.local.json files under ~/source/*/.claude/.
        Shared by Test-SettingsJson, Test-HeredocPollution, and Test-SharedPermissions.
    #>
    $sourceDir = Join-Path $env:USERPROFILE 'source'
    if (-not (Test-Path $sourceDir)) { return @() }

    @(Get-ChildItem -Path $sourceDir -Filter 'settings.local.json' -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*\.claude\*' })
}

# -- Diagnostic Functions ------------------------------------------------------

function Test-SettingsJson {
    <#
    .SYNOPSIS
        Validate JSON syntax of settings.json and all settings.local.json files.
    #>
    $results = @{ Valid = $true; Errors = @() }

    # Check ~/.claude/settings.json
    if (Test-Path $SettingsJson) {
        try {
            $null = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            Write-Check "settings.json: valid JSON"
        } catch {
            $results.Valid = $false
            $results.Errors += "settings.json: $($_.Exception.Message)"
            Write-Problem "settings.json: invalid JSON"
        }
    } else {
        $results.Valid = $false
        $results.Errors += "settings.json: file not found"
        Write-Problem "settings.json: not found at $SettingsJson"
    }

    # Check all settings.local.json under ~/source/
    $localFiles = Get-LocalSettingsFiles
    foreach ($file in $localFiles) {
        try {
            $null = Get-Content $file.FullName -Raw | ConvertFrom-Json
            Write-Check "settings.local.json: valid ($($file.FullName))"
        } catch {
            $results.Valid = $false
            $results.Errors += "$($file.FullName): $($_.Exception.Message)"
            Write-Problem "settings.local.json: invalid ($($file.FullName))"
        }
    }

    return $results
}

function Test-HeredocPollution {
    <#
    .SYNOPSIS
        Detect settings.local.json entries >200 chars (heredoc auto-approve symptom).
    #>
    $results = @{ Clean = $true; PollutedFiles = @() }

    $localFiles = Get-LocalSettingsFiles

    foreach ($file in $localFiles) {
        try {
            $json = Get-Content $file.FullName -Raw | ConvertFrom-Json
            $allowEntries = @()
            if ($json.permissions -and $json.permissions.allow) {
                $allowEntries = @($json.permissions.allow)
            }

            $polluted = @($allowEntries | Where-Object { $_.Length -gt 200 })
            if ($polluted.Count -gt 0) {
                $results.Clean = $false
                $results.PollutedFiles += @{
                    Path    = $file.FullName
                    Count   = $polluted.Count
                    Entries = $polluted
                }
                Write-Problem "Heredoc pollution: $($file.FullName) ($($polluted.Count) entries >200 chars)"
                foreach ($entry in $polluted) {
                    Write-Detail "  $($entry.Substring(0, [Math]::Min(80, $entry.Length)))..."
                }
            } else {
                Write-Check "No heredoc pollution: $($file.FullName)"
            }
        } catch {
            # Unparseable JSON is handled by Test-SettingsJson; skip here
            Write-Detail "Skipped (unparseable): $($file.FullName)"
        }
    }

    return $results
}

function Test-HookPaths {
    <#
    .SYNOPSIS
        Verify all hook files referenced in settings.json exist on disk.
    #>
    $results = @{ Valid = $true; MissingFiles = @(); MissingCoreModules = @() }

    # Check hook files
    foreach ($hookFile in $HookFiles) {
        $path = Join-Path $HooksDir $hookFile
        if (Test-Path $path) {
            Write-Check "Hook file: $hookFile"
        } else {
            $results.Valid = $false
            $results.MissingFiles += $hookFile
            Write-Problem "Hook file missing: $hookFile"
        }
    }

    # Check core modules
    foreach ($module in $CoreModules) {
        $path = Join-Path $CoreDir $module
        if (Test-Path $path) {
            Write-Check "Core module: $module"
        } else {
            $results.Valid = $false
            $results.MissingCoreModules += $module
            Write-Problem "Core module missing: $module"
        }
    }

    # Validate hook command paths in settings.json
    $cmdValidation = Test-HookCommandPaths -SettingsPath $SettingsJson
    if (-not $cmdValidation.Valid) {
        $results.Valid = $false
        foreach ($missingPath in $cmdValidation.MissingPaths) {
            $results.MissingFiles += $missingPath
            Write-Problem "Hook command path missing: $missingPath"
        }
    } else {
        Write-Check "All hook command paths valid"
    }

    return $results
}

function Test-SharedPermissions {
    <#
    .SYNOPSIS
        Detect drift: project settings.local.json missing shared permission patterns.
    #>
    $results = @{ Clean = $true; Drift = @() }

    $sharedPermsFile = Join-Path $RepoRoot (Join-Path 'config' 'permissions-allow.json')
    if (-not (Test-Path $sharedPermsFile)) {
        Write-Detail "No shared permissions file found at $sharedPermsFile"
        return $results
    }

    try {
        $sharedData = Get-Content $sharedPermsFile -Raw | ConvertFrom-Json
        $sharedAllows = @($sharedData.permissions.allow)
    } catch {
        $results.Clean = $false
        $results.Drift += "Cannot parse shared permissions file: $sharedPermsFile"
        Write-Problem "Shared permissions file is invalid JSON: $sharedPermsFile"
        return $results
    }

    $localFiles = Get-LocalSettingsFiles

    foreach ($file in $localFiles) {
        try {
            $json = Get-Content $file.FullName -Raw | ConvertFrom-Json
            # Only check obi-deploy managed files
            if ($json._managed_by -ne 'obi-deploy') {
                Write-Detail "Skipped (not managed): $($file.FullName)"
                continue
            }

            $localAllows = @()
            if ($json.permissions -and $json.permissions.allow) {
                $localAllows = @($json.permissions.allow)
            }

            $missing = @($sharedAllows | Where-Object { $_ -notin $localAllows })
            if ($missing.Count -gt 0) {
                $results.Clean = $false
                $results.Drift += @{
                    Path    = $file.FullName
                    Missing = $missing
                }
                Write-Problem "Permission drift: $($file.FullName) missing $($missing.Count) shared pattern(s)"
                foreach ($m in $missing) {
                    Write-Detail "  Missing: $m"
                }
            } else {
                Write-Check "Shared permissions present: $($file.FullName)"
            }
        } catch {
            Write-Detail "Skipped (unparseable): $($file.FullName)"
        }
    }

    return $results
}

function Test-PermissionRules {
    <#
    .SYNOPSIS
        Reject path-qualified Write(...) permission rules (issue #200 SS5).
    .DESCRIPTION
        A `permissions.allow` entry of the form `Write(<path>)` makes Claude Code emit a
        permission-rule warning at startup. `Edit(<path>)` is the correct rule type -- it covers
        the file-editing tools -- so this is a semantic-preserving rule-type correction. This check
        flags any `Write(...)` allow entry (path-qualified) and accepts the corrected `Edit(...)`
        forms. Bare `Write` (tool-level, no parentheses) is fine and NOT flagged.

        Defaults to the deployed $SettingsJson. Deployment validation passes the source file(s) via
        -SettingsPaths so a bad rule is caught BEFORE it is deployed, not only after.
    #>
    param([string[]]$SettingsPaths = @($SettingsJson))

    $results = @{ Valid = $true; Violations = @() }

    foreach ($path in $SettingsPaths) {
        if (-not $path -or -not (Test-Path $path)) { continue }
        try {
            $json = Get-Content $path -Raw | ConvertFrom-Json
        } catch {
            # JSON syntax errors are reported by Test-SettingsJson; skip here.
            Write-Detail "Skipped permission-rule check (unparseable): $path"
            continue
        }

        $allow = @()
        if ($json.permissions -and $json.permissions.allow) { $allow = @($json.permissions.allow) }

        # Path-qualified Write(...) only. Bare `Write` has no parentheses and is allowed.
        $bad = @($allow | Where-Object { $_ -is [string] -and $_ -match '^Write\(.+\)$' })
        if ($bad.Count -gt 0) {
            $results.Valid = $false
            foreach ($rule in $bad) {
                $results.Violations += @{ Path = $path; Rule = $rule }
                Write-Problem "Path-qualified Write() permission rule: '$rule' ($path)"
            }
            Write-Detail "  Use Edit(...) instead -- Edit covers file-editing tools and clears the startup warning."
        } else {
            Write-Check "Permission rules: no path-qualified Write() entries ($path)"
        }
    }

    return $results
}

function Test-Dependencies {
    <#
    .SYNOPSIS
        Check runtime requirements: Python, hook_wrapper.cmd, gh.
    #>
    $results = @{ Valid = $true; Missing = @() }

    # Python
    if (Test-Path $PythonExe) {
        try {
            $version = & $PythonExe --version 2>&1
            Write-Check "Python: $version"
        } catch {
            $results.Valid = $false
            $results.Missing += 'Python (exists but failed to run)'
            Write-Problem "Python exists but failed to run: $_"
        }
    } else {
        $results.Valid = $false
        $results.Missing += "Python ($PythonExe)"
        Write-Problem "Python not found: $PythonExe"
    }

    # hook_wrapper.cmd
    $wrapperPath = Join-Path $HooksDir 'hook_wrapper.cmd'
    if (Test-Path $wrapperPath) {
        Write-Check "hook_wrapper.cmd: present"
    } else {
        $results.Valid = $false
        $results.Missing += 'hook_wrapper.cmd'
        Write-Problem "hook_wrapper.cmd: not found at $wrapperPath"
    }

    # gh CLI
    $ghPath = Get-Command gh -ErrorAction SilentlyContinue
    if ($ghPath) {
        Write-Check "gh CLI: available ($($ghPath.Source))"
    } else {
        $results.Missing += 'gh CLI (optional, needed for issue creation)'
        Write-Detail "gh CLI not found -GitHub issue creation will be skipped"
    }

    return $results
}

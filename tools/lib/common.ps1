<#
.SYNOPSIS
    Shared helper functions for Obi Wag tools (deploy.ps1, config-guardian.ps1).

.DESCRIPTION
    Dot-source this file from tools/ scripts:
        . (Join-Path $ScriptDir 'lib\common.ps1')

    Provides:
    - Output formatting helpers (Write-Header, Write-Step, Write-Check, etc.)
    - Hook-path validation (Test-HookCommandPaths)
#>

# -- Output Helpers ------------------------------------------------------------

function Write-Header {
    param([string]$Title)
    Write-Host ''
    Write-Host ('=' * 60) -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host ('=' * 60) -ForegroundColor Cyan
    Write-Host ''
}

function Write-Step {
    param([string]$Message)
    Write-Host "  -> $Message" -ForegroundColor Yellow
}

function Write-Check {
    param([string]$Message)
    Write-Host "  [OK]   $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "    $Message" -ForegroundColor Gray
}

function Write-Problem {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
}

function Write-Detail {
    param([string]$Message)
    if ($VerbosePreference -eq 'Continue') {
        Write-Host "         $Message" -ForegroundColor Gray
    }
}

function Write-Repair {
    param([string]$Message)
    Write-Host "  [FIX]  $Message" -ForegroundColor Yellow
}

# -- Hook-Path Validation -----------------------------------------------------

function Test-HookCommandPaths {
    <#
    .SYNOPSIS
        Validate that script paths referenced in settings.json hook commands or exec-form
        args exist on disk.

    .PARAMETER SettingsPath
        Path to the settings.json file to validate.

    .PARAMETER FallbackDir
        Optional directory to check as a fallback location for missing scripts.

    .OUTPUTS
        Hashtable with keys: Valid ([bool]), MissingPaths ([string[]])
    #>
    param(
        [Parameter(Mandatory)][string]$SettingsPath,
        [string]$FallbackDir
    )

    $results = @{ Valid = $true; MissingPaths = @() }

    if (-not (Test-Path $SettingsPath)) {
        return $results
    }

    try {
        $settings = Get-Content $SettingsPath -Raw | ConvertFrom-Json
        if (-not $settings.hooks) { return $results }

        # Enumerate the configured event properties instead of maintaining a partial list.
        # Claude adds hook events over time; omitting one makes a missing script invisible to
        # both deploy.ps1 and config-guardian.ps1 (PostToolUseFailure and SubagentStop were
        # previously missed here).
        foreach ($hookProperty in $settings.hooks.PSObject.Properties) {
            $hookList = $hookProperty.Value
            if (-not $hookList) { continue }

            foreach ($hookGroup in $hookList) {
                foreach ($hook in $hookGroup.hooks) {
                    $cmd = $hook.command
                    if (-not $cmd) { continue }

                    # Shell-form hooks embed the script in `command`; exec-form hooks use a
                    # bare executable plus an `args` array. Inspect both so switching the hot
                    # path away from a shell wrapper does not weaken deployment validation.
                    $candidates = @()
                    $expandedCommand = $cmd -replace '%USERPROFILE%', $env:USERPROFILE
                    $expandedCommand = $expandedCommand -replace '\$HOME', $env:USERPROFILE

                    foreach ($match in [regex]::Matches($expandedCommand, '["'']([^"'']+\.(cmd|ps1|py))["'']')) {
                        $candidates += $match.Groups[1].Value
                    }

                    $bareCommand = $expandedCommand.Trim().Trim('"').Trim("'")
                    if ($bareCommand -match '\.(cmd|ps1|py)$') {
                        $candidates += $bareCommand
                    }

                    foreach ($arg in @($hook.args)) {
                        if ($null -eq $arg) { continue }
                        $expandedArg = ([string]$arg) -replace '%USERPROFILE%', $env:USERPROFILE
                        $expandedArg = $expandedArg -replace '\$HOME', $env:USERPROFILE
                        $expandedArg = $expandedArg.Trim().Trim('"').Trim("'")
                        if ($expandedArg -match '\.(cmd|ps1|py)$') {
                            $candidates += $expandedArg
                        }
                    }

                    foreach ($scriptPath in @($candidates | Select-Object -Unique)) {
                        $found = Test-Path $scriptPath

                        if (-not $found -and $FallbackDir) {
                            $fallbackPath = Join-Path $FallbackDir (Split-Path -Leaf $scriptPath)
                            $found = Test-Path $fallbackPath
                        }

                        if (-not $found) {
                            $results.Valid = $false
                            $results.MissingPaths += $scriptPath
                        }
                    }
                }
            }
        }
    } catch {
        # Caller decides how to handle parse errors
    }

    return $results
}

<#
.SYNOPSIS
    Validate phase-table.json structure and README table sync.

.DESCRIPTION
    Dot-sourced by config-guardian.ps1 AFTER lib/common.ps1 and lib/validation.ps1.
    Assumes the caller has defined $ScriptDir and $RepoRoot.

    Provides:
    - Test-DispatchDocs  (DD-1..DD-2: phase-table.json structural validity +
                          README table sync via render-phase-table.ps1 -DryRun)
#>

function Test-DispatchDocs {
    <#
    .SYNOPSIS
        Validate phase-table.json structure and README table freshness.
    .PARAMETER CheckOnly
        Suppresses success output (CI-friendly). Errors still print.
    #>
    param([switch]$CheckOnly)

    $results = @{ Valid = $true; Errors = @() }

    $ExpectedSchema = 6
    $RequiredLanes = @('trivial', 'express', 'standard', 'max')
    $RequiredPhaseKeys = @('n', 'name', 'agent', 'delegated', 'default_strategy', 'recipe',
                           'inline_fallback_eligible', 'primary_signal', 'special_signals',
                           'verdict_in_body', 'display')

    # --- Check 1: phase-table.json valid + schema_version + structural keys ---

    $jsonPath = Join-Path $RepoRoot 'phases\phase-table.json'
    $table = $null

    if (-not (Test-Path $jsonPath)) {
        $results.Valid = $false
        $results.Errors += "phases/phase-table.json not found at $jsonPath"
    } else {
        try {
            $table = Get-Content $jsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            $results.Valid = $false
            $results.Errors += "phase-table.json is not valid JSON: $($_.Exception.Message)"
        }
    }

    if ($table) {
        if ($table.schema_version -ne $ExpectedSchema) {
            $results.Valid = $false
            $results.Errors += "schema_version is $($table.schema_version); expected $ExpectedSchema"
        }
        if (-not $table.phases -or @($table.phases).Count -eq 0) {
            $results.Valid = $false
            $results.Errors += 'phase-table.json has no non-empty phases array'
        } else {
            foreach ($phase in $table.phases) {
                $present = $phase.PSObject.Properties.Name
                $missing = $RequiredPhaseKeys | Where-Object { $_ -notin $present }
                if ($missing) {
                    $results.Valid = $false
                    $results.Errors += "phase $($phase.n) ($($phase.name)) missing keys: $($missing -join ', ')"
                }
                if ($phase.primary_signal) {
                    $psKeys = $phase.primary_signal.PSObject.Properties.Name
                    foreach ($k in @('value', 'match_mode')) {
                        if ($k -notin $psKeys) {
                            $results.Valid = $false
                            $results.Errors += "phase $($phase.n) primary_signal missing '$k'"
                        }
                    }
                }
                if ($phase.display) {
                    $dKeys = $phase.display.PSObject.Properties.Name
                    foreach ($k in @('command', 'signal_display')) {
                        if ($k -notin $dKeys) {
                            $results.Valid = $false
                            $results.Errors += "phase $($phase.n) display missing '$k'"
                        }
                    }
                }
            }

            # --- lanes structure (OPT-18 schema v6): each lane declares a phase list ---
            $phaseNums = @($table.phases | ForEach-Object { [int]$_.n })
            if (-not $table.lanes) {
                $results.Valid = $false
                $results.Errors += 'phase-table.json missing top-level lanes object (schema v6)'
            } else {
                foreach ($laneKey in $RequiredLanes) {
                    $lane = $table.lanes.$laneKey
                    if (-not $lane) {
                        $results.Valid = $false
                        $results.Errors += "lanes.$laneKey is missing"
                        continue
                    }
                    if (-not $lane.phases -or @($lane.phases).Count -eq 0) {
                        $results.Valid = $false
                        $results.Errors += "lanes.$laneKey has no non-empty phases array"
                        continue
                    }
                    foreach ($p in $lane.phases) {
                        $pn = [int]$p
                        # Phase 0 (max prereq lock-in) is valid but has no phase row.
                        if ($pn -ne 0 -and $pn -notin $phaseNums) {
                            $results.Valid = $false
                            $results.Errors += "lanes.$laneKey references unknown phase $pn"
                        }
                    }
                    foreach ($g in @($lane.gates)) {
                        if ($null -ne $g -and [int]$g -notin $phaseNums) {
                            $results.Valid = $false
                            $results.Errors += "lanes.$laneKey gates reference unknown phase $g"
                        }
                    }
                }
            }

            # --- transitions (e.g. README SKIPPED -> skip [8]) must target real phases ---
            foreach ($phase in $table.phases) {
                if ($phase.transitions) {
                    foreach ($t in $phase.transitions) {
                        if (-not $t.on_signal) {
                            $results.Valid = $false
                            $results.Errors += "phase $($phase.n) transition missing on_signal"
                        }
                        foreach ($sk in @($t.skip)) {
                            if ([int]$sk -notin $phaseNums) {
                                $results.Valid = $false
                                $results.Errors += "phase $($phase.n) transition skips unknown phase $sk"
                            }
                        }
                    }
                }
            }
        }
    }

    $check1Pass = ($results.Errors.Count -eq 0)
    if ($check1Pass -and $table) {
        if (-not $CheckOnly) {
            Write-Check "phase-table.json valid; schema_version $ExpectedSchema; $(@($table.phases).Count) phases + $(@($table.lanes.PSObject.Properties).Count) lanes with required keys"
        }
    }

    # --- Check 2: README table is in sync with a fresh render ---

    $readmePath   = Join-Path $RepoRoot 'phases\README.md'
    $rendererPath = Join-Path $ScriptDir 'render-phase-table.ps1'
    $StartMarker  = '<!-- obi:phase-table-start -->'
    $EndMarker    = '<!-- obi:phase-table-end -->'

    if (-not (Test-Path $readmePath)) {
        $results.Valid = $false
        $results.Errors += "phases/README.md not found at $readmePath"
    } elseif (-not (Test-Path $rendererPath)) {
        $results.Valid = $false
        $results.Errors += "tools/render-phase-table.ps1 not found at $rendererPath"
    } elseif ($table) {
        $readmeContent = [System.IO.File]::ReadAllText($readmePath)
        $si = $readmeContent.IndexOf($StartMarker)
        $ei = $readmeContent.IndexOf($EndMarker)
        if ($si -lt 0 -or $ei -lt 0 -or $ei -lt $si) {
            $results.Valid = $false
            $results.Errors += "phases/README.md is missing the table markers ($StartMarker / $EndMarker)"
        } else {
            $currentInner = $readmeContent.Substring($si + $StartMarker.Length, $ei - ($si + $StartMarker.Length))
            $rendered     = (& $rendererPath -DryRun -RepoRoot $RepoRoot) -join "`n"
            $normCurrent  = ($currentInner -replace "`r`n", "`n").Trim("`n")
            $normExpected = ($rendered     -replace "`r`n", "`n").Trim("`n")
            if ($normCurrent -ne $normExpected) {
                $results.Valid = $false
                $results.Errors += 'phases/README.md table is out of sync with phase-table.json; run tools/render-phase-table.ps1'
            } elseif (-not $CheckOnly) {
                Write-Check 'README table matches a fresh render from phase-table.json'
            }
        }
    }

    # Report errors
    if ($results.Errors.Count -gt 0) {
        foreach ($e in $results.Errors) {
            Write-Problem $e
        }
    }

    return $results
}

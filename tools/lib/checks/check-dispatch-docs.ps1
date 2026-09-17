<#
.SYNOPSIS
    Validate phase-table.json structure, README table sync, and Codex catch-log wiring.
#>

. (Join-Path $PSScriptRoot 'check-autonomous-recovery-docs.ps1')
. (Join-Path $PSScriptRoot 'check-efficiency-policy.ps1')

function Test-DispatchDocs {
    # CheckOnly suppresses success output; structural errors always print.
    param([switch]$CheckOnly)

    $results = @{ Valid = $true; Errors = @() }

    $ExpectedSchema = 7
    # Host ceiling for a single foreground tool call is 600 s; keep 30 s of slack
    # so the supervisor always wins the race to write a terminal status.
    $OuterCallCeilingSec = 570
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
        $efficiencyErrors = @(Get-EfficiencyPolicyError -Table $table)
        if ($efficiencyErrors.Count) { $results.Valid = $false; $results.Errors += $efficiencyErrors }
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
                if ([bool]$phase.delegated) {
                    $resolvedDelegation = if ($phase.delegation_policy) {
                        $phase.delegation_policy
                    } else {
                        $table.delegation_policy_defaults
                    }
                    if (-not $resolvedDelegation -or -not $resolvedDelegation.claude -or
                        -not $resolvedDelegation.codex) {
                        $results.Valid = $false
                        $results.Errors += "delegated phase $($phase.n) has no Claude/Codex phase policy or default"
                        continue
                    }
                    $claude = $resolvedDelegation.claude
                    $claudeKeys = @('nominal_work_sec', 'idle_sec', 'productive_extension_sec',
                                    'finalization_sec', 'absolute_sec', 'recent_progress_sec',
                                    'cleanup_margin_sec')
                    $claudeMissing = @($claudeKeys | Where-Object {
                        $_ -notin $claude.PSObject.Properties.Name
                    })
                    if ($claudeMissing.Count -gt 0) {
                        $results.Valid = $false
                        $results.Errors += "delegated phase $($phase.n) Claude policy missing: $($claudeMissing -join ', ')"
                    } else {
                        $sum = [int]$claude.nominal_work_sec + [int]$claude.productive_extension_sec +
                            [int]$claude.finalization_sec
                        if ($sum -ne [int]$claude.absolute_sec -or [int]$claude.nominal_work_sec -lt 1 -or
                            [int]$claude.idle_sec -lt 1 -or [int]$claude.recent_progress_sec -lt 1 -or
                            [int]$claude.cleanup_margin_sec -lt 1) {
                            $results.Valid = $false
                            $results.Errors += "delegated phase $($phase.n) has an invalid Claude budget relationship"
                        }
                        # The outer tool call is absolute_sec + cleanup_margin_sec. Exceed the
                        # host's 600 s foreground ceiling and the harness kills the supervisor
                        # before it can persist a terminal status: stale 'running', orphan tree.
                        $outerSec = [int]$claude.absolute_sec + [int]$claude.cleanup_margin_sec
                        if ($outerSec -gt $OuterCallCeilingSec) {
                            $results.Valid = $false
                            $results.Errors += ("delegated phase $($phase.n) outer call " +
                                "${outerSec}s (absolute_sec + cleanup_margin_sec) exceeds the " +
                                "${OuterCallCeilingSec}s host ceiling")
                        }
                    }
                    $codex = $resolvedDelegation.codex
                    $codexKeys = @('synthesis_due_sec', 'grace_sec', 'recent_progress_sec',
                                   'wait_window_max_sec', 'resume_window_count', 'resume_window_sec',
                                   'terminal_states', 'initial_classifications', 'synthesis_instruction')
                    $codexMissing = @($codexKeys | Where-Object {
                        $_ -notin $codex.PSObject.Properties.Name
                    })
                    if ($codexMissing.Count -gt 0) {
                        $results.Valid = $false
                        $results.Errors += "delegated phase $($phase.n) Codex policy missing: $($codexMissing -join ', ')"
                    }
                }
            }

            $delegatedRows = @($table.phases | Where-Object { [bool]$_.delegated })
            $dispatchRows = @($delegatedRows | Where-Object { $_.default_strategy -eq 'dispatch' })
            $nominalBudgets = @($dispatchRows | ForEach-Object {
                [int]$_.delegation_policy.claude.nominal_work_sec
            } | Sort-Object -Unique)
            if ($dispatchRows.Count -ge 3 -and $nominalBudgets.Count -lt 2) {
                $results.Valid = $false
                $results.Errors += 'Discovery, Author, and Learning must not share one unexplained Claude nominal budget'
            }
            $discovery = @($table.phases | Where-Object { [int]$_.n -eq 1 }) | Select-Object -First 1
            $research = $discovery.delegation_policy.codex.research
            $canonicalSteer = 'No more tools. Return the phase artifact now from current evidence; list missing sources instead of researching further.'
            if (-not $research -or [int]$research.targeted_local_call_limit -ne 12 -or
                [int]$research.official_source_batch_call_limit -ne 4 -or
                [int]$research.source_open_stop_sec -ne 180) {
                $results.Valid = $false
                $results.Errors += 'Discovery Codex policy must retain the 12 local / 4 official / 180-second research bounds'
            }
            $badSteers = @($delegatedRows | Where-Object {
                $resolved = if ($_.delegation_policy) {
                    $_.delegation_policy.codex
                } else {
                    $table.delegation_policy_defaults.codex
                }
                [string]$resolved.synthesis_instruction -cne $canonicalSteer
            })
            if ($badSteers.Count -gt 0) {
                $results.Valid = $false
                $results.Errors += 'delegated Codex phases must share the exact canonical no-tools synthesis instruction'
            }

            # --- lanes structure (OPT-18 schema v7): each lane declares a phase list ---
            $phaseNums = @($table.phases | ForEach-Object { [int]$_.n })
            if (-not $table.lanes) {
                $results.Valid = $false
                $results.Errors += 'phase-table.json missing top-level lanes object (schema v7)'
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
                    $acceptedSignals = @([string]$phase.primary_signal.value) +
                        @($phase.special_signals | ForEach-Object { [string]$_.value })
                    foreach ($t in $phase.transitions) {
                        if (-not $t.on_signal) {
                            $results.Valid = $false
                            $results.Errors += "phase $($phase.n) transition missing on_signal"
                        }
                        if (-not $t.skip -or @($t.skip).Count -eq 0) {
                            $results.Valid = $false
                            $results.Errors += "phase $($phase.n) transition '$($t.on_signal)' has no skip targets"
                        }
                        if ($t.on_signal -and -not @($acceptedSignals | Where-Object {
                            $_ -and ([string]$t.on_signal).StartsWith($_, [System.StringComparison]::Ordinal) -or
                                $_.StartsWith([string]$t.on_signal, [System.StringComparison]::Ordinal)
                        })) {
                            $results.Valid = $false
                            $results.Errors += "phase $($phase.n) transition '$($t.on_signal)' does not match a declared primary/special signal"
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

    # --- Check 3: Codex catch-log capture wiring present (OPT-19/21 regression guard) ---
    # The catch-log step is prose inside contract docs, not unit-testable code, so
    # nothing stopped it from silently going missing: the phase=plan invocation was
    # absent from obi-auto.md Phase 0 for a week after OPT-19 shipped, leaving
    # .obi/codex-catches.jsonl empty across all repos and OPT-21 unable to gather
    # data. This asserts the executable log-codex-catch.ps1 call still exists where
    # each capture path runs it, so a future edit can't drop it unnoticed.
    # The phase=plan wiring moved out of orchestration\obi-auto.md in 0.69.73: the
    # rigor=max Phase 0 / Gates 2-5 block now lives in policies\rigor-max-gates.md so a
    # plain /obi-auto run does not load ~180 lines it never uses. The guard follows the
    # prose to its new home rather than being relaxed.
    $catchWiring = @(
        @{ File = 'policies\rigor-max-gates.md';    Needles = @('log-codex-catch.ps1', '-Phase plan');   Label = 'rigor-max-gates.md Phase 0 plan-catch (phase=plan)' }
        @{ File = 'phases\05-integrate\command.md'; Needles = @('log-codex-catch.ps1', '-Phase review'); Label = '05-integrate review-catch (phase=review)' }
    )
    foreach ($w in $catchWiring) {
        $cwPath = Join-Path $RepoRoot $w.File
        if (-not (Test-Path $cwPath)) {
            $results.Valid = $false
            $results.Errors += "catch-log wiring: $($w.File) not found"
            continue
        }
        $cwText  = [System.IO.File]::ReadAllText($cwPath)
        $missing = @($w.Needles | Where-Object { $cwText -notlike "*$_*" })
        if ($missing.Count -gt 0) {
            $results.Valid = $false
            $results.Errors += "catch-log wiring missing in $($w.File): $($missing -join ', ') [$($w.Label)]"
        } elseif (-not $CheckOnly) {
            Write-Check "catch-log wiring present: $($w.Label)"
        }
    }

    # --- Check 4: recovery eligibility is derived from phase-table, never a stale name list ---
    $obiAutoPath = Join-Path $RepoRoot 'orchestration\obi-auto.md'
    if (-not (Test-Path -LiteralPath $obiAutoPath -PathType Leaf)) {
        $results.Valid = $false
        $results.Errors += 'orchestration/obi-auto.md not found for recovery-contract validation'
    } else {
        try { $obiAutoText = Read-ExpandedOrchestrationContract -Path $obiAutoPath -RepoRoot $RepoRoot }
        catch { $results.Valid = $false; $results.Errors += $_.Exception.Message; $obiAutoText = '' }
        $obiAutoNormalized = [regex]::Replace($obiAutoText, '\s+', ' ')

        # Issue #198: both operator-facing contracts must carry the same visible
        # drop-recovery templates, self-correction clause, grep form, and ordered example.
        $operationTimeoutsPath = Join-Path $RepoRoot 'docs\operation-timeouts.md'
        $dropContracts = @(
            @{ Label = 'orchestration/obi-auto.md'; Text = $obiAutoText }
        )
        if (-not (Test-Path -LiteralPath $operationTimeoutsPath -PathType Leaf)) {
            $results.Valid = $false
            $results.Errors += 'docs/operation-timeouts.md not found for drop-recovery contract validation'
        } else {
            $dropContracts += @{ Label = 'docs/operation-timeouts.md'; Text = [System.IO.File]::ReadAllText($operationTimeoutsPath) }
        }

        # Keep non-ASCII punctuation out of this PowerShell source file. Windows
        # PowerShell 5.1 decodes BOM-less scripts using the active ANSI code page,
        # while ReadAllText correctly decodes the UTF-8 Markdown contracts.
        $emDash = [char]0x2014
        $dropDetectedTemplate = "DROP DETECTED: <tool/phase> $emDash verifying with <check>, then <retry once | proceed | halt>"
        $dropResolvedTemplate = 'DROP RESOLVED: <already-applied | retried-ok | escalating>'
        $dropSelfCorrection = "If you reach the step-1 read after a sentinel without having emitted the DETECTED line, you skipped the contract $emDash emit it now."
        $dropGrepForm = '^DROP (DETECTED|RESOLVED):'
        $dropTranscript = @(
            "DROP DETECTED: Agent/Review $emDash verifying with .obi/state/dispatch-state.json, then retry once"
            'VERIFY: no completed Review artifact; retry budget available'
            'RETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS'
            'DROP RESOLVED: retried-ok'
        ) -join "`n"
        $dropTranscriptNormalized = [regex]::Replace($dropTranscript, '\s+', ' ').Trim()

        foreach ($contract in $dropContracts) {
            $normalized = [regex]::Replace([string]$contract.Text, '\s+', ' ')
            $missingDropMarkers = @($dropDetectedTemplate, $dropResolvedTemplate,
                $dropSelfCorrection, $dropGrepForm) | Where-Object { $normalized -cnotlike "*$_*" }
            if (@($missingDropMarkers).Count -gt 0) {
                $results.Valid = $false
                $results.Errors += "drop-recovery contract missing exact markers in $($contract.Label): $($missingDropMarkers -join ', ')"
            }

            $hasOrderedTranscript = $false
            foreach ($block in [regex]::Matches([string]$contract.Text,
                    '(?ms)^\s*```text\s*\r?\n(?<body>.*?)\r?\n\s*```\s*$')) {
                $bodyNormalized = [regex]::Replace($block.Groups['body'].Value, '\s+', ' ').Trim()
                if ($bodyNormalized -ceq $dropTranscriptNormalized) {
                    $hasOrderedTranscript = $true
                    break
                }
            }
            if (-not $hasOrderedTranscript) {
                $results.Valid = $false
                $results.Errors += "drop-recovery contract lacks the exact ordered text transcript in $($contract.Label)"
            }
        }

        if ($obiAutoText -notlike '*inline_fallback_eligible: false*' -or
            $obiAutoText -notlike '*phase-table.json*') {
            $results.Valid = $false
            $results.Errors += 'obi-auto recovery must derive inline-fallback ineligibility from phase-table.json'
        }
        if ($obiAutoText -match 'inline-(?:fallback-)?ineligible phases\s*\(') {
            $results.Valid = $false
            $results.Errors += 'obi-auto recovery contains a duplicated hard-coded inline-ineligible phase list'
        }

        $autonomousErrors = @(Get-AutonomousRecoveryDocError -RepoRoot $RepoRoot `
            -ObiAutoNormalized $obiAutoNormalized)
        if ($autonomousErrors.Count -gt 0) {
            $results.Valid = $false
            $results.Errors += $autonomousErrors
        }

        $orphanArchiveMarkers = @(
            'fresh-run startup',
            'archive-orphan-state.ps1',
            '-Mode Archive',
            'no_eligible_orphans',
            'active and live',
            'move-only manifest'
        )
        if (@($orphanArchiveMarkers | Where-Object {
                    $obiAutoText -notlike "*$_*"
                }).Count -gt 0) {
            $results.Valid = $false
            $results.Errors += 'obi-auto normal lifecycle paths must run the reversible orphan-state archive'
        }

        $trackerClosureMarkers = @(
            'lifecycle tracker',
            'blocked_open_trackers',
            'Status: OPEN',
            'never rewrite the tracker'
        )
        if (@($trackerClosureMarkers | Where-Object {
                    $obiAutoText -notlike "*$_*"
                }).Count -gt 0) {
            $results.Valid = $false
            $results.Errors += 'obi-auto completion must close lifecycle trackers before active-run archival'
        }

        $completionMarkers = @(
            'On successful Learning',
            'archive-run-state.ps1',
            'active RunId',
            'status is complete',
            'leave the remaining state in place'
        )
        if (@($completionMarkers | Where-Object { $obiAutoText -notlike "*$_*" }).Count -gt 0 -or
            $obiAutoText -match 'delete BOTH\s+`?dispatch-state\.json`?\s+AND\s+`?run-id\.txt`?') {
            $results.Valid = $false
            $results.Errors += 'obi-auto successful completion must archive active-run state so stale phase markers cannot survive'
        }
    }

    # --- Check 5: changed-file lint selections must fail closed (dogfood DF-18) ---
    $recipesPath = Join-Path $RepoRoot 'orchestration\inline-fallback-recipes.md'
    if (-not (Test-Path -LiteralPath $recipesPath -PathType Leaf)) {
        $results.Valid = $false
        $results.Errors += 'orchestration/inline-fallback-recipes.md not found for lint-contract validation'
    } else {
        $recipesText = [System.IO.File]::ReadAllText($recipesPath)
        if ($recipesText -notmatch 'Invoke-ScriptAnalyzer' -or
            $recipesText -notmatch 'once per selected path' -or
            $recipesText -notmatch [regex]::Escape('-ErrorAction Stop') -or
            $recipesText -notmatch [regex]::Escape('-Severity Error')) {
            $results.Valid = $false
            $results.Errors += 'PowerShell lint contract must invoke one path at a time with -Severity Error -ErrorAction Stop'
        }
        $unsafeLintExample = '`Invoke-ScriptAnalyzer\s+(?![^`\r\n]*-ErrorAction\s+Stop)(?![^`\r\n]*-Severity\s+Error)[^`\r\n]+`'
        if ([regex]::IsMatch($recipesText, $unsafeLintExample,
                [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
            $results.Valid = $false
            $results.Errors += 'PowerShell lint contract contains a bare ScriptAnalyzer invocation without the fail-closed severity/error flags'
        }
        $pythonLintMarkers = @('python -m ruff check <path>',
                               'existing changed Python paths',
                               'once per selected path',
                               'nonzero exit')
        foreach ($marker in $pythonLintMarkers) {
            if ($recipesText -notmatch [regex]::Escape($marker)) {
                $results.Valid = $false
                $results.Errors += 'Python lint contract must run Ruff once per existing changed Python path and fail closed'
                break
            }
        }
        if ($recipesText -match '(?i)(?:python\s+-m\s+)?ruff\s+check\s+\.(?:\s|`|$)') {
            $results.Valid = $false
            $results.Errors += 'Python lint contract must not scan the repository root for a changed-file gate'
        }
        $countMarkers = @('PassedCount', 'FailedCount', 'SkippedCount', 'TotalCount',
                          'NotRunCount', 'expected executed')
        foreach ($marker in $countMarkers) {
            if ($recipesText -notmatch [regex]::Escape($marker)) {
                $results.Valid = $false
                $results.Errors += 'Focused validation must define the filtered Pester executed count and assert the expected executed count'
                break
            }
        }
        if ($recipesText -notmatch [regex]::Escape('bash -n -c') -or
            $recipesText -match [regex]::Escape('bash -n <tempfile>')) {
            $results.Valid = $false
            $results.Errors += 'README review Bash parsing must use bash -n -c instead of a Windows temp-file path'
        }
        if ($recipesText -notmatch 'CLAUDE_CODE_GIT_BASH_PATH' -or
            $recipesText -notmatch [regex]::Escape('System32\bash.exe')) {
            $results.Valid = $false
            $results.Errors += 'README review must select Git Bash explicitly instead of ambient Windows/WSL Bash'
        }

        # DF-37/38: the repository-wide mixed suite exceeds the default five-minute
        # foreground ceiling. Release owns one fresh full run, decomposed into audited
        # components; Pester alone gets ten minutes while non-Pester work retains five.
        $recipesNormalized = [regex]::Replace($recipesText, '\s+', ' ')
        $releaseMarkers = @('-PowerShellOnly', '-ShardCount 3', '-ShardIndex', '-ListTargets',
                            '-PythonOnly', 'complete and disjoint',
                            'sole unconditional full-suite owner',
                            'exact timed-out process tree')
        foreach ($marker in $releaseMarkers) {
            if ($recipesNormalized -notmatch [regex]::Escape($marker)) {
                $results.Valid = $false
                $results.Errors += "Release test contract is missing bounded-shard marker: $marker"
            }
        }

        $recipeR = [regex]::Match($recipesText,
            '(?ms)^## Recipe R\b.*?(?=^## Recipe M\b)').Value
        $recipeRNormalized = [regex]::Replace($recipeR, '\s+', ' ')
        if (-not $recipeR -or
            $recipeRNormalized -notmatch [regex]::Escape('smallest focused selection') -or
            $recipeRNormalized -notmatch [regex]::Escape('full-suite sharding only when isolation is unsafe')) {
            $results.Valid = $false
            $results.Errors += 'Recipe R must keep Re-review focused and reserve full-suite sharding for unsafe isolation'
        }
        if ($recipeRNormalized -notmatch 'Pester-only selection.{0,160}timeout: 600000' -or
            $recipeRNormalized -notmatch 'non-Pester selection.{0,160}timeout: 300000') {
            $results.Valid = $false
            $results.Errors += 'Recipe R must give Pester selections 600000 ms while retaining 300000 ms for non-Pester selections'
        }

        $recipeG = [regex]::Match($recipesText, '(?ms)^## Recipe G\b.*\z').Value
        $recipeGNormalized = [regex]::Replace($recipeG, '\s+', ' ')
        if (-not $recipeG -or
            $recipeGNormalized -notmatch [regex]::Escape('TARGET=') -or
            $recipeGNormalized -notmatch [regex]::Escape('do not also run the monolithic command')) {
            $results.Valid = $false
            $results.Errors += 'Recipe G must audit target union and prohibit a duplicate monolithic test run'
        }
        if ($recipeGNormalized -notmatch 'Pester shard.{0,160}timeout: 600000' -or
            $recipeGNormalized -notmatch 'Python component.{0,160}timeout: 300000') {
            $results.Valid = $false
            $results.Errors += 'Recipe G must cap Pester shards at 600000 ms and the Python component at 300000 ms'
        }
        if ($recipeGNormalized -notmatch [regex]::Escape('CHANGELOG.md') -or
            $recipeGNormalized -notmatch [regex]::Escape('TODO: describe this release.') -or
            $recipeGNormalized -notmatch [regex]::Escape('Release history does not belong in `tools/version.yaml`')) {
            $results.Valid = $false
            $results.Errors += 'Recipe G must replace the CHANGELOG.md bump stub and keep release history out of version.yaml'
        }
        if ($recipeGNormalized -notmatch [regex]::Escape('Get-Item -LiteralPath $sourcePath -ErrorAction Stop') -or
            $recipeGNormalized -notmatch [regex]::Escape('Get-ChildItem -LiteralPath $sourcePath -Recurse -File -ErrorAction Stop') -or
            $recipeGNormalized -notmatch [regex]::Escape('Any enumeration error or remaining hit fails the recipe')) {
            $results.Valid = $false
            $results.Errors += 'Recipe G version scan must enumerate each source path with terminating errors'
        }
    }

    # --- Check 6: release entry points delegate to Recipe G without stale unsafe copies ---
    $phaseReleasePath = Join-Path $RepoRoot 'phases\09-release\command.md'
    if (-not (Test-Path -LiteralPath $phaseReleasePath -PathType Leaf)) {
        $results.Valid = $false
        $results.Errors += 'phases/09-release/command.md not found for release-contract validation'
    } else {
        $phaseReleaseText = [System.IO.File]::ReadAllText($phaseReleasePath)
        $phaseNeedles = @('Recipe G', 'orchestration/inline-fallback-recipes.md',
                          'policies/zero-hallucination.md', 'policies/hard-stop-conditions.md',
                          'policies/vendor-rules.md', 'explicit file list')
        foreach ($needle in $phaseNeedles) {
            if ($phaseReleaseText -notmatch [regex]::Escape($needle)) {
                $results.Valid = $false
                $results.Errors += "Phase 9 must defer to the canonical safe release contract: missing $needle"
            }
        }
        $deployedPolicyNeedles = @('docs/policies/zero-hallucination.md',
                                   'docs/policies/hard-stop-conditions.md',
                                   'docs/policies/vendor-rules.md')
        foreach ($needle in $deployedPolicyNeedles) {
            if ($phaseReleaseText -notmatch [regex]::Escape($needle)) {
                $results.Valid = $false
                $results.Errors += "Phase 9 must retain the Claude deployed policy mirror: missing $needle"
            }
        }
        if ($phaseReleaseText -match '(?im)^\s*\d+\.\s*Stage changes:\s*git add -A\s*$') {
            $results.Valid = $false
            $results.Errors += 'Phase 9 contains blanket git add -A staging'
        }
    }

    $releaseAgentPath = Join-Path $RepoRoot 'platforms\claude-code\agents\obi-release-gate.md'
    if (-not (Test-Path -LiteralPath $releaseAgentPath -PathType Leaf)) {
        $results.Valid = $false
        $results.Errors += 'Claude release-gate agent not found for release-contract validation'
    } else {
        $releaseAgentText = [System.IO.File]::ReadAllText($releaseAgentPath)
        $agentNeedles = @('Recipe G', 'complete/disjoint', 'full local `tools/deploy.ps1`',
                          'CHANGELOG.md', 'release prose never goes in', 'explicit file list',
                          'do not invent', '.nuspec', '.github/workflows/')
        foreach ($needle in $agentNeedles) {
            if ($releaseAgentText -notmatch [regex]::Escape($needle)) {
                $results.Valid = $false
                $results.Errors += "Claude release agent is missing canonical release guidance: $needle"
            }
        }
        if ($releaseAgentText -match [regex]::Escape('You do NOT push, create MRs, or deploy.')) {
            $results.Valid = $false
            $results.Errors += 'Claude release agent contradicts its required local live-deploy verification'
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

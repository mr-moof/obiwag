<#
.SYNOPSIS
    Step 2 — Claude Code deployment for the Obi Wag deploy pipeline (OPT-12, #186).

.DESCRIPTION
    Extracted from deploy.ps1. Dot-sourced into deploy.ps1's scope (after
    lib/common.ps1, lib/deploy-common.ps1, lib/deploy-manifest.ps1), so it sees
    the caller's $script:manifest and the source/target path variables.

    Depends on: lib/common.ps1 (Write-*, Test-HookCommandPaths),
    lib/deploy-common.ps1 (Copy-SingleFile, Copy-DirectoryContents),
    lib/deploy-manifest.ps1 (Add-ManifestEntry, Add-ManifestBulk).

    Provides:
      - Invoke-ClaudeDeploy : deploy commands/agents/orchestration/CLAUDE.md/
        statusline/hooks/docs/policies/skills/tools/settings, sync shared
        permissions, validate 20 commands + 13 agents, write the manifest, and
        run the config-guardian post-deploy check.
#>

# Deploy all Claude Code configuration. Reads $ClaudeTarget, $PhasesDir,
# $PlatformsDir, $OrchestrationDir, $RepoRoot, $ScriptDir, $HooksDir, $DocsDir,
# $PoliciesDir, $SkillsDir, $ToolsTarget, $UsersDir from the deploy.ps1 caller
# scope. The `exit 1` on incomplete command set terminates the whole script.
function Protect-ClaudeSettingsBackup {
    param(
        [string]$SettingsTarget,
        [switch]$DryRun
    )

    if (-not (Test-Path -LiteralPath $SettingsTarget -PathType Leaf)) { return }

    $backupPath = "$SettingsTarget.backup"
    if (Test-Path -LiteralPath $backupPath) {
        Write-Info 'settings.json.backup already exists (preserved from earlier deploy)'
    } elseif ($DryRun) {
        Write-Info 'Would backup: settings.json -> settings.json.backup (one-time, preserves user-original)'
    } else {
        Copy-Item -LiteralPath $SettingsTarget -Destination $backupPath -ErrorAction Stop
        Write-Info 'Backed up settings.json -> settings.json.backup (one-time, preserves user-original)'
    }
}

function Invoke-ClaudeConfigGuardian {
    param(
        [string]$GuardianScript,
        [string]$RepoRoot
    )

    & $GuardianScript -RepoRoot $RepoRoot -CheckOnly -NoIssue -NoSnapshot
    $guardianExitCode = $LASTEXITCODE
    if ($guardianExitCode -ne 0) {
        Add-DeployFailure -Stage 'ConfigGuardian' -Detail "Exited with code $guardianExitCode"
        return $false
    }
    return $true
}

function Invoke-ClaudeDeploy {
    param(
        [switch]$DryRun,
        [switch]$SyncProjectPermissions
    )

    Write-Step 'Deploying Claude Code configuration...'

    $commandsTarget = Join-Path $ClaudeTarget 'commands'
    $agentsTarget   = Join-Path $ClaudeTarget 'agents'

    # -- Phase commands: phases/0N-name/command.md -> commands/<name>.md --
    if (Test-Path $PhasesDir) {
        Get-ChildItem -Path $PhasesDir -Directory | ForEach-Object {
            $phaseDir  = $_.FullName
            $phaseName = $_.Name

            # Extract command name: strip leading "0N-" prefix
            $cmdName = $phaseName -replace '^\d+-', ''

            # Deploy command.md -> commands/<cmdName>.md
            $cmdSource = Join-Path $phaseDir 'command.md'
            if (Test-Path $cmdSource) {
                $cmdDest = Join-Path $commandsTarget "$cmdName.md"
                Copy-SingleFile -Source $cmdSource -Destination $cmdDest -DryRun:$DryRun | Out-Null
                Add-ManifestEntry "commands/$cmdName.md" "phases/$phaseName/command.md"
            }
        }
        Write-Check 'Phase commands deployed'
    }

    # -- Claude Code agents: platforms/claude-code/agents/*.md -> agents/*.md --
    $claudeAgentsSource = Join-Path $PlatformsDir 'claude-code\agents'
    if (Test-Path $claudeAgentsSource) {
        Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' | ForEach-Object {
            $dest = Join-Path $agentsTarget $_.Name
            Copy-SingleFile -Source $_.FullName -Destination $dest -DryRun:$DryRun | Out-Null
            Add-ManifestEntry "agents/$($_.Name)" "platforms/claude-code/agents/$($_.Name)"
        }
        Write-Check 'Claude Code agents deployed'
    } else {
        Write-Warning "Claude Code agents source not found: $claudeAgentsSource"
    }

    # -- Orchestration commands --
    if (Test-Path $OrchestrationDir) {
        # Top-level: obi.md, obi-auto.md
        Get-ChildItem -Path $OrchestrationDir -File -Filter '*.md' | ForEach-Object {
            $dest = Join-Path $commandsTarget $_.Name
            Copy-SingleFile -Source $_.FullName -Destination $dest -DryRun:$DryRun | Out-Null
            Add-ManifestEntry "commands/$($_.Name)" "orchestration/$($_.Name)"
        }

        # Utilities: obi-collect.md, obi-memory-review.md, obi-update.md, obi-swarm.md
        $utilitiesDir = Join-Path $OrchestrationDir 'utilities'
        if (Test-Path $utilitiesDir) {
            Get-ChildItem -Path $utilitiesDir -File -Filter '*.md' | ForEach-Object {
                $dest = Join-Path $commandsTarget $_.Name
                Copy-SingleFile -Source $_.FullName -Destination $dest -DryRun:$DryRun | Out-Null
                Add-ManifestEntry "commands/$($_.Name)" "orchestration/utilities/$($_.Name)"
            }
        }
        Write-Check 'Orchestration commands deployed'
    }

    # -- Global CLAUDE.md (the per-session contract; the repo-root CLAUDE.md is repo context only) --
    $claudeMdSource = Join-Path $PlatformsDir 'claude-code\CLAUDE.global.md'
    $claudeMdTarget = Join-Path $ClaudeTarget 'CLAUDE.md'
    if (Test-Path $claudeMdSource) {
        Copy-SingleFile -Source $claudeMdSource -Destination $claudeMdTarget -DryRun:$DryRun | Out-Null
        Add-ManifestEntry 'CLAUDE.md' 'platforms/claude-code/CLAUDE.global.md'
        Write-Check 'CLAUDE.md deployed (from platforms/claude-code/CLAUDE.global.md)'
    }

    # -- StatusLine script (.ps1 variant) --
    $statuslineSource = Join-Path $ScriptDir 'statusline-command.ps1'
    $statuslineTarget = Join-Path $ClaudeTarget 'statusline-command.ps1'
    if (Test-Path $statuslineSource) {
        Copy-SingleFile -Source $statuslineSource -Destination $statuslineTarget -DryRun:$DryRun | Out-Null
        Add-ManifestEntry 'statusline-command.ps1' 'tools/statusline-command.ps1'
        Write-Check 'StatusLine script deployed'
    }

    # -- StatusLine script (.sh variant — the ACTIVE statusline per settings.json) --
    $statuslineShSource = Join-Path $ScriptDir 'statusline-command.sh'
    $statuslineShTarget = Join-Path $ClaudeTarget 'statusline-command.sh'
    if (Test-Path $statuslineShSource) {
        Copy-SingleFile -Source $statuslineShSource -Destination $statuslineShTarget -DryRun:$DryRun | Out-Null
        Add-ManifestEntry 'statusline-command.sh' 'tools/statusline-command.sh'
        Write-Check 'StatusLine .sh (active) deployed'
    }

    # -- Hooks --
    if (Test-Path $HooksDir) {
        $hooksTarget = Join-Path $ClaudeTarget 'hooks'
        Copy-DirectoryContents -Source $HooksDir -Destination $hooksTarget -DryRun:$DryRun
        Add-ManifestBulk $HooksDir 'hooks' 'hooks'
        Write-Info 'Deployed hooks/'

        if (-not $env:CLAUDE_PROJECT_ROOT) {
            Write-Warning ''
            Write-Warning 'CLAUDE_PROJECT_ROOT environment variable is NOT set!'
            Write-Warning 'Hooks require this variable to function properly.'
            Write-Warning ''
            Write-Warning 'To fix, run:'
            Write-Warning "  [System.Environment]::SetEnvironmentVariable('CLAUDE_PROJECT_ROOT', '$RepoRoot', 'User')"
        } else {
            Write-Check "CLAUDE_PROJECT_ROOT is set: $env:CLAUDE_PROJECT_ROOT"
        }
    }

    # -- Docs --
    if (Test-Path $DocsDir) {
        $docsTarget = Join-Path $ClaudeTarget 'docs'
        Copy-DirectoryContents -Source $DocsDir -Destination $docsTarget -DryRun:$DryRun
        Add-ManifestBulk $DocsDir 'docs' 'docs'
        Write-Info 'Deployed docs/'
    }

    # -- Policies -> docs/policies/ --
    if (Test-Path $PoliciesDir) {
        $policiesTarget = Join-Path (Join-Path $ClaudeTarget 'docs') 'policies'
        Copy-DirectoryContents -Source $PoliciesDir -Destination $policiesTarget -DryRun:$DryRun
        Add-ManifestBulk $PoliciesDir 'policies' 'docs/policies'
        Write-Info 'Deployed policies/ -> docs/policies/'
    }

    # -- Skills --
    if (Test-Path $SkillsDir) {
        $skillsTarget = Join-Path $ClaudeTarget 'skills'
        Copy-DirectoryContents -Source $SkillsDir -Destination $skillsTarget -DryRun:$DryRun
        Add-ManifestBulk $SkillsDir 'skills' 'skills'
        Write-Info 'Deployed skills/'
    }

    # -- Grounding patterns (.obi/patterns -> ~/.claude/.obi/patterns) --
    # pattern_matcher.load_patterns() reads this deployed copy FIRST, then the
    # source repo, then user overrides. Without this step a machine with no
    # source-repo checkout loads zero patterns, which made grounding silently a
    # dev-workstation-only feature (issue #202).
    $patternsSource = Join-Path $RepoRoot '.obi\patterns'
    if (Test-Path $patternsSource) {
        $patternsTarget = Join-Path $ClaudeTarget '.obi\patterns'
        Copy-DirectoryContents -Source $patternsSource -Destination $patternsTarget -DryRun:$DryRun

        # MIRROR, not merge. Copy-DirectoryContents only copies files in, so a
        # pattern deleted or renamed at source would linger in the deployed
        # directory and keep grounding as a ghost -- and because load_patterns()
        # reads the deployed copy too, nothing else would ever remove it.
        if (Test-Path $patternsTarget) {
            $sourceNames = @(Get-ChildItem -Path $patternsSource -Filter '*.md' -File |
                Select-Object -ExpandProperty Name)
            foreach ($deployed in @(Get-ChildItem -Path $patternsTarget -Filter '*.md' -File)) {
                if ($sourceNames -notcontains $deployed.Name) {
                    if ($DryRun) {
                        Write-Info "  [DryRun] would remove stale pattern: $($deployed.Name)"
                    } else {
                        Remove-Item -LiteralPath $deployed.FullName -Force
                        Write-Info "  Removed stale deployed pattern: $($deployed.Name)"
                    }
                }
            }
        }

        Add-ManifestBulk $patternsSource '.obi/patterns' '.obi/patterns'
        Write-Info 'Deployed .obi/patterns/'
    }

    # -- Tools (Phase 0 protocol scripts, probes, gates, config-guardian) --
    # Deployed to the shared $ToolsTarget\tools\ directory. Deployment
    # also sets $env:OBI_HOME (User scope) so callers can resolve scripts as
    # $env:OBI_HOME\tools\<name>.ps1 without hardcoding the path.
    if (Test-Path $ScriptDir) {
        $toolsDeployDest = Join-Path $ToolsTarget 'tools'
        Copy-DirectoryContents -Source $ScriptDir -Destination $toolsDeployDest -DryRun:$DryRun
        Add-ManifestBulk $ScriptDir 'tools' 'tools'

        if (-not $DryRun) {
            $existingObiHome = [System.Environment]::GetEnvironmentVariable('OBI_HOME', 'User')
            if ($existingObiHome -ne $ToolsTarget) {
                [System.Environment]::SetEnvironmentVariable('OBI_HOME', $ToolsTarget, 'User')
                Write-Info "Set OBI_HOME=$ToolsTarget (User scope) - restart Claude Code to pick it up"
            }
            $env:OBI_HOME = $ToolsTarget
        }

        Write-Info "Deployed tools/ -> $toolsDeployDest"
    }

    # -- phase-table.json: lane/routing single source of truth, read at RUNTIME by
    #    tools/classify-lane.ps1 (OPT-18). Deployed to $OBI_HOME/phases so the classifier
    #    resolves it via its script-sibling `..\phases` lookup after deploy. (Drift detection
    #    skips it: it lives under OBI_HOME, not ~/.claude.)
    $phaseTableSource = Join-Path $PhasesDir 'phase-table.json'
    if (Test-Path $phaseTableSource) {
        $phaseTableDest = Join-Path $ToolsTarget 'phases\phase-table.json'
        Copy-SingleFile -Source $phaseTableSource -Destination $phaseTableDest -DryRun:$DryRun | Out-Null
        # The Claude manifest is rooted at ~/.claude. OBI_HOME has a separate
        # deployment root, so recording this path there would misclassify a
        # nonexistent ~/.claude/phases target as Claude-owned.
        Write-Info "Deployed phase-table.json -> $phaseTableDest"
    }

    # -- User settings (Issue #10: validate hook paths + backup) --
    $userSettingsSource = Join-Path $UsersDir (Join-Path $env:USERNAME 'settings.json')
    if (Test-Path $userSettingsSource) {
        $settingsTarget = Join-Path $ClaudeTarget 'settings.json'

        # Backup existing settings before overwriting — but ONLY ONCE (#168).
        # The previous behavior overwrote .backup on every deploy, so after the
        # 2nd redeploy the backup contained the prior Obi-deployed file, NOT the
        # user's pre-Obi original. Preserving the first backup guarantees the
        # uninstall path (and any manual restore) can recover the user's
        # original state regardless of how many subsequent deploys ran.
        Protect-ClaudeSettingsBackup -SettingsTarget $settingsTarget -DryRun:$DryRun

        # Validate hook paths in the source settings
        $hookValidation = Test-HookCommandPaths -SettingsPath $userSettingsSource -FallbackDir $HooksDir
        if (-not $hookValidation.Valid) {
            foreach ($missingPath in $hookValidation.MissingPaths) {
                Write-Warning "Hook path not found at deployed or source location: $missingPath"
            }
        }

        Copy-SingleFile -Source $userSettingsSource -Destination $settingsTarget -DryRun:$DryRun | Out-Null
        Add-ManifestEntry 'settings.json' "users/$env:USERNAME/settings.json"
        Write-Check "User settings deployed for $env:USERNAME"
    } else {
        Write-Info "No user-specific settings found at: $userSettingsSource"
    }

    # -- Sync shared permissions to project settings.local.json (Issue #74) --
    # Default-off opt-in (#170). Without -SyncProjectPermissions, deploy
    # previews which projects WOULD be affected and skips the write. Old
    # behavior preserved with the explicit flag.
    $sharedPermsFile = Join-Path $RepoRoot (Join-Path 'config' 'permissions-allow.json')
    if (Test-Path $sharedPermsFile) {
        if ($SyncProjectPermissions) {
            Write-Step 'Syncing shared permissions to project settings.local.json files...'
        } else {
            Write-Step 'Scanning project settings.local.json files (no writes — pass -SyncProjectPermissions to opt in)...'
        }

        $sharedData = Get-Content $sharedPermsFile -Raw | ConvertFrom-Json
        $sharedAllows = @($sharedData.permissions.allow)
        $sourceDir = Join-Path $env:USERPROFILE 'source'
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        $syncCount = 0
        $previewCount = 0

        if (Test-Path $sourceDir) {
            Get-ChildItem -Path $sourceDir -Directory | ForEach-Object {
                $projectClaudeDir = Join-Path $_.FullName '.claude'
                $localSettingsPath = Join-Path $projectClaudeDir 'settings.local.json'

                # Merge: shared + existing (deduplicated), preserve _managed_by marker
                $existingAllows = @()
                if (Test-Path $localSettingsPath) {
                    try {
                        $existing = Get-Content $localSettingsPath -Raw | ConvertFrom-Json
                        if ($existing.permissions -and $existing.permissions.allow) {
                            $existingAllows = @($existing.permissions.allow)
                        }
                    } catch {
                        Write-Info "Skipped (unparseable): $localSettingsPath"
                        return  # Skip this project (ForEach-Object return)
                    }
                }

                $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

                $outputObj = @{
                    '_managed_by' = 'obi-deploy'
                    'permissions' = @{
                        'allow' = $merged
                    }
                }

                if (-not $SyncProjectPermissions) {
                    # Default-off path: report but skip write.
                    Write-Info "Would sync (need -SyncProjectPermissions): $($_.Name) ($($merged.Count) entries)"
                    $previewCount++
                } elseif ($DryRun) {
                    Write-Info "Would sync: $($_.Name) ($($merged.Count) entries)"
                    $syncCount++
                } else {
                    if (-not (Test-Path $projectClaudeDir)) {
                        New-Item -ItemType Directory -Path $projectClaudeDir -Force | Out-Null
                    }
                    $jsonText = $outputObj | ConvertTo-Json -Depth 5
                    [System.IO.File]::WriteAllText($localSettingsPath, $jsonText, $utf8NoBom)
                    Write-Info "Synced: $($_.Name) ($($merged.Count) entries)"
                    $syncCount++
                }
            }
        }

        if ($SyncProjectPermissions -and $DryRun) {
            Write-Check "Shared permissions sync preview ($syncCount project(s) would be written; DryRun)"
        } elseif ($SyncProjectPermissions) {
            Write-Check "Shared permissions synced to $syncCount project(s)"
        } else {
            Write-Check "Shared permissions scan complete ($previewCount project(s) would be synced with -SyncProjectPermissions; default skip per #170)"
        }
    }

    # -- Validate all 17 commands deployed --
    if (-not $DryRun) {
        $requiredCommands = @(
            'author.md', 'discovery.md', 'integrate.md',
            'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
            'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
            'readme-review.md', 'release.md', 're-review.md', 'review.md',
            'simplify.md'
        )
        $installed = (Get-ChildItem "$commandsTarget\*.md" -ErrorAction SilentlyContinue).Name
        $missing = $requiredCommands | Where-Object { $_ -notin $installed }

        if ($missing) {
            Write-Warning ''
            Write-Warning 'INCOMPLETE DEPLOYMENT DETECTED!'
            Write-Warning "Missing commands: $($missing -join ', ')"
            Write-Warning ''
            Write-Warning 'The 10-phase workflow requires all 20 commands.'
            exit 1
        }
        Write-Check "All $($requiredCommands.Count) commands verified"

        # Validate agents (all 12 workflow agents from platforms/claude-code/agents/)
        $requiredAgents = @(
            'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
            'obi-reviewer.md', 'obi-integrator.md', 'obi-rereviewer.md',
            'obi-readme.md', 'obi-readme-verifier.md', 'obi-release-gate.md',
            'obi-learner.md',
            'obi-pipeline-monitor.md', 'obi-swarm-worker.md'
        )
        $installedAgents = (Get-ChildItem "$agentsTarget\*.md" -ErrorAction SilentlyContinue).Name
        $missingAgents = $requiredAgents | Where-Object { $_ -notin $installedAgents }

        if ($missingAgents) {
            Write-Warning "Missing agents: $($missingAgents -join ', ')"
        } else {
            Write-Check "All $($requiredAgents.Count) agents verified"
        }
    }

    # Step 2.5: Restore project memory from backup if missing
    # Safety net for external wipes (e.g., external tooling reset).
    # Only restores when memory directory is empty/missing; never overwrites.
    if (-not $DryRun) {
        $projectsDir = Join-Path $ClaudeTarget 'projects'
        $memoryRestored = $false

        if (Test-Path $projectsDir) {
            # Find the project memory directory (home-encoded)
            # Encoding mirrors hooks/core/project_memory.py:_encode_path_for_claude()
            $homePath = $env:USERPROFILE
            $encoded = ($homePath -replace '\\', '/') -replace ':/', '--'
            $encoded = $encoded -replace '/', '-'
            $encoded = $encoded.TrimEnd('-')
            $memoryDir = Join-Path $projectsDir "$encoded\memory"

            $memoryExists = (Test-Path $memoryDir) -and
                (Get-ChildItem -Path $memoryDir -File -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0

            if (-not $memoryExists) {
                $backupDir = Join-Path $ClaudeTarget '.obi\backups\project-memory'
                if (Test-Path $backupDir) {
                    $latestSnapshot = Get-ChildItem -Path $backupDir -Directory -Filter 'snapshot-*' |
                        Sort-Object Name -Descending | Select-Object -First 1

                    if ($latestSnapshot) {
                        $indexPath = Join-Path $latestSnapshot.FullName 'backup-index.json'
                        if (Test-Path $indexPath) {
                            # Validate sentinel
                            try {
                                $index = Get-Content $indexPath -Raw | ConvertFrom-Json
                                if ($index.file_count -gt 0) {
                                    if (-not (Test-Path $memoryDir)) {
                                        New-Item -ItemType Directory -Path $memoryDir -Force | Out-Null
                                    }
                                    # Copy all files except backup-index.json
                                    Get-ChildItem -Path $latestSnapshot.FullName -File |
                                        Where-Object { $_.Name -ne 'backup-index.json' } |
                                        ForEach-Object { Copy-Item $_.FullName $memoryDir -Force }
                                    $restoredCount = (Get-ChildItem -Path $memoryDir -File | Measure-Object).Count
                                    Write-Warning "Restored $restoredCount project memory files from backup ($($latestSnapshot.Name))"
                                    $memoryRestored = $true
                                }
                            } catch {
                                Write-Warning "Failed to restore project memory from backup: $_"
                            }
                        } else {
                            Write-Detail "Latest backup snapshot missing sentinel file, skipping restore"
                        }
                    }
                }

                if (-not $memoryRestored -and -not $memoryExists) {
                    Write-Detail "Project memory directory empty/missing and no valid backup found"
                }
            } else {
                Write-Check "Project memory directory intact (restore not needed)"
            }
        }
    }

    # Step 2.6: Post-deploy validation
    # config-guardian now lives permanently at $ToolsTarget\tools\, which is
    # user-local. The previous staging-and-cleanup dance is no longer needed.
    if (-not $DryRun) {
        Write-Step 'Running Config Guardian post-deploy validation...'
        $guardianScript = Join-Path $ToolsTarget 'tools\config-guardian.ps1'
        Invoke-ClaudeConfigGuardian -GuardianScript $guardianScript -RepoRoot $RepoRoot | Out-Null

        # Commit the new ownership baseline only after copies and validation
        # succeed. A failed deployment keeps the prior manifest intact so its
        # hashes remain an honest record of the last successful installation.
        Write-ClaudeDeploymentManifest
    }

    Write-Check 'Claude Code deployment complete'
    Write-Info "Target: $ClaudeTarget"
}

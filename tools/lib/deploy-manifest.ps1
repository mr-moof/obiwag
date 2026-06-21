<#
.SYNOPSIS
    Deployment-manifest + collision-aware cleanup for the Obi Wag deploy
    pipeline (OPT-12, #186).

.DESCRIPTION
    Extracted from deploy.ps1. Dot-sourced into deploy.ps1's scope (after
    lib/common.ps1 and lib/deploy-common.ps1), so functions here see the
    caller's $script:-scoped state ($script:manifest, $script:obiCollisions,
    $script:deployFailures) and the source/target path variables.

    Depends on: lib/common.ps1 (Write-*), lib/deploy-common.ps1 (Test-PathUnderRoot).

    Provides:
      - Add-ManifestEntry / Add-ManifestBulk : record deployed-rel -> source-rel (#136)
      - Get-Obi*FromRepo                      : repo-enumeration cleanup fallbacks (#147)
      - Remove-ObiOwnedFile                   : collision-aware remove with .user-backup (#160/#167)
      - Invoke-ClaudeCleanup                  : Step 1 stale-artifact cleanup + collision gate
#>

# Deployment manifest helpers. $script:manifest is initialized by deploy.ps1.
function Add-ManifestEntry {
    param([string]$DeployedRel, [string]$SourceRel)
    $script:manifest[$DeployedRel.Replace('\', '/')] = $SourceRel.Replace('\', '/')
}

function Add-ManifestBulk {
    param([string]$SourceDir, [string]$SourcePrefix, [string]$DeployedPrefix)
    if (-not (Test-Path $SourceDir)) { return }
    Get-ChildItem -Path $SourceDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
        $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*'
    } | ForEach-Object {
        $rel = $_.FullName.Substring($SourceDir.Length + 1)
        Add-ManifestEntry "$DeployedPrefix/$rel" "$SourcePrefix/$rel"
    }
}

# Repo-source enumeration (used as fallback for any cleanup category with 0
# manifest entries, #147). These read the source-dir path variables
# ($SkillsDir, $RepoRoot, $OrchestrationDir, $PlatformsDir, $DocsDir,
# $PoliciesDir, $HooksDir) from the deploy.ps1 caller scope at call time.
function Get-ObiSkillsFromRepo {
    return @(Get-ChildItem -Path $SkillsDir -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
}
function Get-ObiCommandsFromRepo {
    $out = @()
    $out += @(Get-ChildItem -Path (Join-Path $RepoRoot 'phases') -Directory -ErrorAction SilentlyContinue | ForEach-Object { ($_.Name -replace '^\d+-', '') + '.md' })
    $out += @(Get-ChildItem -Path $OrchestrationDir -File -Filter '*.md' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $out += @(Get-ChildItem -Path (Join-Path $OrchestrationDir 'utilities') -File -Filter '*.md' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    return $out
}
function Get-ObiAgentsFromRepo {
    return @(Get-ChildItem -Path (Join-Path $PlatformsDir 'claude-code\agents') -File -Filter '*.md' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
}
function Get-ObiDocsFromRepo {
    # docs/* and policies/* are both deployed under ~/.claude/docs/ via Add-ManifestBulk.
    # Apply the same __pycache__ / .pytest_cache filter so the fallback set matches
    # what the manifest would have contained.
    $out = @()
    if (Test-Path $DocsDir) {
        $out += @(Get-ChildItem -Path $DocsDir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*' } |
            ForEach-Object { $_.FullName.Substring($DocsDir.Length + 1).Replace('\', '/') })
    }
    if (Test-Path $PoliciesDir) {
        $out += @(Get-ChildItem -Path $PoliciesDir -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*' } |
            ForEach-Object { 'policies/' + $_.FullName.Substring($PoliciesDir.Length + 1).Replace('\', '/') })
    }
    return $out
}
function Get-ObiHooksFromRepo {
    return @(Get-ChildItem -Path $HooksDir -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*' } |
        ForEach-Object { $_.FullName.Substring($HooksDir.Length + 1).Replace('\', '/') })
}

# Collision-aware remove (#167). Compares target file's SHA256 against the
# source file (the manifest's source-rel resolved to an absolute path). If
# the target has been user-modified (hashes differ):
#   - Records the collision in $script:obiCollisions for the post-cleanup
#     gate to read.
#   - If $Force: copies target to <target>.user-backup (only if
#     .user-backup doesn't already exist) AND removes the target.
#   - If not $Force: skips the remove (target preserved for user).
# If the source can't be resolved (no manifest entry / fallback path),
# falls through to the legacy unconditional-remove behavior — no regression
# on pre-167 deploys.
function Remove-ObiOwnedFile {
    param(
        [string]$TargetFile,
        [string]$DeployedRel,
        [string]$CategoryLabel,
        [string]$SourceRel,
        [string]$RepoRoot,
        [switch]$DryRun,
        [switch]$Force
    )

    $sourcePath = $null
    if ($SourceRel) {
        if ([System.IO.Path]::IsPathRooted($SourceRel)) {
            $sourcePath = $SourceRel  # external command (172) — absolute
        } else {
            $sourcePath = Join-Path $RepoRoot $SourceRel
        }
    }

    if ($sourcePath -and (Test-Path -LiteralPath $sourcePath)) {
        try {
            $tgtHash = (Get-FileHash -LiteralPath $TargetFile -Algorithm SHA256).Hash
            $srcHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash
            if ($tgtHash -ne $srcHash) {
                $script:obiCollisions += [pscustomobject]@{
                    Category = $CategoryLabel
                    Target   = $TargetFile
                    Source   = $sourcePath
                }
                if (-not $Force) {
                    return  # skip the remove; target preserved
                }
                # -Force: backup before remove (preserve user's earliest version).
                # DryRun preview must NOT write to disk (Codex Recipe 1 #1).
                $backupPath = "$TargetFile.user-backup"
                if ($DryRun) {
                    if (-not (Test-Path -LiteralPath $backupPath)) {
                        Write-Info "Would back up user-modified $CategoryLabel -> .user-backup: $DeployedRel"
                    } else {
                        Write-Info "Would skip backup (earlier .user-backup preserved): $DeployedRel"
                    }
                } else {
                    if (-not (Test-Path -LiteralPath $backupPath)) {
                        Copy-Item -LiteralPath $TargetFile -Destination $backupPath
                        Write-Info "Backed up user-modified $CategoryLabel -> .user-backup: $DeployedRel"
                    } else {
                        Write-Info "Skipped backup (earlier .user-backup preserved): $DeployedRel"
                    }
                }
            }
        } catch {
            # Hash compute failed (permission denied, IO race, etc.). Fail
            # closed (Codex Recipe 1 #3): treat as collision and skip the
            # remove. -Force still proceeds (with no backup since we can't
            # confirm content differs) so a determined operator can recover.
            $script:obiCollisions += [pscustomobject]@{
                Category = "$CategoryLabel (hash-failure)"
                Target   = $TargetFile
                Source   = $sourcePath
            }
            Write-Info "Hash compare failed for ${DeployedRel}: $($_.Exception.Message) -- treating as collision"
            if (-not $Force) {
                return
            }
        }
    }

    if ($DryRun) {
        Write-Info "Would remove obi ${CategoryLabel}: $DeployedRel"
    } else {
        Remove-Item -LiteralPath $TargetFile -Force
        Write-Info "Removed obi ${CategoryLabel}: $DeployedRel"
    }
}

# Step 1: Cleanup stale artifacts (Issue #9). Collision-aware (#167); aborts
# (exit 2) when user-modified Obi files are detected without -Force. Reads
# $ClaudeTarget, $RepoRoot, and the source-dir path vars from caller scope.
function Invoke-ClaudeCleanup {
    param(
        [switch]$DryRun,
        [switch]$Force,
        [string]$DeployScriptPath  # used in the collision-gate re-run hint
    )

    Write-Step 'Cleaning stale deployment artifacts...'

    # Build obi-owned directory names for commands, agents, and skills.
    # Uses deployment manifest when available; falls back to repo enumeration.
    $manifestPath = Join-Path $ClaudeTarget '.obi\deployment-manifest.json'
    $obiOwnedSkills   = @()
    $obiOwnedCommands = @()
    $obiOwnedAgents   = @()

    # Skills installed by external tooling (not owned by this repo's deploy).
    # Old manifests on disk may still reference such skills, so the cleanup
    # below would unhelpfully delete the externally-installed copy. Add any
    # such skill names here to exclude them from cleanup. Empty by default.
    $externallyManagedSkills = @()

    # $obiSourceMap maps deployed-rel → source-rel. Used by collision detection
    # (167) to resolve the Obi source for each manifest-tracked target file.
    # Empty when manifest is missing — collision detection falls through to
    # legacy unconditional remove for that category. Fallback (repo-enum) sets
    # entries to $null since fallback names lack source-rel info.
    $obiSourceMap = @{}

    if (Test-Path $manifestPath) {
        $manifestData = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $manifestKeys = @($manifestData.mappings.PSObject.Properties.Name)
        foreach ($k in $manifestKeys) {
            $obiSourceMap[$k] = $manifestData.mappings.$k
        }
        $obiOwnedSkills   = @($manifestKeys | Where-Object { $_ -match '^skills/([^/]+)/' } | ForEach-Object { $Matches[1] } | Select-Object -Unique)
        $obiOwnedCommands = @($manifestKeys | Where-Object { $_ -match '^commands/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedAgents   = @($manifestKeys | Where-Object { $_ -match '^agents/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedDocs     = @($manifestKeys | Where-Object { $_ -match '^docs/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedHooks    = @($manifestKeys | Where-Object { $_ -match '^hooks/(.+)$' } | ForEach-Object { $Matches[1] })
        Write-Info "Loaded manifest: $($obiOwnedSkills.Count) skill dirs, $($obiOwnedCommands.Count) commands, $($obiOwnedAgents.Count) agents, $($obiOwnedDocs.Count) docs, $($obiOwnedHooks.Count) hook files"

        # Per-category fallback: if any category came up empty (e.g. v0.69.12
        # manifests pre-date the agents/ keys being written), fall back to
        # repo enumeration for THAT category. Otherwise first deploy after
        # the missing-keys point would silently skip cleanup for it. #147
        if ($obiOwnedSkills.Count -eq 0) {
            $obiOwnedSkills = Get-ObiSkillsFromRepo
            Write-Info "Manifest had 0 skills; using repo fallback: $($obiOwnedSkills.Count) skill dirs"
        }
        if ($obiOwnedCommands.Count -eq 0) {
            $obiOwnedCommands = Get-ObiCommandsFromRepo
            Write-Info "Manifest had 0 commands; using repo fallback: $($obiOwnedCommands.Count) commands"
        }
        if ($obiOwnedAgents.Count -eq 0) {
            $obiOwnedAgents = Get-ObiAgentsFromRepo
            Write-Info "Manifest had 0 agents; using repo fallback: $($obiOwnedAgents.Count) agents"
        }
        if ($obiOwnedDocs.Count -eq 0) {
            $obiOwnedDocs = Get-ObiDocsFromRepo
            Write-Info "Manifest had 0 docs; using repo fallback: $($obiOwnedDocs.Count) docs"
        }
        if ($obiOwnedHooks.Count -eq 0) {
            $obiOwnedHooks = Get-ObiHooksFromRepo
            Write-Info "Manifest had 0 hooks; using repo fallback: $($obiOwnedHooks.Count) hook files"
        }
    } else {
        # No manifest at all: enumerate every category from repo source
        $obiOwnedSkills   = Get-ObiSkillsFromRepo
        $obiOwnedCommands = Get-ObiCommandsFromRepo
        $obiOwnedAgents   = Get-ObiAgentsFromRepo
        $obiOwnedDocs     = Get-ObiDocsFromRepo
        $obiOwnedHooks    = Get-ObiHooksFromRepo
        Write-Info "No manifest found; using repo fallback: $($obiOwnedSkills.Count) skill dirs, $($obiOwnedCommands.Count) commands, $($obiOwnedAgents.Count) agents, $($obiOwnedDocs.Count) docs, $($obiOwnedHooks.Count) hook files"
    }

    # Drop externally-managed skills from the cleanup list regardless of source
    # (manifest or enumeration). Their source of truth lives outside this repo.
    $obiOwnedSkills = @($obiOwnedSkills | Where-Object { $_ -notin $externallyManagedSkills })

    # Targeted cleanup: remove only obi-owned items from commands/
    # Collision-aware via Remove-ObiOwnedFile (#167).
    $commandsPath = Join-Path $ClaudeTarget 'commands'
    if (Test-Path -LiteralPath $commandsPath) {
        foreach ($cmd in $obiOwnedCommands) {
            $cmdFile = Join-Path $commandsPath $cmd
            if (-not (Test-PathUnderRoot -CandidatePath $cmdFile -Root $commandsPath)) {
                Write-Warning "Skipping unsafe commands cleanup entry: $cmd"
                continue
            }
            if (Test-Path -LiteralPath $cmdFile) {
                Remove-ObiOwnedFile -TargetFile $cmdFile -DeployedRel "commands/$cmd" `
                    -CategoryLabel 'command' -SourceRel $obiSourceMap["commands/$cmd"] `
                    -RepoRoot $RepoRoot -DryRun:$DryRun -Force:$Force
            }
        }
    }

    # Targeted cleanup: remove only obi-owned items from agents/
    # Collision-aware via Remove-ObiOwnedFile (#167).
    $agentsPath = Join-Path $ClaudeTarget 'agents'
    if (Test-Path -LiteralPath $agentsPath) {
        foreach ($agt in $obiOwnedAgents) {
            $agtFile = Join-Path $agentsPath $agt
            if (-not (Test-PathUnderRoot -CandidatePath $agtFile -Root $agentsPath)) {
                Write-Warning "Skipping unsafe agents cleanup entry: $agt"
                continue
            }
            if (Test-Path -LiteralPath $agtFile) {
                Remove-ObiOwnedFile -TargetFile $agtFile -DeployedRel "agents/$agt" `
                    -CategoryLabel 'agent' -SourceRel $obiSourceMap["agents/$agt"] `
                    -RepoRoot $RepoRoot -DryRun:$DryRun -Force:$Force
            }
        }
    }

    # Targeted cleanup: remove only obi-owned skill directories.
    # Skills are whole-directory atomic Obi units (no per-file collision
    # detection — user-created skill dirs already escape Obi ownership
    # via the manifest). But the same wildcard + path-traversal safety
    # pattern applies (Codex Recipe 1 #2):
    #   - Test-PathUnderRoot rejects `..` traversal and absolute paths
    #     in malformed manifest entries.
    #   - -LiteralPath on Test-Path / Remove-Item disables wildcards so
    #     a corrupted key like `*` doesn't glob-match siblings.
    $skillsPath = Join-Path $ClaudeTarget 'skills'
    if (Test-Path -LiteralPath $skillsPath) {
        foreach ($skill in $obiOwnedSkills) {
            $skillDir = Join-Path $skillsPath $skill
            if (-not (Test-PathUnderRoot -CandidatePath $skillDir -Root $skillsPath)) {
                Write-Warning "Skipping unsafe skills cleanup entry: $skill (resolved outside $skillsPath)"
                continue
            }
            if (Test-Path -LiteralPath $skillDir) {
                if ($DryRun) { Write-Info "Would remove obi skill: $skill/" }
                else { Remove-Item -LiteralPath $skillDir -Recurse -Force; Write-Info "Removed obi skill: $skill/" }
            }
        }
    }

    # Targeted cleanup: remove only obi-owned files from docs/ (#160).
    # Previously wholesale-wiped, which clobbered any user-authored docs.
    # Collision-aware via Remove-ObiOwnedFile (#167).
    $docsPath = Join-Path $ClaudeTarget 'docs'
    if (Test-Path -LiteralPath $docsPath) {
        foreach ($doc in $obiOwnedDocs) {
            $docFile = Join-Path $docsPath $doc
            if (-not (Test-PathUnderRoot -CandidatePath $docFile -Root $docsPath)) {
                Write-Warning "Skipping unsafe docs cleanup entry: $doc (resolved outside $docsPath)"
                continue
            }
            if (Test-Path -LiteralPath $docFile) {
                Remove-ObiOwnedFile -TargetFile $docFile -DeployedRel "docs/$doc" `
                    -CategoryLabel 'doc' -SourceRel $obiSourceMap["docs/$doc"] `
                    -RepoRoot $RepoRoot -DryRun:$DryRun -Force:$Force
            }
        }
    }

    # Targeted cleanup: remove only obi-owned files from hooks/ (#160).
    # Collision-aware via Remove-ObiOwnedFile (#167).
    $hooksTarget = Join-Path $ClaudeTarget 'hooks'
    if (Test-Path -LiteralPath $hooksTarget) {
        foreach ($hook in $obiOwnedHooks) {
            $hookFile = Join-Path $hooksTarget $hook
            if (-not (Test-PathUnderRoot -CandidatePath $hookFile -Root $hooksTarget)) {
                Write-Warning "Skipping unsafe hooks cleanup entry: $hook (resolved outside $hooksTarget)"
                continue
            }
            if (Test-Path -LiteralPath $hookFile) {
                Remove-ObiOwnedFile -TargetFile $hookFile -DeployedRel "hooks/$hook" `
                    -CategoryLabel 'hook' -SourceRel $obiSourceMap["hooks/$hook"] `
                    -RepoRoot $RepoRoot -DryRun:$DryRun -Force:$Force
            }
        }
    }

    # tools/ is no longer deployed under $ClaudeTarget. The canonical path is
    # $ToolsTarget\tools\, set up by the deploy block further down. The orphan
    # tree at ~/.claude/tools/ is left in place intentionally — old plans /
    # scripts that hardcoded the old path will fail loudly rather than silently
    # running stale copies. Clean it manually if it bothers you.

    # Collision-detection gate (#167). After the 4 collision-aware loops,
    # check if any user-modified Obi files were detected. Default:
    # abort with a summary so user sees what would have been clobbered.
    # -Force: collisions were already backed up to .user-backup and
    # removed by Remove-ObiOwnedFile; proceed to deploy phase.
    if ($script:obiCollisions.Count -gt 0 -and -not $Force) {
        Write-Warning ''
        Write-Warning "[!] DEPLOYMENT BLOCKED: $($script:obiCollisions.Count) collision(s) detected"
        Write-Warning ''
        Write-Warning 'The following manifest-tracked files differ from their Obi source —'
        Write-Warning 'user-modified content. Re-running this deploy without -Force would'
        Write-Warning 'have clobbered them.'
        Write-Warning ''
        foreach ($c in $script:obiCollisions) {
            Write-Warning "  [$($c.Category)] $($c.Target)"
            Write-Warning "    source: $($c.Source)"
        }
        Write-Warning ''
        Write-Warning 'To proceed, re-run with -Force. Modified targets will be backed up'
        Write-Warning 'to <target>.user-backup before Obi overwrites them. Existing'
        Write-Warning '.user-backup siblings are never clobbered.'
        Write-Warning ''
        Write-Warning "  & '$DeployScriptPath' -Force"
        Write-Warning ''
        exit 2
    }

    Write-Check 'Cleanup complete'
}

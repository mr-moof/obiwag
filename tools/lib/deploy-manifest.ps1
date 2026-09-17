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

function Get-ObiSkillFilesFromRepo {
    return @(Get-ChildItem -Path $SkillsDir -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*' } |
        ForEach-Object { $_.FullName.Substring($SkillsDir.Length + 1).Replace('\', '/') })
}

function Resolve-ObiSourcePath {
    param(
        [string]$SourceRel,
        [string]$RepoRoot
    )

    if (-not $SourceRel) { return $null }
    if ([System.IO.Path]::IsPathRooted($SourceRel)) { return $SourceRel }

    $candidate = Join-Path $RepoRoot $SourceRel
    if (-not (Test-PathUnderRoot -CandidatePath $candidate -Root $RepoRoot)) {
        return $null
    }
    return $candidate
}

# Best-effort migration bridge for schema-1 manifests, which did not record
# deployed hashes. A working-tree source may have advanced while the installed
# target still equals the checked-in HEAD blob from the previous deployment.
# New schema-2 manifests do not depend on this Git fallback.
function Test-ObiTargetMatchesGitHead {
    param(
        [string]$TargetFile,
        [string]$SourceRel,
        [string]$RepoRoot
    )

    if (-not $SourceRel -or [System.IO.Path]::IsPathRooted($SourceRel)) { return $false }
    if ($SourceRel -match '(^|[\\/])\.\.([\\/]|$)') { return $false }

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { return $false }

    try {
        $normalizedSource = $SourceRel.Replace('\', '/')
        $headHash = @(& $git.Source -C $RepoRoot rev-parse --verify "HEAD:$normalizedSource" 2>$null)
        if ($LASTEXITCODE -ne 0 -or $headHash.Count -ne 1) { return $false }

        $targetHash = @(& $git.Source -C $RepoRoot hash-object -- $TargetFile 2>$null)
        if ($LASTEXITCODE -ne 0 -or $targetHash.Count -ne 1) { return $false }

        return ([string]$headHash[0]).Trim() -eq ([string]$targetHash[0]).Trim()
    } catch {
        return $false
    }
}

function Get-ObiOwnedFileCollision {
    param(
        [string]$TargetFile,
        [string]$DeployedRel,
        [string]$CategoryLabel,
        [string]$SourceRel,
        [string]$RepoRoot,
        [string]$DeployedHash
    )

    if (-not (Test-Path -LiteralPath $TargetFile -PathType Leaf)) { return $null }

    $sourcePath = Resolve-ObiSourcePath -SourceRel $SourceRel -RepoRoot $RepoRoot
    try {
        $targetHash = (Get-FileHash -LiteralPath $TargetFile -Algorithm SHA256 -ErrorAction Stop).Hash

        # The prior deployment hash is the authoritative ownership baseline.
        if ($DeployedHash -and $targetHash -eq $DeployedHash) { return $null }

        # A retry after a partially completed copy is also safe: the target may
        # already equal the current source even though the old manifest remains.
        if ($sourcePath -and (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
            $sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256 -ErrorAction Stop).Hash
            if ($targetHash -eq $sourceHash) { return $null }

            # Schema-1 migration: recognize the checked-in pre-edit source.
            if (-not $DeployedHash -and
                (Test-ObiTargetMatchesGitHead -TargetFile $TargetFile -SourceRel $SourceRel -RepoRoot $RepoRoot)) {
                return $null
            }
        }

        return [pscustomobject]@{
            Category = $CategoryLabel
            Target   = $TargetFile
            Source   = if ($sourcePath) { $sourcePath } else { $SourceRel }
            Reason   = if ($DeployedHash) { 'deployed-hash-mismatch' } else { 'legacy-baseline-unavailable' }
        }
    } catch {
        return [pscustomobject]@{
            Category = "$CategoryLabel (hash-failure)"
            Target   = $TargetFile
            Source   = if ($sourcePath) { $sourcePath } else { $SourceRel }
            Reason   = $_.Exception.Message
        }
    }
}

function Write-ClaudeDeploymentManifest {
    $deployedHashes = @{}

    foreach ($deployedRel in @($script:manifest.Keys | Sort-Object)) {
        # tools/* is deployed to OBI_HOME rather than ClaudeTarget and is not
        # part of Claude cleanup/collision ownership.
        if ($deployedRel -like 'tools/*') { continue }

        $targetFile = Join-Path $ClaudeTarget $deployedRel
        if (-not (Test-PathUnderRoot -CandidatePath $targetFile -Root $ClaudeTarget)) {
            Add-DeployFailure -Stage 'Manifest' -Detail "Unsafe deployed path: $deployedRel"
            continue
        }
        if (-not (Test-Path -LiteralPath $targetFile -PathType Leaf)) { continue }

        try {
            $deployedHashes[$deployedRel] = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256 -ErrorAction Stop).Hash
        } catch {
            Add-DeployFailure -Stage 'Manifest' -Detail "Hash failed for ${deployedRel}: $($_.Exception.Message)"
        }
    }

    if ($script:deployFailures.Count -gt 0) {
        Write-Info 'Skipped deployment manifest update because deployment failures are pending'
        return
    }

    $obiDir = Join-Path $ClaudeTarget '.obi'
    if (-not (Test-Path -LiteralPath $obiDir)) {
        New-Item -ItemType Directory -Path $obiDir -Force | Out-Null
    }
    $manifestObj = @{
        version = '2.0'
        deployed_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        mappings = $script:manifest
        deployed_hashes = $deployedHashes
    }
    $manifestPath = Join-Path $obiDir 'deployment-manifest.json'
    $manifestJson = $manifestObj | ConvertTo-Json -Depth 5
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)
    Write-Check "Deployment manifest written ($($script:manifest.Count) entries, $($deployedHashes.Count) hashes)"
}

# Collision-aware remove (#167). Collision classification is normally completed
# for the entire cleanup plan before this function mutates any file. Direct
# callers retain the same fail-closed behavior through the unchecked fallback.
# If the target has been user-modified:
#   - Records the collision in $script:obiCollisions for the post-cleanup
#     gate to read.
#   - If $Force: copies target to <target>.user-backup (only if
#     .user-backup doesn't already exist) AND removes the target.
#   - If not $Force: skips the remove (target preserved for user).
# If neither a deployed hash nor a resolvable source can prove ownership, the
# classifier fails closed and preserves the target unless -Force is explicit.
function Remove-ObiOwnedFile {
    param(
        [string]$TargetFile,
        [string]$DeployedRel,
        [string]$CategoryLabel,
        [string]$SourceRel,
        [string]$RepoRoot,
        [string]$DeployedHash,
        [switch]$DryRun,
        [switch]$Force,
        [switch]$CollisionChecked,
        [switch]$CollisionDetected,
        [switch]$KeepTarget
    )

    if (-not $CollisionChecked) {
        $collision = Get-ObiOwnedFileCollision -TargetFile $TargetFile -DeployedRel $DeployedRel `
            -CategoryLabel $CategoryLabel -SourceRel $SourceRel -RepoRoot $RepoRoot `
            -DeployedHash $DeployedHash
        $CollisionDetected = $null -ne $collision
        if ($CollisionDetected) { $script:obiCollisions += $collision }
    }

    if ($CollisionDetected) {
        if (-not $Force) { return }

        # -Force preserves the earliest user version before removal. DryRun
        # previews the same operation without writing.
        $backupPath = "$TargetFile.user-backup"
        if ($DryRun) {
            if (-not (Test-Path -LiteralPath $backupPath)) {
                Write-Info "Would back up user-modified $CategoryLabel -> .user-backup: $DeployedRel"
            } else {
                Write-Info "Would skip backup (earlier .user-backup preserved): $DeployedRel"
            }
        } else {
            if (-not (Test-Path -LiteralPath $backupPath)) {
                Copy-Item -LiteralPath $TargetFile -Destination $backupPath -ErrorAction Stop
                Write-Info "Backed up user-modified $CategoryLabel -> .user-backup: $DeployedRel"
            } else {
                Write-Info "Skipped backup (earlier .user-backup preserved): $DeployedRel"
            }
        }
    }

    # Current mappings are updated in place by Invoke-ClaudeDeploy. Retaining
    # them avoids a delete-recopy availability window and leaves the last-good
    # target present if a later copy fails. Only stale mappings are removed.
    # settings.json is always retained because its source is user-specific and
    # may legitimately be absent in another checkout/user context.
    if ($KeepTarget) { return }

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
    $obiOwnedSkills     = @()
    $obiOwnedSkillFiles = @()
    $obiOwnedCommands   = @()
    $obiOwnedAgents     = @()
    $obiOwnedDocs       = @()
    $obiOwnedHooks      = @()
    $obiOwnedOtherFiles = @()
    $script:obiCollisions = @()

    # Reserved for explicitly configured externally managed skills.
    $externallyManagedSkills = @()

    # Maps provide both the source location and the exact bytes written by the
    # prior successful deployment. Schema-1 manifests have only source paths;
    # Test-ObiTargetMatchesGitHead supplies a bounded migration bridge.
    $obiSourceMap = @{}
    $obiDeployedHashMap = @{}

    if (Test-Path -LiteralPath $manifestPath) {
        $manifestData = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $manifestProperties = @($manifestData.mappings.PSObject.Properties)
        $manifestKeys = @($manifestProperties.Name)
        foreach ($property in $manifestProperties) {
            $obiSourceMap[$property.Name] = [string]$property.Value
        }
        if ($manifestData.PSObject.Properties.Name -contains 'deployed_hashes' -and $manifestData.deployed_hashes) {
            foreach ($property in @($manifestData.deployed_hashes.PSObject.Properties)) {
                $obiDeployedHashMap[$property.Name] = [string]$property.Value
            }
        }
        $obiOwnedSkillFiles = @($manifestKeys | Where-Object { $_ -match '^skills/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedSkills   = @($obiOwnedSkillFiles | ForEach-Object { ($_ -split '/')[0] } | Select-Object -Unique)
        $obiOwnedCommands = @($manifestKeys | Where-Object { $_ -match '^commands/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedAgents   = @($manifestKeys | Where-Object { $_ -match '^agents/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedDocs     = @($manifestKeys | Where-Object { $_ -match '^docs/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedHooks    = @($manifestKeys | Where-Object { $_ -match '^hooks/(.+)$' } | ForEach-Object { $Matches[1] })
        $obiOwnedOtherFiles = @($manifestKeys | Where-Object {
            $_ -notmatch '^(commands|agents|skills|docs|hooks|tools)/'
        })
        Write-Info "Loaded manifest: $($obiOwnedSkills.Count) skill dirs, $($obiOwnedCommands.Count) commands, $($obiOwnedAgents.Count) agents, $($obiOwnedDocs.Count) docs, $($obiOwnedHooks.Count) hook files, $($obiOwnedOtherFiles.Count) other files"

        # Per-category fallback: if any category came up empty (e.g. v0.69.12
        # manifests pre-date the agents/ keys being written), fall back to
        # repo enumeration for THAT category. Otherwise first deploy after
        # the missing-keys point would silently skip cleanup for it. #147
        if ($obiOwnedSkills.Count -eq 0) {
            $obiOwnedSkills = Get-ObiSkillsFromRepo
            $obiOwnedSkillFiles = Get-ObiSkillFilesFromRepo
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
        $obiOwnedSkillFiles = Get-ObiSkillFilesFromRepo
        $obiOwnedCommands = Get-ObiCommandsFromRepo
        $obiOwnedAgents   = Get-ObiAgentsFromRepo
        $obiOwnedDocs     = Get-ObiDocsFromRepo
        $obiOwnedHooks    = Get-ObiHooksFromRepo
        Write-Info "No manifest found; using repo fallback: $($obiOwnedSkills.Count) skill dirs, $($obiOwnedCommands.Count) commands, $($obiOwnedAgents.Count) agents, $($obiOwnedDocs.Count) docs, $($obiOwnedHooks.Count) hook files"
    }

    # Drop externally-managed skills from cleanup regardless of whether the
    # ownership list came from a manifest or source enumeration.
    $obiOwnedSkills = @($obiOwnedSkills | Where-Object { $_ -notin $externallyManagedSkills })
    $obiOwnedSkillFiles = @($obiOwnedSkillFiles | Where-Object {
        (($_ -split '/')[0]) -notin $externallyManagedSkills
    })

    # Build the complete cleanup plan without mutating anything. The collision
    # gate below evaluates this whole list before the first Remove-Item, which
    # makes a blocked deploy non-destructive.
    $cleanupPlan = [System.Collections.Generic.List[object]]::new()
    $categories = @(
        @{ Items = $obiOwnedCommands;   Root = (Join-Path $ClaudeTarget 'commands'); Prefix = 'commands'; Label = 'command' },
        @{ Items = $obiOwnedAgents;     Root = (Join-Path $ClaudeTarget 'agents');   Prefix = 'agents';   Label = 'agent' },
        @{ Items = $obiOwnedSkillFiles; Root = (Join-Path $ClaudeTarget 'skills');   Prefix = 'skills';   Label = 'skill file' },
        @{ Items = $obiOwnedDocs;       Root = (Join-Path $ClaudeTarget 'docs');     Prefix = 'docs';     Label = 'doc' },
        @{ Items = $obiOwnedHooks;      Root = (Join-Path $ClaudeTarget 'hooks');    Prefix = 'hooks';    Label = 'hook' },
        @{ Items = $obiOwnedOtherFiles; Root = $ClaudeTarget;                        Prefix = '';         Label = 'managed file' }
    )

    foreach ($category in $categories) {
        foreach ($item in @($category.Items)) {
            $targetFile = Join-Path $category.Root $item
            if (-not (Test-PathUnderRoot -CandidatePath $targetFile -Root $category.Root)) {
                Write-Warning "Skipping unsafe $($category.Prefix) cleanup entry: $item"
                continue
            }
            if (-not (Test-Path -LiteralPath $targetFile -PathType Leaf)) { continue }

            $deployedRel = if ($category.Prefix) { "$($category.Prefix)/$item" } else { $item }
            $sourceRel = $obiSourceMap[$deployedRel]
            if (-not $sourceRel) {
                # Repo-enumeration fallbacks have deterministic source paths
                # for these three mirrored trees.
                if ($category.Prefix -eq 'skills') { $sourceRel = "skills/$item" }
                elseif ($category.Prefix -eq 'hooks') { $sourceRel = "hooks/$item" }
                elseif ($category.Prefix -eq 'docs') {
                    $sourceRel = if ($item -like 'policies/*') {
                        'policies/' + $item.Substring('policies/'.Length)
                    } else {
                        "docs/$item"
                    }
                }
            }

            $resolvedSource = Resolve-ObiSourcePath -SourceRel $sourceRel -RepoRoot $RepoRoot
            $keepTarget = ($deployedRel -eq 'settings.json') -or
                ($resolvedSource -and (Test-Path -LiteralPath $resolvedSource -PathType Leaf))

            $cleanupPlan.Add([pscustomobject]@{
                TargetFile    = $targetFile
                DeployedRel   = $deployedRel
                CategoryLabel = $category.Label
                SourceRel     = $sourceRel
                DeployedHash  = $obiDeployedHashMap[$deployedRel]
                KeepTarget    = $keepTarget
            })
        }
    }

    foreach ($entry in $cleanupPlan) {
        $collision = Get-ObiOwnedFileCollision -TargetFile $entry.TargetFile `
            -DeployedRel $entry.DeployedRel -CategoryLabel $entry.CategoryLabel `
            -SourceRel $entry.SourceRel -RepoRoot $RepoRoot -DeployedHash $entry.DeployedHash
        if ($null -ne $collision) { $script:obiCollisions += $collision }
    }

    # Shared tools are deployed to $ToolsTarget/tools/. Preserve unrelated legacy files.

    # Collision-detection gate (#167). This runs before all cleanup mutation.
    if ($script:obiCollisions.Count -gt 0 -and -not $Force) {
        Write-Warning ''
        Write-Warning "[!] DEPLOYMENT BLOCKED: $($script:obiCollisions.Count) collision(s) detected"
        Write-Warning ''
        Write-Warning 'The following manifest-tracked files match neither the prior deployed'
        Write-Warning 'bytes nor the current source. They may contain user modifications.'
        Write-Warning ''
        foreach ($c in $script:obiCollisions) {
            Write-Warning "  [$($c.Category)] $($c.Target)"
            Write-Warning "    source: $($c.Source)"
            Write-Warning "    reason: $($c.Reason)"
        }
        Write-Warning ''
        Write-Warning 'To proceed, re-run with -Force. Modified targets will be backed up'
        Write-Warning 'to <target>.user-backup before Obi overwrites them. Existing'
        Write-Warning '.user-backup siblings are never clobbered.'
        Write-Warning ''
        Write-Warning "  & '$DeployScriptPath' -Force"
        Write-Warning ''
        return $false
    }

    $collisionTargets = @{}
    foreach ($collision in $script:obiCollisions) {
        $collisionTargets[$collision.Target] = $true
    }
    foreach ($entry in $cleanupPlan) {
        Remove-ObiOwnedFile -TargetFile $entry.TargetFile -DeployedRel $entry.DeployedRel `
            -CategoryLabel $entry.CategoryLabel -SourceRel $entry.SourceRel `
            -RepoRoot $RepoRoot -DeployedHash $entry.DeployedHash -DryRun:$DryRun -Force:$Force `
            -CollisionChecked -CollisionDetected:($collisionTargets.ContainsKey($entry.TargetFile)) `
            -KeepTarget:$entry.KeepTarget
    }

    # Skill cleanup is file-granular so user-created files inside an Obi skill
    # survive. Prune only directories that became empty after managed files
    # were removed; .user-backup files intentionally keep their directory.
    if (-not $DryRun) {
        $skillsPath = Join-Path $ClaudeTarget 'skills'
        foreach ($skill in $obiOwnedSkills) {
            $skillDir = Join-Path $skillsPath $skill
            if (-not (Test-PathUnderRoot -CandidatePath $skillDir -Root $skillsPath)) { continue }
            if (-not (Test-Path -LiteralPath $skillDir -PathType Container)) { continue }

            $nestedDirs = @(Get-ChildItem -LiteralPath $skillDir -Directory -Recurse -ErrorAction SilentlyContinue |
                Sort-Object { $_.FullName.Length } -Descending)
            foreach ($nestedDir in $nestedDirs) {
                if (@(Get-ChildItem -LiteralPath $nestedDir.FullName -Force -ErrorAction SilentlyContinue).Count -eq 0) {
                    Remove-Item -LiteralPath $nestedDir.FullName -Force
                }
            }
            if (@(Get-ChildItem -LiteralPath $skillDir -Force -ErrorAction SilentlyContinue).Count -eq 0) {
                Remove-Item -LiteralPath $skillDir -Force
                Write-Info "Removed empty obi skill: $skill/"
            }
        }
    }

    Write-Check 'Cleanup complete'
    return $true
}

<#
.SYNOPSIS
    Removes only Obi-deployed files, leaving user-authored content intact.

.DESCRIPTION
    Reads the deployment manifests at:
      - ~/.claude/.obi/deployment-manifest.json (Claude)
      - ~/.codex/.obi/deployment-manifest.json (Codex)
    and removes the files listed there, plus optionally restores the
    pre-Obi ~/.claude/settings.json from settings.json.backup.

    Safety model:
    - Every manifest entry passes a root-containment guard before delete.
      Manifest values that resolve outside an allow-listed root (e.g.
      `../../etc/passwd`, absolute paths to system locations) are
      skipped with a warning.
    - <file>.user-backup siblings are NEVER removed by uninstall. They
      represent user-modified content preserved during a prior -Force
      deploy (167).
    - settings.json.backup is only restored when its SHA256 still matches
      the Obi-deployed source (i.e. user hasn't edited the deployed
      settings.json since Obi wrote it). If they differ, the backup is
      preserved and the user is told to restore manually if they want.

    -DryRun previews everything without changes.
    -RestoreUserBackups opts in to renaming surviving <file>.user-backup
    siblings back to their original filenames after the Obi-deployed
    file is removed (useful when the user wants their pre-collision
    content restored as the active file).

.PARAMETER DryRun
    Preview what would be removed/restored without making changes.

.PARAMETER RestoreUserBackups
    After removing manifest files, rename any surviving <file>.user-backup
    back to <file>. Default off -- the .user-backup files are preserved
    in place, and the user can restore them manually.

.PARAMETER ClaudeOnly
    Only process the Claude manifest. Skip Codex.

.PARAMETER CodexOnly
    Only process the Codex manifest. Skip Claude.

.EXAMPLE
    .\tools\uninstall.ps1 -DryRun
    # Preview the uninstall without changes

.EXAMPLE
    .\tools\uninstall.ps1
    # Remove all Obi-deployed files; restore settings.json from
    # .backup if its hash still matches the Obi source; preserve any
    # .user-backup siblings in place.

.EXAMPLE
    .\tools\uninstall.ps1 -RestoreUserBackups
    # Same as above, but also rename surviving .user-backup files back
    # to their original filenames.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$RestoreUserBackups,
    [switch]$ClaudeOnly,
    [switch]$CodexOnly
)

$ErrorActionPreference = 'Stop'

function Write-Step    { param([string]$Msg) Write-Host ""; Write-Host "  -> $Msg" -ForegroundColor Cyan }
function Write-Info    { param([string]$Msg) Write-Host "    $Msg" }
function Write-Check   { param([string]$Msg) Write-Host "  [OK]   $Msg" -ForegroundColor Green }
function Write-Skip    { param([string]$Msg) Write-Host "  [SKIP] $Msg" -ForegroundColor Yellow }
function Write-Problem { param([string]$Msg) Write-Warning "  [WARN] $Msg" }

# Root-containment guard. Resolves the candidate path and asserts it
# stays under one of the allow-listed roots. Defends against corrupt
# manifest entries with `..` traversal or absolute paths to outside
# locations. Mirrors deploy.ps1's Test-PathUnderRoot (160/167).
function Test-PathUnderAnyRoot {
    param([string]$CandidatePath, [string[]]$AllowedRoots)
    try {
        $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
        foreach ($root in $AllowedRoots) {
            $resolvedRoot = [System.IO.Path]::GetFullPath($root)
            if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
                $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
            }
            if ($resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
        return $false
    } catch {
        return $false
    }
}

# Resolve a manifest's deployed-rel key to an absolute filesystem path.
# Claude manifest:
#   - root-level keys (no `/`): under $ClaudeTarget (CLAUDE.md, statusline-command.ps1, settings.json)
#   - commands/agents/skills/docs/hooks/X: under $ClaudeTarget\<category>\X
#   - tools/X: under $env:OBI_HOME\tools\X (tools carve-out)
# Codex manifest:
#   - absolute path key (starts with drive letter): use verbatim
#   - skills/X: under $CodexTarget\skills\X
#   - root-level (AGENTS.md, .codex/hooks.json): see special-case table
function Resolve-ManifestKeyToPath {
    param(
        [string]$ManifestKey,
        [string]$Platform,            # 'claude' or 'codex'
        [string]$ClaudeTarget,
        [string]$CodexTarget,
        [string]$ToolsTarget,
        [string]$RepoRoot
    )

    if ([System.IO.Path]::IsPathRooted($ManifestKey)) {
        return $ManifestKey  # already absolute (Codex hooks/tools after f51c340)
    }

    $normalizedKey = $ManifestKey.Replace('/', [System.IO.Path]::DirectorySeparatorChar)

    if ($Platform -eq 'claude') {
        if ($ManifestKey -like 'tools/*') {
            # Strip the 'tools/' prefix and join under ToolsTarget\tools\
            $rel = $ManifestKey.Substring('tools/'.Length).Replace('/', [System.IO.Path]::DirectorySeparatorChar)
            return Join-Path (Join-Path $ToolsTarget 'tools') $rel
        }
        return Join-Path $ClaudeTarget $normalizedKey
    } else {  # codex
        # Codex root-level entries: AGENTS.md goes to repo root (.codex carve-out)
        # but most Codex absolute-key entries already cover hooks/tools paths.
        # Relative keys (skills/) go under CodexTarget.
        if ($ManifestKey -eq 'AGENTS.md') {
            return Join-Path $RepoRoot 'AGENTS.md'
        }
        if ($ManifestKey -like '.codex/*') {
            return Join-Path $RepoRoot $normalizedKey
        }
        return Join-Path $CodexTarget $normalizedKey
    }
}

# Determine the allow-listed roots for a platform's manifest. Any
# resolved manifest path must fall under one of these before being
# considered for removal.
function Get-AllowedRoots {
    param(
        [string]$Platform,
        [string]$ClaudeTarget,
        [string]$CodexTarget,
        [string]$ToolsTarget,
        [string]$RepoRoot
    )
    if ($Platform -eq 'claude') {
        return @($ClaudeTarget, (Join-Path $ToolsTarget 'tools'), (Join-Path $ToolsTarget 'hooks'))
    } else {
        # Narrow the Codex repo-root admittance (Codex Recipe 1 #4).
        # Earlier draft admitted the entire $RepoRoot for the AGENTS.md
        # carve-out, which would have let any absolute manifest key
        # under the checkout (e.g. corrupted ./users/.../*.py) pass
        # containment. Restrict to the specific Codex carve-out paths:
        # <repo>/AGENTS.md and <repo>/.codex/. The Get-AllowedRoots check
        # already does StartsWith comparison, so listing AGENTS.md
        # parent (RepoRoot) would over-admit; instead use the parent of
        # the .codex/ subdir AS its own allow-listed root since the only
        # file under it is hooks.json. AGENTS.md is special-cased: it's
        # the only allowed direct child of RepoRoot, so we use exact-
        # path comparison rather than StartsWith.
        return @(
            $CodexTarget,
            (Join-Path $ToolsTarget 'tools'),
            (Join-Path $ToolsTarget 'hooks'),
            (Join-Path $RepoRoot '.codex')
            # AGENTS.md is handled via exact-path check in the loop below
            # (it's a single allowed file, not a root prefix).
        )
    }
}

# Codex-only exact-path allowlist: paths that are NOT under any
# directory-prefix allowed-root but are explicitly permitted single
# files. Currently just <repo>/AGENTS.md. Used as an OR with the
# Get-AllowedRoots prefix check.
function Test-IsExactCodexAllowed {
    param([string]$CandidatePath, [string]$RepoRoot)
    try {
        $resolved = [System.IO.Path]::GetFullPath($CandidatePath)
        $agentsMd = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot 'AGENTS.md'))
        return $resolved.Equals($agentsMd, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

# Sentinel for an external manifest source (e.g. a command file deployed
# from a sibling repo via an absolute source path). When this is the source-rel,
# we still own removing the deployed copy, but we don't try to
# hash-compare against the original source for the settings.json restore.
function Test-IsAbsoluteSourceRel {
    param([string]$SourceRel)
    return ([System.IO.Path]::IsPathRooted($SourceRel))
}

# ============================================================
# Resolve roots
# ============================================================
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
$ClaudeTarget = Join-Path $env:USERPROFILE '.claude'
$CodexTarget  = Join-Path $env:USERPROFILE '.codex'
$ToolsTarget  = if ($env:OBI_HOME) { $env:OBI_HOME } else { 'C:\src\obi-tools' }

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Obi Wag Uninstall" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Info "Claude target: $ClaudeTarget"
Write-Info "Codex target:  $CodexTarget"
Write-Info "Tools target:  $ToolsTarget"
if ($DryRun) {
    Write-Host ""
    Write-Host "  *** DRY RUN -- no changes will be made ***" -ForegroundColor Yellow
}

$removed = 0
$skipped = 0
$preservedUserBackups = 0
$unsafeSkipped = 0

# ============================================================
# Process Claude manifest
# ============================================================
if (-not $CodexOnly) {
    Write-Step 'Reading Claude deployment manifest...'
    $claudeManifestPath = Join-Path $ClaudeTarget '.obi\deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $claudeManifestPath)) {
        Write-Skip "No Claude manifest at $claudeManifestPath"
    } else {
        $claudeData = Get-Content -LiteralPath $claudeManifestPath -Raw | ConvertFrom-Json
        $claudeKeys = @($claudeData.mappings.PSObject.Properties.Name)
        Write-Info "Loaded Claude manifest: $($claudeKeys.Count) entries"

        $allowedClaudeRoots = Get-AllowedRoots -Platform 'claude' `
            -ClaudeTarget $ClaudeTarget -CodexTarget $CodexTarget `
            -ToolsTarget $ToolsTarget -RepoRoot $RepoRoot

        foreach ($key in $claudeKeys) {
            $targetPath = Resolve-ManifestKeyToPath -ManifestKey $key -Platform 'claude' `
                -ClaudeTarget $ClaudeTarget -CodexTarget $CodexTarget `
                -ToolsTarget $ToolsTarget -RepoRoot $RepoRoot

            if (-not (Test-PathUnderAnyRoot -CandidatePath $targetPath -AllowedRoots $allowedClaudeRoots)) {
                Write-Problem "Skipping unsafe entry: $key -> $targetPath (resolved outside allowed roots)"
                $unsafeSkipped++
                continue
            }

            # Special-case settings.json: handled separately with hash-guard restore.
            if ($key -eq 'settings.json') { continue }

            $userBackupPath = "$targetPath.user-backup"
            $hasUserBackup = Test-Path -LiteralPath $userBackupPath
            $targetExists = Test-Path -LiteralPath $targetPath

            if (-not $targetExists -and -not $hasUserBackup) {
                continue  # nothing to do -- both already gone
            }

            # Handle the "target already removed by an earlier uninstall, but
            # a .user-backup sibling lingers" case (Codex Recipe 1 #5).
            # Without this, -RestoreUserBackups silently strands orphan
            # backups when the user re-runs uninstall.
            if (-not $targetExists -and $hasUserBackup) {
                if ($RestoreUserBackups) {
                    if ($DryRun) {
                        Write-Info "Would restore orphan .user-backup -> $targetPath"
                    } else {
                        Move-Item -LiteralPath $userBackupPath -Destination $targetPath
                        Write-Info "Restored orphan .user-backup -> $targetPath"
                    }
                } else {
                    Write-Info "Preserved orphan .user-backup: $userBackupPath (use -RestoreUserBackups)"
                    $preservedUserBackups++
                }
                continue
            }

            if ($DryRun) {
                Write-Info "Would remove: $targetPath"
                if ($hasUserBackup) {
                    if ($RestoreUserBackups) {
                        Write-Info "  Would restore .user-backup -> original filename"
                    } else {
                        Write-Info "  Preserving .user-backup sibling"
                    }
                }
            } else {
                Remove-Item -LiteralPath $targetPath -Force
                Write-Info "Removed: $targetPath"
                if ($hasUserBackup) {
                    if ($RestoreUserBackups) {
                        Move-Item -LiteralPath $userBackupPath -Destination $targetPath
                        Write-Info "  Restored .user-backup -> $targetPath"
                    } else {
                        Write-Info "  Preserved $userBackupPath (use -RestoreUserBackups to restore)"
                        $preservedUserBackups++
                    }
                }
            }
            $removed++
        }
    }
}

# ============================================================
# Process Codex manifest
# ============================================================
if (-not $ClaudeOnly) {
    Write-Step 'Reading Codex deployment manifest...'
    $codexManifestPath = Join-Path $CodexTarget '.obi\deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $codexManifestPath)) {
        Write-Skip "No Codex manifest at $codexManifestPath"
    } else {
        $codexData = Get-Content -LiteralPath $codexManifestPath -Raw | ConvertFrom-Json
        $codexKeys = @($codexData.mappings.PSObject.Properties.Name)
        Write-Info "Loaded Codex manifest: $($codexKeys.Count) entries"

        $allowedCodexRoots = Get-AllowedRoots -Platform 'codex' `
            -ClaudeTarget $ClaudeTarget -CodexTarget $CodexTarget `
            -ToolsTarget $ToolsTarget -RepoRoot $RepoRoot

        foreach ($key in $codexKeys) {
            $targetPath = Resolve-ManifestKeyToPath -ManifestKey $key -Platform 'codex' `
                -ClaudeTarget $ClaudeTarget -CodexTarget $CodexTarget `
                -ToolsTarget $ToolsTarget -RepoRoot $RepoRoot

            $isUnderPrefix = Test-PathUnderAnyRoot -CandidatePath $targetPath -AllowedRoots $allowedCodexRoots
            $isExactAllowed = Test-IsExactCodexAllowed -CandidatePath $targetPath -RepoRoot $RepoRoot
            if (-not ($isUnderPrefix -or $isExactAllowed)) {
                Write-Problem "Skipping unsafe entry: $key -> $targetPath (resolved outside allowed roots)"
                $unsafeSkipped++
                continue
            }

            $userBackupPath = "$targetPath.user-backup"
            $hasUserBackup = Test-Path -LiteralPath $userBackupPath
            $targetExists = Test-Path -LiteralPath $targetPath

            if (-not $targetExists -and -not $hasUserBackup) { continue }

            # Same orphan-backup handling as Claude loop (Codex Recipe 1 #5).
            if (-not $targetExists -and $hasUserBackup) {
                if ($RestoreUserBackups) {
                    if ($DryRun) {
                        Write-Info "Would restore orphan .user-backup -> $targetPath"
                    } else {
                        Move-Item -LiteralPath $userBackupPath -Destination $targetPath
                        Write-Info "Restored orphan .user-backup -> $targetPath"
                    }
                } else {
                    Write-Info "Preserved orphan .user-backup: $userBackupPath (use -RestoreUserBackups)"
                    $preservedUserBackups++
                }
                continue
            }

            if ($DryRun) {
                Write-Info "Would remove: $targetPath"
                if ($hasUserBackup) {
                    if ($RestoreUserBackups) {
                        Write-Info "  Would restore .user-backup -> original filename"
                    } else {
                        Write-Info "  Preserving .user-backup sibling"
                    }
                }
            } else {
                Remove-Item -LiteralPath $targetPath -Force
                Write-Info "Removed: $targetPath"
                if ($hasUserBackup) {
                    if ($RestoreUserBackups) {
                        Move-Item -LiteralPath $userBackupPath -Destination $targetPath
                        Write-Info "  Restored .user-backup -> $targetPath"
                    } else {
                        Write-Info "  Preserved $userBackupPath (use -RestoreUserBackups to restore)"
                        $preservedUserBackups++
                    }
                }
            }
            $removed++
        }
    }
}

# ============================================================
# settings.json restore (hash-guarded)
# ============================================================
if (-not $CodexOnly) {
    Write-Step 'Considering settings.json restore...'
    $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
    $settingsBackup = Join-Path $ClaudeTarget 'settings.json.backup'

    if (-not (Test-Path -LiteralPath $settingsBackup)) {
        Write-Skip 'No settings.json.backup -- nothing to restore.'
    } elseif (-not (Test-Path -LiteralPath $settingsTarget)) {
        # Deployed settings.json gone (uninstall might already have removed it).
        # Move backup back into place verbatim.
        if ($DryRun) {
            Write-Info "Would restore: $settingsBackup -> $settingsTarget"
        } else {
            Move-Item -LiteralPath $settingsBackup -Destination $settingsTarget
            Write-Check "Restored settings.json from .backup (no current target -- pure restore)"
        }
    } else {
        # Hash-guard: only restore if current settings.json matches the
        # Obi-deployed source. If they differ, the user has edited the
        # deployed file post-deploy and replacing it risks losing those
        # edits -- preserve the backup and tell them to restore manually.
        $deployedSource = $null
        try {
            $claudeManifestPath = Join-Path $ClaudeTarget '.obi\deployment-manifest.json'
            if (Test-Path -LiteralPath $claudeManifestPath) {
                $claudeData = Get-Content -LiteralPath $claudeManifestPath -Raw | ConvertFrom-Json
                $settingsSourceRel = $claudeData.mappings.'settings.json'
                if ($settingsSourceRel) {
                    if ([System.IO.Path]::IsPathRooted($settingsSourceRel)) {
                        $deployedSource = $settingsSourceRel
                    } else {
                        $deployedSource = Join-Path $RepoRoot $settingsSourceRel.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
                    }
                }
            }
        } catch {
            Write-Problem "Couldn't read settings.json source from manifest: $($_.Exception.Message)"
        }

        $shouldRestore = $false
        if ($deployedSource -and (Test-Path -LiteralPath $deployedSource)) {
            try {
                $currentHash = (Get-FileHash -LiteralPath $settingsTarget -Algorithm SHA256).Hash
                $obiSourceHash = (Get-FileHash -LiteralPath $deployedSource -Algorithm SHA256).Hash
                if ($currentHash -eq $obiSourceHash) {
                    $shouldRestore = $true
                } else {
                    Write-Skip "settings.json was edited post-deploy (hash mismatch with Obi source)."
                    Write-Info  "  Backup preserved at: $settingsBackup"
                    Write-Info  "  Restore manually with: Move-Item -LiteralPath '$settingsBackup' -Destination '$settingsTarget'"
                    $preservedUserBackups++
                }
            } catch {
                Write-Problem "Hash compare failed; preserving backup: $($_.Exception.Message)"
            }
        } else {
            Write-Skip "Couldn't resolve Obi source for settings.json; preserving backup."
        }

        if ($shouldRestore) {
            if ($DryRun) {
                Write-Info "Would restore: $settingsBackup -> $settingsTarget (hash match confirmed)"
            } else {
                Remove-Item -LiteralPath $settingsTarget -Force
                Move-Item -LiteralPath $settingsBackup -Destination $settingsTarget
                Write-Check "Restored settings.json from .backup (hash matched Obi source)"
            }
        }
    }
}

# ============================================================
# Summary
# ============================================================
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Uninstall Summary" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
if ($DryRun) {
    Write-Info "Mode: DryRun (no changes made)"
} else {
    Write-Info "Mode: Live"
}
Write-Info "Files removed:              $removed"
Write-Info ".user-backup files preserved: $preservedUserBackups"
Write-Info "Unsafe manifest entries skipped: $unsafeSkipped"
Write-Host ""
if (-not $RestoreUserBackups -and $preservedUserBackups -gt 0 -and -not $DryRun) {
    Write-Info "To restore preserved .user-backup files (rename them back to"
    Write-Info "their original filenames), re-run: tools/uninstall.ps1 -RestoreUserBackups"
    Write-Host ""
}

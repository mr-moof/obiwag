<#
.SYNOPSIS
    Step 3.5 — Codex deployment for the Obi Wag deploy pipeline (OPT-12, #186).

.DESCRIPTION
    Extracted from deploy.ps1. Dot-sourced into deploy.ps1's scope (after
    lib/common.ps1 and lib/deploy-common.ps1), so it sees the source/target
    path variables and the $script:deployFailures accumulator.

    Depends on: lib/common.ps1 (Write-*), lib/deploy-common.ps1
    (Copy-SingleFile, Copy-DirectoryContents, Add-DeployFailure).

    Provides:
      - Invoke-CodexDeploy : deploy AGENTS.md, project hooks.json, runtime hooks,
        skills, and shared tooling for Codex; set OBI_HOME / OBIWAG_SOURCE; write
        the Codex deployment manifest; run validate-codex.py.
#>

# Deploy all Codex configuration. Reads $PlatformsDir, $RepoRoot, $HooksDir,
# $SkillsDir, $ScriptDir, $ToolsTarget, $CodexTarget from the deploy.ps1 caller
# scope. The Codex manifest is function-local ($codexManifest).
function Invoke-CodexDeploy {
    param(
        [switch]$DryRun
    )

    Write-Step 'Deploying Codex configuration...'

    $codexSource = Join-Path $PlatformsDir 'codex'
    $codexManifest = @{}

    function Add-CodexManifestEntry {
        param([string]$DeployedRel, [string]$SourceRel)
        $codexManifest[$DeployedRel.Replace('\', '/')] = $SourceRel.Replace('\', '/')
    }

    function Add-CodexManifestBulk {
        param([string]$SourceDir, [string]$SourcePrefix, [string]$DeployedPrefix)
        if (-not (Test-Path $SourceDir)) { return }
        Get-ChildItem -Path $SourceDir -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
            $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*'
        } | ForEach-Object {
            $rel = $_.FullName.Substring($SourceDir.Length + 1)
            Add-CodexManifestEntry "$DeployedPrefix/$rel" "$SourcePrefix/$rel"
        }
    }

    if (-not (Test-Path $codexSource)) {
        Write-Problem "Codex platform source not found: $codexSource"
        Add-DeployFailure -Stage 'Codex' -Detail "Missing platform source: $codexSource"
    } else {
        $codexProjectDir = Join-Path $RepoRoot '.codex'
        if (-not $DryRun -and -not (Test-Path $codexProjectDir)) {
            New-Item -ItemType Directory -Path $codexProjectDir -Force | Out-Null
        }

        # Codex loads project instructions from AGENTS.md.
        $codexAgentsSource = Join-Path $codexSource 'AGENTS.md'
        $codexAgentsTarget = Join-Path $RepoRoot 'AGENTS.md'
        Copy-SingleFile -Source $codexAgentsSource -Destination $codexAgentsTarget -DryRun:$DryRun | Out-Null
        Add-CodexManifestEntry 'AGENTS.md' 'platforms/codex/AGENTS.md'

        # Project-scoped Codex hooks.
        $codexHooksSource = Join-Path $codexSource 'hooks.json'
        $codexHooksTarget = Join-Path $codexProjectDir 'hooks.json'
        Copy-SingleFile -Source $codexHooksSource -Destination $codexHooksTarget -DryRun:$DryRun | Out-Null
        Add-CodexManifestEntry '.codex/hooks.json' 'platforms/codex/hooks.json'

        # Runtime hook scripts live under OBI_HOME, not the user profile.
        if (Test-Path $HooksDir) {
            $codexHooksRuntime = Join-Path $ToolsTarget 'hooks'
            Copy-DirectoryContents -Source $HooksDir -Destination $codexHooksRuntime -DryRun:$DryRun
            Add-CodexManifestBulk $HooksDir 'hooks' $codexHooksRuntime
            Write-Info "Deployed Codex hooks -> $codexHooksRuntime"
        }

        # Reusable capabilities use Codex's native skills directory.
        if (Test-Path $SkillsDir) {
            $codexSkillsTarget = Join-Path $CodexTarget 'skills'
            Copy-DirectoryContents -Source $SkillsDir -Destination $codexSkillsTarget -DryRun:$DryRun
            Add-CodexManifestBulk $SkillsDir 'skills' 'skills'
            Write-Info "Deployed Codex skills -> $codexSkillsTarget"
        }

        # Keep shared PowerShell tooling in the CB-trusted OBI_HOME tree.
        if (Test-Path $ScriptDir) {
            $toolsDeployDest = Join-Path $ToolsTarget 'tools'
            Copy-DirectoryContents -Source $ScriptDir -Destination $toolsDeployDest -DryRun:$DryRun
            Add-CodexManifestBulk $ScriptDir 'tools' $toolsDeployDest
            Write-Info "Deployed tools/ -> $toolsDeployDest"
        }

        if (-not $DryRun) {
            $existingObiHome = [System.Environment]::GetEnvironmentVariable('OBI_HOME', 'User')
            if ($existingObiHome -ne $ToolsTarget) {
                [System.Environment]::SetEnvironmentVariable('OBI_HOME', $ToolsTarget, 'User')
                Write-Info "Set OBI_HOME=$ToolsTarget (User scope) - restart Codex to pick it up"
            }
            $env:OBI_HOME = $ToolsTarget

            $existingSource = [System.Environment]::GetEnvironmentVariable('OBIWAG_SOURCE', 'User')
            if ($existingSource -ne $RepoRoot) {
                [System.Environment]::SetEnvironmentVariable('OBIWAG_SOURCE', $RepoRoot, 'User')
                Write-Info "Set OBIWAG_SOURCE=$RepoRoot (User scope) - restart Codex to pick it up"
            }
            $env:OBIWAG_SOURCE = $RepoRoot

            $codexObiDir = Join-Path $CodexTarget '.obi'
            if (-not (Test-Path $codexObiDir)) {
                New-Item -ItemType Directory -Path $codexObiDir -Force | Out-Null
            }
            $manifestObj = @{
                version = '1.0'
                deployed_at = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ')
                mappings = $codexManifest
            }
            $manifestPath = Join-Path $codexObiDir 'deployment-manifest.json'
            $manifestJson = $manifestObj | ConvertTo-Json -Depth 3
            $utf8NoBom = New-Object System.Text.UTF8Encoding $false
            [System.IO.File]::WriteAllText($manifestPath, $manifestJson, $utf8NoBom)
            Write-Check "Codex deployment manifest written ($($codexManifest.Count) entries)"

            python (Join-Path $codexSource 'validate-codex.py')
        }

        Write-Check 'Codex deployment complete'
        Write-Info "Target: $CodexTarget"
    }
}

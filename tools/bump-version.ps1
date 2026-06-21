<#
.SYNOPSIS
    Bumps Obi Wag version across all versioned files.

.DESCRIPTION
    Reads the new version, updates the files that contain version references,
    and optionally commits the result. Uses tools/version.yaml as the source of truth.

    Note: hooks/core/version.py reads CURRENT_VERSION dynamically from
    version.yaml at import time, so it is intentionally NOT in the target
    list — bumping version.yaml is enough to update the runtime value.

.PARAMETER Version
    The new version string (e.g., "0.56").

.PARAMETER DryRun
    Show what would change without modifying any files.

.PARAMETER Commit
    Stage and commit the version bump after updating files.

.EXAMPLE
    .\tools\bump-version.ps1 -Version 0.56
    # Bump all files to 0.56

.EXAMPLE
    .\tools\bump-version.ps1 -Version 0.56 -DryRun
    # Preview changes without writing
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Version,

    [switch]$DryRun,
    [switch]$Commit
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$Today     = Get-Date -Format 'yyyy-MM-dd'

# Validate version format
if ($Version -notmatch '^\d+\.\d+(\.\d+)?$') {
    Write-Error "Invalid version format: '$Version'. Expected format like '0.56' or '1.0.0'."
    exit 1
}

# Read current version from version.yaml
$versionYaml = Join-Path $RepoRoot 'tools\version.yaml'
$yamlContent = Get-Content $versionYaml -Raw
if ($yamlContent -match 'version:\s*"([^"]+)"') {
    $OldVersion = $Matches[1]
} else {
    Write-Error "Could not read current version from $versionYaml"
    exit 1
}

if ($OldVersion -eq $Version) {
    Write-Warning "Version is already $Version. Nothing to do."
    exit 0
}

Write-Host ''
Write-Host "Bumping Obi Wag: $OldVersion -> $Version" -ForegroundColor Cyan
Write-Host ''

# Define all files and their replacement patterns
# Each entry: relative path, regex to match, replacement string
$targets = @(
    @{
        File    = 'tools\version.yaml'
        Pattern = '(?<=version:\s*")' + [regex]::Escape($OldVersion) + '(?=")'
        Replace = $Version
        Label   = 'version.yaml (version)'
    }
    @{
        File    = 'tools\version.yaml'
        Pattern = '(?<=last_updated:\s*")[\d-]+(?=")'
        Replace = $Today
        Label   = 'version.yaml (last_updated)'
    }
    # CLAUDE.md targets were removed in 0.69.34: the docs compact in ad0f5e7
    # stripped the `**Version:** X | **Last Updated:** Y` header line from
    # CLAUDE.md, and that's intentional — CLAUDE.md is meant to stay lean and
    # let tools/version.yaml be the single source of truth. Keeping the
    # targets here caused every bump to emit two [MISS] warnings + a final
    # non-zero exit ("Version bump incomplete"), which masked real failures.
    @{
        File    = 'README.md'
        Pattern = '(?<=\*\*v)' + [regex]::Escape($OldVersion) + '(?=\*\*)'
        Replace = $Version
        Label   = 'README.md'
    }
    @{
        File    = 'docs\hooks-architecture.md'
        Pattern = '(?<=\*\*Version:\*\*\s*)' + [regex]::Escape($OldVersion)
        Replace = $Version
        Label   = 'hooks-architecture.md (version)'
    }
    @{
        File    = 'docs\hooks-architecture.md'
        Pattern = '(?<=\*\*Last Updated:\*\*\s*)[\d-]+(?=\s*\|)'
        Replace = $Today
        Label   = 'hooks-architecture.md (date)'
    }
)

$success = 0
$failed  = 0

foreach ($target in $targets) {
    $filePath = Join-Path $RepoRoot $target.File

    if (-not (Test-Path $filePath)) {
        Write-Warning "  [SKIP] $($target.Label) - file not found: $($target.File)"
        $failed++
        continue
    }

    $content = Get-Content $filePath -Raw

    if ($content -match $target.Pattern) {
        if ($DryRun) {
            Write-Host "  [DRY] $($target.Label): would replace -> $($target.Replace)" -ForegroundColor Yellow
        } else {
            $newContent = $content -replace $target.Pattern, $target.Replace
            Set-Content -Path $filePath -Value $newContent -NoNewline
            Write-Host "  [OK]  $($target.Label)" -ForegroundColor Green
        }
        $success++
    } else {
        Write-Warning "  [MISS] $($target.Label) - pattern not matched in $($target.File)"
        $failed++
    }
}

Write-Host ''

if ($failed -gt 0) {
    Write-Warning "$failed target(s) failed. Review warnings above."
    if (-not $DryRun) {
        Write-Error "Version bump incomplete. Not all files were updated."
        exit 1
    }
}

Write-Host "$success target(s) updated successfully." -ForegroundColor Green

# Prepend a stub section to CHANGELOG.md for the new version.
# The full release history lives in CHANGELOG.md (split out of version.yaml in
# issue #182). On bump we insert a new "## <version> (<date>)" section at the
# top of the entries so the release author fills in the prose; version.yaml
# stays slim.
$changelog = Join-Path $RepoRoot 'CHANGELOG.md'
$changelogStub = "## $Version ($Today)`n`nTODO: describe this release.`n"

if (-not (Test-Path $changelog)) {
    Write-Warning "  [SKIP] CHANGELOG.md - file not found: CHANGELOG.md"
} elseif ($DryRun) {
    Write-Host "  [DRY] CHANGELOG.md: would prepend section -> ## $Version ($Today)" -ForegroundColor Yellow
} else {
    $changelogContent = Get-Content $changelog -Raw

    # Insert the stub immediately before the first existing "## " version
    # section (newest-at-top), preserving the file header/preamble above it.
    if ($changelogContent -match '(?m)^## ') {
        $firstSection = $changelogContent.IndexOf("`n## ")
        if ($firstSection -ge 0) {
            $insertAt = $firstSection + 1
            $head = $changelogContent.Substring(0, $insertAt)
            $tail = $changelogContent.Substring($insertAt)
            $newChangelog = $head + $changelogStub + "`n" + $tail
        } else {
            # First "## " is at the very start of the file (no preamble).
            $newChangelog = $changelogStub + "`n" + $changelogContent
        }
    } else {
        # No existing version sections — append the stub after the content.
        $newChangelog = $changelogContent.TrimEnd() + "`n`n" + $changelogStub
    }

    Set-Content -Path $changelog -Value $newChangelog -NoNewline
    Write-Host "  [OK]  CHANGELOG.md (prepended ## $Version section)" -ForegroundColor Green
}

# Commit if requested
if ($Commit -and -not $DryRun -and $failed -eq 0) {
    Write-Host ''
    Write-Host 'Committing version bump...' -ForegroundColor Cyan

    $filesToStage = @($targets | ForEach-Object { $_.File }) + 'CHANGELOG.md' | Sort-Object -Unique
    foreach ($f in $filesToStage) {
        git -C $RepoRoot add $f
    }
    git -C $RepoRoot -c user.email=user@example.com commit -m "release: v$Version"

    Write-Host "Committed: release: v$Version" -ForegroundColor Green
}

Write-Host ''

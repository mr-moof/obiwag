<#
.SYNOPSIS
    Shared low-level helpers for the Obi Wag deploy pipeline (OPT-12, #186).

.DESCRIPTION
    Extracted from deploy.ps1 so deploy.ps1 stays a slim dispatcher under the
    600-line cap. Dot-sourced by deploy.ps1 into the deploy script's scope, so
    functions here see the caller's $script:-scoped state (e.g.
    $script:deployFailures) and path variables.

    Depends on: lib/common.ps1 (Write-Info / Write-Problem) dot-sourced first.

    Provides:
      - Add-DeployFailure       : append to the $script:deployFailures accumulator (#136)
      - Copy-SingleFile         : copy one file with DryRun/Optional/failure tracking
      - Copy-DirectoryContents  : recursive copy with __pycache__/.pytest_cache filter
      - Test-PathUnderRoot      : root-containment guard (rejects ../ traversal) (#160)
#>

# Fail-closed accumulator helper. The list itself is initialized by the caller
# (deploy.ps1) before any deploy function runs; the null guard (gotchas #8)
# protects against a dot-source ordering mistake.
function Add-DeployFailure {
    param(
        [Parameter(Mandatory)][string]$Stage,
        [Parameter(Mandatory)][string]$Detail
    )
    if ($null -eq $script:deployFailures) {
        $script:deployFailures = [System.Collections.Generic.List[object]]::new()
    }
    $script:deployFailures.Add([pscustomobject]@{ Stage = $Stage; Detail = $Detail })
}

function Copy-SingleFile {
    param(
        [string]$Source,
        [string]$Destination,
        [switch]$DryRun,
        [switch]$Optional  # When set, missing source is reported but not a failure
    )

    if (-not (Test-Path $Source)) {
        if ($Optional) {
            Write-Info "Skipped (optional, not found): $Source"
        } else {
            Write-Problem "Source not found: $Source"
            Add-DeployFailure -Stage 'Copy-SingleFile' -Detail "Missing source: $Source -> $Destination"
        }
        return $false
    }

    if ($DryRun) {
        Write-Info "Would copy: $Source -> $Destination"
        return $true
    }

    $destDir = Split-Path -Parent $Destination
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    try {
        Copy-Item -Path $Source -Destination $Destination -Force -ErrorAction Stop
    } catch {
        Write-Problem "Copy failed: $Source -> $Destination`n    $($_.Exception.Message)"
        Add-DeployFailure -Stage 'Copy-SingleFile' -Detail "Copy failed: $Source -> $Destination ($($_.Exception.Message))"
        return $false
    }
    Write-Info "Copied: $(Split-Path -Leaf $Destination)"
    return $true
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination,
        [switch]$DryRun,
        [switch]$Optional  # When set, missing source is reported but not a failure
    )

    if (-not (Test-Path $Source)) {
        if ($Optional) {
            Write-Info "Skipped (optional, not found): $Source"
        } else {
            Write-Problem "Source directory not found: $Source"
            Add-DeployFailure -Stage 'Copy-DirectoryContents' -Detail "Missing source directory: $Source"
        }
        return
    }

    if ($DryRun) {
        Write-Info "Would copy: $Source -> $Destination"
        return
    }

    if (-not (Test-Path $Destination)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }

    Get-ChildItem -Path $Source -Recurse -ErrorAction SilentlyContinue | Where-Object {
        $_.FullName -notlike '*__pycache__*' -and $_.FullName -notlike '*.pytest_cache*'
    } | ForEach-Object {
        $relativePath = $_.FullName.Substring($Source.Length + 1)
        $targetPath = Join-Path $Destination $relativePath

        if ($_.PSIsContainer) {
            if (-not (Test-Path $targetPath)) {
                New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
            }
        } else {
            $targetDir = Split-Path -Parent $targetPath
            if (-not (Test-Path $targetDir)) {
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            }
            try {
                Copy-Item -Path $_.FullName -Destination $targetPath -Force -ErrorAction Stop
                Write-Info "Copied: $relativePath"
            } catch {
                Write-Problem "Copy failed: $relativePath`n    $($_.Exception.Message)"
                Add-DeployFailure -Stage 'Copy-DirectoryContents' -Detail "Copy failed: $($_.FullName) -> $targetPath ($($_.Exception.Message))"
            }
        }
    }
}

# Root-containment guard: assert resolved path is under $Root before any
# Remove-Item. Defends against corrupted/malicious manifest entries with
# `..` traversal or absolute paths. Returns $true if safe, $false (and
# warns) if the resolved path escapes the target root.
function Test-PathUnderRoot {
    param([string]$CandidatePath, [string]$Root)
    try {
        $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
        $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
        if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
            $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
        }
        return $resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

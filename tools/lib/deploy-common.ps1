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
      - Remove-RetiredObiTools  : exact cleanup for superseded peer-review wrappers
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

        # Do NOT rely on GetFullPath throwing to reject malformed input: that is
        # .NET Framework behavior only. On .NET (Core) 5+ -- i.e. pwsh 7, which is
        # what this workstation runs -- GetFullPath does NOT throw on an embedded
        # drive spec; it returns the string verbatim, so
        # "<root>\C:\evil.exe" StartsWith("<root>\") is TRUE and the guard would
        # ACCEPT it. Reject an embedded volume separator explicitly so both hosts
        # agree. (A colon is legal only as the drive separator at index 1.)
        if ($resolvedCandidate.IndexOf([System.IO.Path]::VolumeSeparatorChar, 2) -ge 0) {
            return $false
        }

        $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
        if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
            $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
        }
        return $resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Remove-RetiredObiAgentSkills {
    <#
    Remove only the obsolete, repo-owned adversarial-review skill that older
    installs copied into ~/.agents. The repository stopped shipping that skill,
    but the old deployment root is outside the normal manifest cleanup and can
    silently keep invoking the 30-minute legacy wrapper.

    A same-named user directory is preserved unless both Obi signatures are
    present. This deliberately does not perform broad ~/.agents cleanup.
    #>
    param(
        [string]$UserProfileRoot = $env:USERPROFILE,
        [switch]$DryRun
    )

    $agentsRoot = Join-Path $UserProfileRoot '.agents'
    $target = Join-Path $agentsRoot 'skills\codex-adversarial-review'
    if (-not (Test-Path -LiteralPath $target)) { return $true }

    if (-not (Test-PathUnderRoot -CandidatePath $target -Root $agentsRoot)) {
        Write-Problem "Refusing retired-skill cleanup outside ~/.agents: $target"
        Add-DeployFailure -Stage 'RetiredSkillCleanup' -Detail "Unsafe target: $target"
        return $false
    }

    $skillFile = Join-Path $target 'SKILL.md'
    $invokeFile = Join-Path $target 'invoke.py'
    $coreFile = Join-Path $target '_codex_core.py'
    $signatureOk = $false
    if ((Test-Path -LiteralPath $skillFile) -and
        (Test-Path -LiteralPath $invokeFile) -and
        (Test-Path -LiteralPath $coreFile)) {
        try {
            $skillText = Get-Content -LiteralPath $skillFile -Raw -ErrorAction Stop
            $invokeText = Get-Content -LiteralPath $invokeFile -Raw -ErrorAction Stop
            $coreText = Get-Content -LiteralPath $coreFile -Raw -ErrorAction Stop
            $signatureOk = (
                $skillText -match '(?m)^name:\s*codex-adversarial-review\s*$' -and
                $skillText -match 'single-purpose wrapper around the Codex CLI' -and
                $invokeText -match 'codex --profile review exec --json -' -and
                $invokeText -match 'CODEX_TIMEOUT_SEC_OVERRIDE' -and
                $coreText -match '(?m)^SIGNAL_SOURCE\s*=\s*["'']codex-adversarial-review["'']\s*$'
            )
        } catch {
            $signatureOk = $false
        }
    }

    if (-not $signatureOk) {
        Write-Problem "Preserving non-matching ~/.agents skill: $target"
        Add-DeployFailure -Stage 'RetiredSkillCleanup' -Detail "Signature mismatch: $target"
        return $false
    }

    if ($DryRun) {
        Write-Info "Would remove retired Obi skill: $target"
        return $true
    }

    try {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        Write-Info "Removed retired Obi skill: $target"
        return $true
    } catch {
        Write-Problem "Failed to remove retired Obi skill: $target ($($_.Exception.Message))"
        Add-DeployFailure -Stage 'RetiredSkillCleanup' -Detail "Remove failed: $target"
        return $false
    }
}

function Remove-RetiredObiTools {
    <#
    Remove only superseded Obi-owned peer-review files from the merge-deployed
    OBI_HOME tools tree. Copy-DirectoryContents is intentionally not a mirror,
    so deleting these files from source alone would leave runnable legacy paths.
    Each exact file must retain its historical signature or deployment fails
    closed and preserves it.
    #>
    param(
        [Parameter(Mandatory)][string]$ToolsTarget,
        [switch]$DryRun
    )

    $toolsRoot = Join-Path $ToolsTarget 'tools'
    if (-not (Test-Path -LiteralPath $toolsRoot)) { return $true }

    $retired = @(
        @{ Relative = 'codex-run.ps1'; Signature = 'Run codex non-interactively with observable streaming' }
        @{ Relative = 'codex-run.tests.ps1'; Signature = 'Pester 5 tests for tools/codex-run.ps1' }
        @{ Relative = 'codex-plan-prep.ps1'; Signature = 'Run codex.cmd against a plan file outside any git repo' }
        @{ Relative = 'codex-plan-prep.tests.ps1'; Signature = 'Pester 5 tests for tools/codex-plan-prep.ps1' }
        @{ Relative = 'schemas\codex-plan-critique.schema.json'; Signature = 'Codex plan-critique findings' }
    )

    $allRemoved = $true
    foreach ($entry in $retired) {
        $target = Join-Path $toolsRoot $entry.Relative
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { continue }

        if (-not (Test-PathUnderRoot -CandidatePath $target -Root $toolsRoot)) {
            Write-Problem "Refusing retired-tool cleanup outside OBI_HOME/tools: $target"
            Add-DeployFailure -Stage 'RetiredToolCleanup' -Detail "Unsafe target: $target"
            $allRemoved = $false
            continue
        }

        try {
            $content = Get-Content -LiteralPath $target -Raw -ErrorAction Stop
        } catch {
            Write-Problem "Cannot inspect retired Obi tool: $target ($($_.Exception.Message))"
            Add-DeployFailure -Stage 'RetiredToolCleanup' -Detail "Read failed: $target"
            $allRemoved = $false
            continue
        }
        if ($content -notmatch [regex]::Escape([string]$entry.Signature)) {
            Write-Problem "Preserving non-matching retired tool: $target"
            Add-DeployFailure -Stage 'RetiredToolCleanup' -Detail "Signature mismatch: $target"
            $allRemoved = $false
            continue
        }

        if ($DryRun) {
            Write-Info "Would remove retired Obi tool: $target"
            continue
        }
        try {
            Remove-Item -LiteralPath $target -Force -ErrorAction Stop
            Write-Info "Removed retired Obi tool: $target"
        } catch {
            Write-Problem "Failed to remove retired Obi tool: $target ($($_.Exception.Message))"
            Add-DeployFailure -Stage 'RetiredToolCleanup' -Detail "Remove failed: $target"
            $allRemoved = $false
        }
    }
    return $allRemoved
}

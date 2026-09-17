<#
.SYNOPSIS
    Reversibly archive one active Obi run before an explicitly chosen fresh start.

.DESCRIPTION
    Moves only files tied to the active RunId plus the documented single-active-run control and
    canonical phase artifacts. It never deletes. The destination is
    .obi/archive/state-<RunId>/ and contains a manifest mapping every original path to its archived
    path. A RunId mismatch, dispatch-state mismatch, unsafe path, reparse point, or destination
    collision fails before movement.

.PARAMETER RunId
    Active run id currently stored in .obi/state/run-id.txt.

.EXAMPLE
    powershell -NoProfile -File $env:OBI_HOME\tools\archive-run-state.ps1 `
        -RunId 20260807T180925Z -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string]$RunId
)

$ErrorActionPreference = 'Stop'

$pathSafetyPath = Join-Path $PSScriptRoot 'lib\path-safety.ps1'
if (-not (Test-Path -LiteralPath $pathSafetyPath -PathType Leaf)) {
    throw "Archive path-safety library not found: $pathSafetyPath"
}
. $pathSafetyPath

$repoRoot = ConvertTo-NormalPath -Path ([string]$PWD)
$obiRoot = Join-Path $repoRoot '.obi'
$stateRoot = Join-Path $obiRoot 'state'
$archiveParent = Join-Path $obiRoot 'archive'
$archiveRoot = Join-Path $archiveParent "state-$RunId"

if (-not (Test-Path -LiteralPath $stateRoot -PathType Container)) {
    throw "Obi state directory not found: $stateRoot"
}
if (-not (Test-NormalPathContained -Candidate $stateRoot -Root $repoRoot)) {
    throw "Obi state directory escapes repository root: $stateRoot"
}
if (-not (Test-NormalPathContained -Candidate $archiveRoot -Root $obiRoot)) {
    throw "Archive destination escapes .obi: $archiveRoot"
}
$stateReparse = Get-ReparsePointInPath -Path $stateRoot -Root $repoRoot
if ($stateReparse) {
    throw "Refusing reparse-point state path component: $stateReparse"
}
$archiveReparse = Get-ReparsePointInPath -Path $archiveRoot -Root $repoRoot
if ($archiveReparse) {
    throw "Refusing reparse-point archive path component: $archiveReparse"
}
if (Test-Path -LiteralPath $archiveRoot) {
    throw "Archive destination already exists; refusing collision: $archiveRoot"
}

$runIdPath = Join-Path $stateRoot 'run-id.txt'
if (-not (Test-Path -LiteralPath $runIdPath -PathType Leaf)) {
    throw "Active run id file not found: $runIdPath"
}
$activeRunId = (Get-Content -LiteralPath $runIdPath -Raw -Encoding UTF8).Trim()
if ($activeRunId -ne $RunId) {
    throw "RunId mismatch: requested '$RunId', active run is '$activeRunId'"
}

$dispatchStatePath = Join-Path $stateRoot 'dispatch-state.json'
if (Test-Path -LiteralPath $dispatchStatePath -PathType Leaf) {
    try { $dispatchState = Get-Content -LiteralPath $dispatchStatePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "dispatch-state.json is unreadable; refusing archive: $($_.Exception.Message)" }
    if (-not $dispatchState.run_id -or [string]$dispatchState.run_id -ne $RunId) {
        throw "dispatch-state.json RunId does not match active run '$RunId'"
    }
}

# Lifecycle trackers are the run's proof that every discovered failure was disposed. An exact
# case-insensitive `Status: OPEN` line blocks archival before any directory creation or movement.
# The archive helper never edits a tracker to make itself pass.
$reportsRoot = Join-Path $obiRoot 'reports'
$openTrackers = @()
if (Test-Path -LiteralPath $reportsRoot -PathType Container) {
    if (-not (Test-NormalPathContained -Candidate $reportsRoot -Root $obiRoot)) {
        throw "Obi reports directory escapes .obi: $reportsRoot"
    }
    $reportsReparse = Get-ReparsePointInPath -Path $reportsRoot -Root $obiRoot
    if ($reportsReparse) {
        throw "Refusing reparse-point reports path component: $reportsReparse"
    }
    $trackerPattern = '^' + [regex]::Escape($RunId) + '-.+\.md$'
    foreach ($file in @(Get-ChildItem -LiteralPath $reportsRoot -File -Force -ErrorAction Stop)) {
        if ($file.Name -notmatch $trackerPattern) { continue }
        if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse-point lifecycle tracker: $($file.FullName)"
        }
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        if ([regex]::IsMatch($content, '(?im)^Status: OPEN\r?$')) {
            $relative = $file.FullName.Substring($obiRoot.Length).TrimStart('\', '/')
            $openTrackers += ".obi/$($relative.Replace('\', '/'))"
        }
    }
}
if ($openTrackers.Count -gt 0) {
    [ordered]@{
        status = 'blocked_open_trackers'
        run_id = $RunId
        files_archived = 0
        open_trackers = @($openTrackers | Sort-Object)
    } | ConvertTo-Json -Depth 4
    exit 2
}

$targetMap = @{}
function Add-ArchiveTarget {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing reparse-point archive target: $($item.FullName)"
    }
    if (-not (Test-NormalPathContained -Candidate $item.FullName -Root $obiRoot)) {
        throw "Archive target escapes .obi: $($item.FullName)"
    }
    $targetReparse = Get-ReparsePointInPath -Path $item.FullName -Root $obiRoot
    if ($targetReparse) {
        throw "Refusing reparse-point archive target component: $targetReparse"
    }
    $targetMap[$item.FullName.ToLowerInvariant()] = $item.FullName
}

function Test-RunScopedArtifact {
    <# Match only canonical run-owned paths below a declared .obi artifact root. Unknown files are
       deliberately preserved; adding a new artifact convention requires extending this table. #>
    param(
        [Parameter(Mandatory)][string]$RelativeRoot,
        [Parameter(Mandatory)][string]$RelativePath,
        [Parameter(Mandatory)][string]$ActiveRunId
    )
    $path = $RelativePath.Replace('/', '\').TrimStart('\')
    $id = [regex]::Escape($ActiveRunId)
    $dispatchKey = '[0-9]{1,3}(?:c[1-3])?'
    $attempt = '(?:-a[12])?'
    switch ($RelativeRoot.ToLowerInvariant()) {
        'state' {
            return $path -match ("^(?:" +
                "worker-$id-$dispatchKey$attempt\.jsonl(?:\.(?:err|exit))?|" +
                "worker-sys-$id-$dispatchKey$attempt\.txt|" +
                "native-phase-$id-$dispatchKey\.json|" +
                "status-updates-$id\.jsonl|" +
                "dispatch-status-$id-$dispatchKey$attempt\.json|" +
                "worker-summary-$id-$dispatchKey$attempt\.json|" +
                "heartbeat-$id\.json|lane-$id\.txt|task-base-$id\.txt)$")
        }
        'reports' {
            return $path -match ("^$id-(?:author|discovery(?:-efficiency|-peer)?|" +
                "dogfood-failures|integration|learning(?:-efficiency)?|readme|release-gate|simplify)\.md$")
        }
        'reviews' {
            return $path -match "^$id-(?:review|rereview|readme-review|phase4-claude)\.md$"
        }
        'runtime' {
            return ($path -match "^(?:probes-$id\.jsonl|grep-gate-[0-9]+-$id\.json)$" -or
                    $path -match "^$id\\.+")
        }
        'review' {
            return ($path -match "^request-$id\.json$" -or $path -match "^runs\\$id\\.+")
        }
        default { return $false }
    }
}

# Run-scoped files are selected from explicit per-root conventions against paths relative to the
# scanned .obi root. Repository ancestors and longer identifier prefixes never establish ownership.
foreach ($relativeRoot in @('state', 'reports', 'reviews', 'runtime', 'review')) {
    $scanRoot = Join-Path $obiRoot $relativeRoot
    if (-not (Test-Path -LiteralPath $scanRoot -PathType Container)) { continue }
    foreach ($file in @(Get-ChildItem -LiteralPath $scanRoot -Recurse -File -Force -ErrorAction Stop)) {
        $relativePath = $file.FullName.Substring($scanRoot.Length).TrimStart('\', '/')
        if (Test-RunScopedArtifact -RelativeRoot $relativeRoot -RelativePath $relativePath `
                -ActiveRunId $RunId) {
            Add-ArchiveTarget -Path $file.FullName
        }
    }
}

# Single-active-run state. These generic names cannot safely coexist across runs, so the explicit
# fresh-start choice archives them. Deliberately excluded: codex-overrides.md, README.md, .gitkeep,
# telemetry, and all other unrecognized files.
foreach ($file in @(Get-ChildItem -LiteralPath $stateRoot -File -Force)) {
    $name = $file.Name
    if ($name -in @('run-id.txt', 'dispatch-state.json') -or
        $name -like 'phase-*-complete.marker' -or
        $name -like 'phase-*.json' -or
        $name -like 'task-*.md' -or
        $name -like 'resume-*.md') {
        Add-ArchiveTarget -Path $file.FullName
    }
}

foreach ($generic in @(
    (Join-Path $obiRoot 'discovery-report.md'),
    (Join-Path $obiRoot "discovery-report-$RunId.md"),
    (Join-Path $obiRoot 'integration-report.md'),
    (Join-Path $obiRoot 'reports\author-report.md')
)) {
    Add-ArchiveTarget -Path $generic
}

$targets = @($targetMap.Values | Sort-Object)
if ($targets.Count -eq 0) {
    throw "No archive targets found for active run '$RunId'"
}

$entries = @()
foreach ($source in $targets) {
    $relative = $source.Substring($obiRoot.Length).TrimStart('\', '/')
    $destination = Join-Path $archiveRoot $relative
    if (-not (Test-NormalPathContained -Candidate $destination -Root $archiveRoot)) {
        throw "Archive target escapes destination: $destination"
    }
    if (Test-Path -LiteralPath $destination) {
        throw "Archive target collision: $destination"
    }
    $entries += [ordered]@{
        original = ".obi/$($relative.Replace('\', '/'))"
        archived = ".obi/archive/state-$RunId/$($relative.Replace('\', '/'))"
    }
}

$manifest = [ordered]@{
    schema_version = 1
    run_id = $RunId
    status = 'planned'
    created_at = ([datetime]::UtcNow).ToString('o')
    finished_at = $null
    error = $null
    files = $entries
}

if ($WhatIfPreference) {
    $manifest.status = 'what_if'
    $manifest | ConvertTo-Json -Depth 6
    return
}
if (-not $PSCmdlet.ShouldProcess($archiveRoot, "move $($targets.Count) active-run file(s) and write manifest")) {
    $manifest.status = 'declined'
    $manifest | ConvertTo-Json -Depth 6
    return
}

New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
$createdArchiveReparse = Get-ReparsePointInPath -Path $archiveRoot -Root $repoRoot
if ($createdArchiveReparse) {
    throw "Refusing reparse-point archive path component after creation: $createdArchiveReparse"
}
$manifestPath = Join-Path $archiveRoot 'manifest.json'
$manifest.status = 'moving'
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

try {
    for ($i = 0; $i -lt $targets.Count; $i++) {
        $source = $targets[$i]
        $destinationRelative = $entries[$i].archived.Substring((".obi/archive/state-$RunId/").Length)
        $destination = Join-Path $archiveRoot $destinationRelative.Replace('/', '\')
        $destinationParent = Split-Path -Parent $destination
        if (-not (Test-Path -LiteralPath $destinationParent -PathType Container)) {
            New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
        }
        Move-Item -LiteralPath $source -Destination $destination -ErrorAction Stop
    }
    $manifest.status = 'complete'
    $manifest.finished_at = ([datetime]::UtcNow).ToString('o')
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
} catch {
    $manifest.status = 'failed'
    $manifest.finished_at = ([datetime]::UtcNow).ToString('o')
    $manifest.error = $_.Exception.Message
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    throw
}

[ordered]@{
    status = 'complete'
    run_id = $RunId
    files_archived = $targets.Count
    archive = $archiveRoot
    manifest = $manifestPath
} | ConvertTo-Json -Depth 3

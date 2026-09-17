<#
.SYNOPSIS
    Audit or reversibly archive abandoned Obi runtime artifacts.

.DESCRIPTION
    Recognizes only documented run-scoped artifact filename families below .obi. Audit mode is
    side-effect-free. Archive mode moves eligible families into
    .obi/archive/orphan-state-<RunId>/ and writes a manifest mapping every original path to its
    archived path. Active, recent, live, unknown, and reparse-point content is never moved.

.PARAMETER Mode
    Audit (default) reports classifications. Archive moves only eligible families.

.PARAMETER MinimumAgeHours
    A family is eligible only when its newest recognized artifact is at least this old.

.PARAMETER RunId
    Optional exact RunId filter. Repeat or pass an array to constrain an archive operation.
#>
[CmdletBinding()]
param(
    [ValidateSet('Audit', 'Archive')]
    [string]$Mode = 'Audit',

    [ValidateRange(1, 87600)]
    [double]$MinimumAgeHours = 24,

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string[]]$RunId
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
$now = [datetime]::UtcNow
$cutoff = $now.AddHours(-$MinimumAgeHours)
$script:unsafePaths = @()
$runIdPattern = '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'

if (-not (Test-Path -LiteralPath $obiRoot -PathType Container)) {
    throw "Obi directory not found: $obiRoot"
}
if (-not (Test-NormalPathContained -Candidate $obiRoot -Root $repoRoot)) {
    throw "Obi directory escapes repository root: $obiRoot"
}
$obiReparse = Get-ReparsePointInPath -Path $obiRoot -Root $repoRoot
if ($obiReparse) { throw "Refusing reparse-point .obi path component: $obiReparse" }

function Get-SafeFilesUnderRoot {
    param([Parameter(Mandatory)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return @() }
    if (-not (Test-NormalPathContained -Candidate $Root -Root $obiRoot)) {
        throw "Scan root escapes .obi: $Root"
    }
    $rootReparse = Get-ReparsePointInPath -Path $Root -Root $obiRoot
    if ($rootReparse) { throw "Refusing reparse-point scan root: $rootReparse" }

    $files = @()
    $pending = New-Object System.Collections.ArrayList
    [void]$pending.Add((Get-Item -LiteralPath $Root -Force))
    while ($pending.Count -gt 0) {
        $index = $pending.Count - 1
        $directory = $pending[$index]
        $pending.RemoveAt($index)
        foreach ($item in @(Get-ChildItem -LiteralPath $directory.FullName -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                $relativeUnsafe = $item.FullName.Substring($obiRoot.Length).TrimStart('\', '/')
                $script:unsafePaths += ".obi/$($relativeUnsafe.Replace('\', '/'))"
                continue
            }
            if ($item.PSIsContainer) {
                [void]$pending.Add($item)
            } else {
                $files += $item
            }
        }
    }
    return $files
}

function New-SafeArchiveDirectory {
    [CmdletBinding(SupportsShouldProcess)]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Root,
        [switch]$AllowExisting
    )
    if (-not (Test-NormalPathContained -Candidate $Path -Root $Root)) {
        throw "Archive directory escapes its allowed root: $Path"
    }

    if (Test-Path -LiteralPath $Path) {
        if (-not $AllowExisting) {
            throw "Archive directory unexpectedly already exists: $Path"
        }
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            throw "Archive path is not a directory: $Path"
        }
    } else {
        $parent = Split-Path -Parent $Path
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
            New-SafeArchiveDirectory -Path $parent -Root $Root -AllowExisting
        }
        $parentReparse = Get-ReparsePointInPath -Path $parent -Root $Root
        if ($parentReparse) { throw "Refusing reparse-point archive parent: $parentReparse" }
        if (-not $PSCmdlet.ShouldProcess($Path, 'create safe archive directory')) {
            throw "Archive directory creation was declined: $Path"
        }
        New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            throw "Archive directory was not created: $Path"
        }
    }

    $createdReparse = Get-ReparsePointInPath -Path $Path -Root $Root
    if ($createdReparse) { throw "Refusing reparse-point archive directory: $createdReparse" }
}

function Get-ArtifactRunId {
    param(
        [Parameter(Mandatory)][string]$RelativeRoot,
        [Parameter(Mandatory)][string]$RelativePath
    )
    $path = $RelativePath.Replace('/', '\').TrimStart('\')
    # Use a lazy RunId capture so the full canonical suffix wins over a shorter suffix embedded in
    # it (for example, `<RunId>-readme-review.md` must not become RunId `<RunId>-readme`).
    $id = '(?<id>[A-Za-z0-9][A-Za-z0-9._-]{0,79}?)'
    $key = '[0-9]{1,3}(?:c[1-3])?'
    $attempt = '(?:-a[12])?'
    $pattern = $null
    switch ($RelativeRoot.ToLowerInvariant()) {
        'state' {
            $pattern = "^(?:worker-$id-$key$attempt\.jsonl(?:\.(?:err|exit))?|" +
                "worker-sys-$id-$key$attempt\.txt|native-phase-$id-$key\.json|" +
                "status-updates-$id\.jsonl|" +
                "dispatch-status-$id-$key$attempt\.json|" +
                "worker-summary-$id-$key$attempt\.json|heartbeat-$id\.json|lane-$id\.txt|" +
                "task-base-$id\.txt)$"
        }
        'reports' {
            $pattern = "^$id-(?:author|discovery(?:-efficiency|-peer)?|dogfood-failures|" +
                "integration|learning(?:-efficiency)?|readme|release-gate|simplify)\.md$"
        }
        'reviews' {
            $pattern = "^$id-(?:readme-review|phase4-claude|rereview|review)\.md$"
        }
        'runtime' {
            $pattern = "^(?:probes-$id\.jsonl|grep-gate-[0-9]+-$id\.json|$id\\.+)$"
        }
        'review' {
            $pattern = "^(?:request-$id\.json|runs\\$id\\.+)$"
        }
        default { return $null }
    }
    $match = [regex]::Match($path, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if (-not $match.Success) { return $null }
    return $match.Groups['id'].Value
}

function Get-RunningStatusClassification {
    param([Parameter(Mandatory)]$Status)
    if ([string]$Status.status -ne 'running') { return 'terminal' }

    $deadlineFuture = $true
    if ($Status.deadline_at) {
        try { $deadlineFuture = ([datetime]$Status.deadline_at).ToUniversalTime() -gt $now }
        catch { return 'unsafe' }
    }
    $pidAlive = $true
    if ($Status.proc_pid) {
        try { $pidAlive = $null -ne (Get-Process -Id ([int]$Status.proc_pid) -ErrorAction SilentlyContinue) }
        catch { return 'unsafe' }
    }
    if ($deadlineFuture -or $pidAlive) { return 'live' }
    return 'stale'
}

$activeRunId = $null
$runIdPath = Join-Path $stateRoot 'run-id.txt'
if (Test-Path -LiteralPath $runIdPath -PathType Leaf) {
    $runIdReparse = Get-ReparsePointInPath -Path $runIdPath -Root $obiRoot
    if ($runIdReparse) { throw "Refusing reparse-point active RunId path: $runIdReparse" }
    $activeRunId = (Get-Content -LiteralPath $runIdPath -Raw -Encoding UTF8).Trim()
    if ([string]::IsNullOrWhiteSpace($activeRunId) -or
        -not [regex]::IsMatch($activeRunId, $runIdPattern)) {
        throw 'Invalid active RunId in .obi/state/run-id.txt; refusing orphan-state classification'
    }
}

$groups = @{}
foreach ($relativeRoot in @('state', 'reports', 'reviews', 'runtime', 'review')) {
    $scanRoot = Join-Path $obiRoot $relativeRoot
    foreach ($file in @(Get-SafeFilesUnderRoot -Root $scanRoot)) {
        $relativePath = $file.FullName.Substring($scanRoot.Length).TrimStart('\', '/')
        $artifactRunId = Get-ArtifactRunId -RelativeRoot $relativeRoot -RelativePath $relativePath
        if (-not $artifactRunId) { continue }
        if (-not $groups.ContainsKey($artifactRunId)) {
            $groups[$artifactRunId] = [ordered]@{
                run_id = $artifactRunId
                files = @()
                status_files = @()
            }
        }
        $groups[$artifactRunId].files += $file
        if ($relativeRoot -eq 'state' -and $file.Name -like 'dispatch-status-*.json') {
            $groups[$artifactRunId].status_files += $file
        }
    }
}

$requestedRunIds = @($RunId | Where-Object { $_ })
$groupResults = @()
$eligibleGroups = @()
foreach ($key in @($groups.Keys | Sort-Object)) {
    $group = $groups[$key]
    $classification = 'eligible'
    $reason = 'recognized family is inactive, terminal, and older than the cutoff'
    $lastWrite = @($group.files | ForEach-Object { $_.LastWriteTimeUtc } | Sort-Object -Descending)[0]

    if ($activeRunId -and $key.Equals($activeRunId, [System.StringComparison]::OrdinalIgnoreCase)) {
        $classification = 'active'
        $reason = 'RunId matches .obi/state/run-id.txt'
    } elseif ($requestedRunIds.Count -gt 0 -and $key -notin $requestedRunIds) {
        $classification = 'filtered_out'
        $reason = 'RunId was not selected'
    } else {
        $unsafeStatus = $false
        $liveStatus = $false
        foreach ($statusFile in @($group.status_files)) {
            try {
                $status = Get-Content -LiteralPath $statusFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                $statusClass = Get-RunningStatusClassification -Status $status
                if ($statusClass -eq 'unsafe') { $unsafeStatus = $true }
                if ($statusClass -eq 'live') { $liveStatus = $true }
            } catch {
                $unsafeStatus = $true
            }
        }
        if ($unsafeStatus) {
            $classification = 'unsafe_status'
            $reason = 'a dispatch status could not be safely interpreted'
        } elseif ($liveStatus) {
            $classification = 'live'
            $reason = 'a running dispatch still has a live PID or future deadline'
        } elseif ($lastWrite -gt $cutoff) {
            $classification = 'too_recent'
            $reason = 'the newest recognized artifact is newer than the age cutoff'
        }
    }

    $relativeFiles = @($group.files | ForEach-Object {
        $relative = $_.FullName.Substring($obiRoot.Length).TrimStart('\', '/')
        ".obi/$($relative.Replace('\', '/'))"
    } | Sort-Object)
    $result = [ordered]@{
        run_id = $key
        eligible = ($classification -eq 'eligible')
        classification = $classification
        reason = $reason
        newest_artifact_at = $lastWrite.ToString('o')
        files = $relativeFiles
    }
    $groupResults += [pscustomobject]$result
    if ($classification -eq 'eligible') { $eligibleGroups += $group }
}

$eligibleRunIds = @($eligibleGroups | ForEach-Object { $_.run_id } | Sort-Object)
$baseResult = [ordered]@{
    schema_version = 1
    status = 'audit_complete'
    mode = $Mode.ToLowerInvariant()
    active_run_id = $activeRunId
    minimum_age_hours = $MinimumAgeHours
    cutoff_at = $cutoff.ToString('o')
    eligible_run_ids = $eligibleRunIds
    groups = $groupResults
    unsafe_paths = @($script:unsafePaths | Sort-Object -Unique)
}

if ($Mode -eq 'Audit') {
    $baseResult | ConvertTo-Json -Depth 8
    exit 0
}

if ($eligibleGroups.Count -eq 0) {
    $baseResult.status = 'no_eligible_orphans'
    $baseResult | ConvertTo-Json -Depth 8
    exit 0
}

# Preflight every source, archive root, and destination before creating any directory or moving any
# file. One collision blocks the entire requested operation.
$plans = @()
foreach ($group in $eligibleGroups) {
    $archiveRoot = Join-Path $archiveParent "orphan-state-$($group.run_id)"
    if (-not (Test-NormalPathContained -Candidate $archiveRoot -Root $obiRoot)) {
        throw "Archive destination escapes .obi: $archiveRoot"
    }
    $archiveReparse = Get-ReparsePointInPath -Path $archiveRoot -Root $obiRoot
    if ($archiveReparse) { throw "Refusing reparse-point archive path component: $archiveReparse" }
    if (Test-Path -LiteralPath $archiveRoot) {
        throw "Orphan archive destination already exists; refusing collision: $archiveRoot"
    }

    $entries = @()
    foreach ($sourceItem in @($group.files | Sort-Object FullName)) {
        if (-not (Test-Path -LiteralPath $sourceItem.FullName -PathType Leaf)) {
            throw "Orphan source disappeared during preflight: $($sourceItem.FullName)"
        }
        $sourceReparse = Get-ReparsePointInPath -Path $sourceItem.FullName -Root $obiRoot
        if ($sourceReparse) { throw "Refusing reparse-point orphan source: $sourceReparse" }
        $relative = $sourceItem.FullName.Substring($obiRoot.Length).TrimStart('\', '/')
        $destination = Join-Path $archiveRoot $relative
        if (-not (Test-NormalPathContained -Candidate $destination -Root $archiveRoot)) {
            throw "Orphan archive target escapes destination: $destination"
        }
        if (Test-Path -LiteralPath $destination) {
            throw "Orphan archive target collision: $destination"
        }
        $entries += [ordered]@{
            original = ".obi/$($relative.Replace('\', '/'))"
            archived = ".obi/archive/orphan-state-$($group.run_id)/$($relative.Replace('\', '/'))"
            source = $sourceItem.FullName
            destination = $destination
        }
    }
    $plans += [pscustomobject]@{
        run_id = $group.run_id
        archive_root = $archiveRoot
        entries = $entries
    }
}

New-SafeArchiveDirectory -Path $archiveParent -Root $obiRoot -AllowExisting

$archiveResults = @()
$filesArchived = 0
foreach ($plan in $plans) {
    New-SafeArchiveDirectory -Path $plan.archive_root -Root $archiveParent
    $manifestPath = Join-Path $plan.archive_root 'manifest.json'
    $manifest = [ordered]@{
        schema_version = 1
        kind = 'orphan_state'
        run_id = $plan.run_id
        status = 'moving'
        created_at = ([datetime]::UtcNow).ToString('o')
        finished_at = $null
        error = $null
        minimum_age_hours = $MinimumAgeHours
        files = @($plan.entries | ForEach-Object {
            [ordered]@{ original = $_.original; archived = $_.archived }
        })
    }
    $manifest | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    try {
        foreach ($entry in $plan.entries) {
            $destinationParent = Split-Path -Parent $entry.destination
            New-SafeArchiveDirectory -Path $destinationParent -Root $plan.archive_root -AllowExisting
            Move-Item -LiteralPath $entry.source -Destination $entry.destination -ErrorAction Stop
            $filesArchived++
        }
        $manifest.status = 'complete'
        $manifest.finished_at = ([datetime]::UtcNow).ToString('o')
        $manifest | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        $archiveResults += [ordered]@{
            run_id = $plan.run_id
            archive = $plan.archive_root
            manifest = $manifestPath
            files_archived = @($plan.entries).Count
        }
    } catch {
        $manifest.status = 'failed'
        $manifest.finished_at = ([datetime]::UtcNow).ToString('o')
        $manifest.error = $_.Exception.Message
        $manifest | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        throw
    }
}

$baseResult.status = 'complete'
$baseResult.files_archived = $filesArchived
$baseResult.archives = $archiveResults
$baseResult | ConvertTo-Json -Depth 8

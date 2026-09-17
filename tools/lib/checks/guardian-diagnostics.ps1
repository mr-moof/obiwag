<#
.SYNOPSIS
    Extended diagnostic checks for Config Guardian: circular dependency detection
    and backup health validation.

.DESCRIPTION
    Dot-sourced by config-guardian.ps1 AFTER lib/common.ps1 and lib/validation.ps1.
    Assumes the caller has defined these script-scope variables:
        $HooksDir, $CoreDir, $PythonExe

    Provides:
    - Test-CircularDeps    (CG-4)
    - Test-BackupHealth    (CG-7)
#>

function Test-CircularDeps {
    <#
    .SYNOPSIS
        Detect circular import chains in Python hook modules.
    #>
    $results = @{ Clean = $true; Cycles = @() }

    if (-not (Test-Path $PythonExe)) {
        Write-Detail "Python not found at $PythonExe -skipping circular dep check"
        return $results
    }

    # Gather all .py files in hooks/ and hooks/core/
    $pyFiles = @()
    if (Test-Path $HooksDir) {
        $pyFiles += Get-ChildItem -Path $HooksDir -Filter '*.py' -ErrorAction SilentlyContinue
    }
    if (Test-Path $CoreDir) {
        $pyFiles += Get-ChildItem -Path $CoreDir -Filter '*.py' -ErrorAction SilentlyContinue
    }

    if ($pyFiles.Count -eq 0) {
        Write-Detail "No Python files to check for circular deps"
        return $results
    }

    # Build dependency graph from MODULE-LEVEL imports only.
    #
    # Indentation is significant here. A deferred (function-local) import is the standard,
    # correct way to break an import cycle in Python: it does not execute at import time, so
    # it cannot cause the ImportError this check exists to catch. The patterns used to be
    # anchored with `^\s*`, which matched indented imports identically to top-level ones, so
    # the check reported a permanent false-positive cycle
    # (session_state <-> memory_reader) whose two edges are BOTH function-local and one of
    # which is explicitly commented "Imported lazily ... a module-level import would be
    # circular". A gate that is always red trains readers to ignore it -- which is how the
    # rigor=max grep gate stayed a silent no-op for months -- so the anchor is now `^`
    # (column 0) and only real, import-time cycles are reported.
    $graph = @{}
    foreach ($file in $pyFiles) {
        $moduleName = $file.BaseName
        $imports = @()
        $content = Get-Content $file.FullName -ErrorAction SilentlyContinue

        foreach ($line in $content) {
            # Match: from core.xyz import ...
            if ($line -match '^from\s+core\.(\w+)\s+import') {
                $imports += $Matches[1]
            }
            # Match: import core.xyz
            elseif ($line -match '^import\s+core\.(\w+)') {
                $imports += $Matches[1]
            }
            # Match: from .xyz import ... (relative)
            elseif ($line -match '^from\s+\.(\w+)\s+import') {
                $imports += $Matches[1]
            }
        }
        $graph[$moduleName] = $imports | Select-Object -Unique
    }

    # DFS cycle detection
    $visited = @{}
    $inStack = @{}

    function Find-Cycle {
        param([string]$Node, [System.Collections.ArrayList]$Path)

        $visited[$Node] = $true
        $inStack[$Node] = $true
        $null = $Path.Add($Node)

        foreach ($neighbor in $graph[$Node]) {
            if (-not $visited.ContainsKey($neighbor)) {
                Find-Cycle -Node $neighbor -Path $Path
            } elseif ($inStack.ContainsKey($neighbor) -and $inStack[$neighbor]) {
                $cycleStart = $Path.IndexOf($neighbor)
                if ($cycleStart -ge 0) {
                    $cycle = @($Path[$cycleStart..($Path.Count - 1)]) + @($neighbor)
                    $script:foundCycles += ,@($cycle)
                }
            }
        }

        $inStack[$Node] = $false
        $Path.RemoveAt($Path.Count - 1)
    }

    $script:foundCycles = @()
    foreach ($node in $graph.Keys) {
        if (-not $visited.ContainsKey($node)) {
            $path = [System.Collections.ArrayList]::new()
            Find-Cycle -Node $node -Path $path
        }
    }

    if ($script:foundCycles.Count -gt 0) {
        $results.Clean = $false
        $results.Cycles = $script:foundCycles
        foreach ($cycle in $script:foundCycles) {
            Write-Problem "Circular import: $($cycle -join ' -> ')"
        }
    } else {
        Write-Check "No circular import dependencies detected"
    }

    # Check for meta-circularity (hooks that read/write settings.json)
    foreach ($file in $pyFiles) {
        $content = Get-Content $file.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -match 'settings\.json' -and $content -match '(open|write|dump)') {
            Write-Detail "Meta-reference: $($file.Name) references settings.json (by design for calibration)"
        }
    }

    return $results
}

function Test-BackupHealth {
    <#
    .SYNOPSIS
        Validate project memory backup freshness and integrity.
    #>
    $results = @{ Valid = $true; Errors = @() }

    $backupDir = Join-Path $env:USERPROFILE '.claude\.obi\backups\project-memory'
    if (-not (Test-Path $backupDir)) {
        $results.Valid = $false
        $results.Errors += 'Backup directory does not exist'
        Write-Problem 'Project memory backup directory missing'
        return $results
    }

    $snapshots = Get-ChildItem -Path $backupDir -Directory -Filter 'snapshot-*' |
        Sort-Object Name -Descending
    if ($snapshots.Count -eq 0) {
        $results.Valid = $false
        $results.Errors += 'No backup snapshots found'
        Write-Problem 'No project memory backup snapshots found'
        return $results
    }

    $latest = $snapshots[0]
    $indexPath = Join-Path $latest.FullName 'backup-index.json'
    if (-not (Test-Path $indexPath)) {
        $results.Valid = $false
        $results.Errors += 'Latest snapshot missing backup-index.json sentinel'
        Write-Problem "Latest snapshot missing sentinel: $($latest.Name)"
        return $results
    }

    # Check freshness (warn if older than 7 days)
    $age = (Get-Date) - $latest.CreationTime
    if ($age.TotalDays -gt 7) {
        $results.Valid = $false
        $results.Errors += "Latest backup is $([math]::Round($age.TotalDays)) days old"
        Write-Problem "Project memory backup is stale ($([math]::Round($age.TotalDays)) days old)"
    } else {
        Write-Check "Project memory backup: $($latest.Name) ($($snapshots.Count) snapshots, $([math]::Round($age.TotalDays, 1))d old)"
    }

    # Check that MEMORY.md is in the backup
    $memoryFile = Join-Path $latest.FullName 'MEMORY.md'
    if (-not (Test-Path $memoryFile)) {
        $results.Valid = $false
        $results.Errors += 'Latest snapshot does not contain MEMORY.md'
        Write-Problem 'Backup snapshot missing MEMORY.md'
    }

    return $results
}

<#
.SYNOPSIS
    Process-tree helpers for the bounded dispatch supervisor (tools/dispatch-worker.ps1).

.DESCRIPTION
    Dot-sourced by dispatch-worker.ps1. Kept in their own file so the dispatcher stays under the
    repository's 600-line cap and so a reaper or healthcheck can reuse the same tree-walk without
    launching a worker. All probes read ONE CIM snapshot per call and never throw: a CIM failure
    degrades to "unknown" (-1 / root-only), which the caller treats as no evidence, not as death.

    PowerShell 5.1+, pure ASCII.
#>

function Get-ProcTreeCpuMs {
    <# .SYNOPSIS Total user+kernel CPU (ms) of the root process and every descendant; -1 when the
       CIM snapshot is unavailable. Liveness signal for the watchdog (a busy tree is not idle). #>
    param([Parameter(Mandatory)][int]$RootPid)
    try {
        $all = Get-CimInstance Win32_Process -ErrorAction Stop
        if (-not $all) { return -1 }
        $byPid = @{}; $childrenOf = @{}
        foreach ($p in $all) {
            $byPid[[int]$p.ProcessId] = $p
            $parent = [int]$p.ParentProcessId
            if (-not $childrenOf.ContainsKey($parent)) {
                $childrenOf[$parent] = New-Object System.Collections.Generic.List[int]
            }
            $childrenOf[$parent].Add([int]$p.ProcessId)
        }
        $seen = @{}; $queue = New-Object System.Collections.Generic.Queue[int]
        $queue.Enqueue($RootPid); $totalMs = 0.0
        while ($queue.Count -gt 0) {
            $cur = $queue.Dequeue()
            if ($seen.ContainsKey($cur)) { continue }
            $seen[$cur] = $true
            $proc = $byPid[$cur]
            if ($proc) {
                $totalMs += ([double]$proc.UserModeTime + [double]$proc.KernelModeTime) / 10000.0
            }
            if ($childrenOf.ContainsKey($cur)) {
                foreach ($c in $childrenOf[$cur]) { $queue.Enqueue($c) }
            }
        }
        return $totalMs
    } catch { return -1 }
}

function Get-FileLenSafe {
    <# .SYNOPSIS File length in bytes, 0 when absent or unreadable (never throws). #>
    param([string]$Path)
    try { if (Test-Path -LiteralPath $Path) { return (Get-Item -LiteralPath $Path).Length } } catch {}
    return 0
}

function Test-PidAlive {
    <# .SYNOPSIS $true when a process with this id exists right now. #>
    param([int]$ProcId)
    if (-not $ProcId) { return $false }
    try { $null = Get-Process -Id $ProcId -ErrorAction Stop; return $true } catch { return $false }
}

function Get-ProcTreePids {
    <# .SYNOPSIS Live PIDs of the root AND every descendant, walking ParentProcessId from a
       single CIM snapshot. Windows keeps a child's ParentProcessId after the parent exits, so an
       orphaned claude descendant of a dead runner is still found (peer finding COD-003). #>
    param([Parameter(Mandatory)][int]$RootPid)
    $alive = New-Object System.Collections.Generic.List[int]
    try {
        $all = Get-CimInstance Win32_Process -ErrorAction Stop
        $childrenOf = @{}
        foreach ($p in $all) {
            $parent = [int]$p.ParentProcessId
            if (-not $childrenOf.ContainsKey($parent)) {
                $childrenOf[$parent] = New-Object System.Collections.Generic.List[int]
            }
            $childrenOf[$parent].Add([int]$p.ProcessId)
        }
        $livePids = @{}
        foreach ($p in $all) { $livePids[[int]$p.ProcessId] = $true }
        $seen = @{}; $queue = New-Object System.Collections.Generic.Queue[int]
        $queue.Enqueue($RootPid)
        while ($queue.Count -gt 0) {
            $cur = $queue.Dequeue()
            if ($seen.ContainsKey($cur)) { continue }
            $seen[$cur] = $true
            if ($livePids.ContainsKey($cur)) { $alive.Add($cur) }
            if ($childrenOf.ContainsKey($cur)) {
                foreach ($c in $childrenOf[$cur]) { $queue.Enqueue($c) }
            }
        }
    } catch {
        if (Test-PidAlive -ProcId $RootPid) { $alive.Add($RootPid) }
    }
    return @($alive)
}

function Stop-ProcTree {
    <# .SYNOPSIS Kill the root tree AND any orphaned descendant, then report whether anything is
       still alive. Returns $true when the whole tree is verified gone. #>
    param([Parameter(Mandatory)][int]$RootPid)
    $survivors = Get-ProcTreePids -RootPid $RootPid
    foreach ($id in $survivors) {
        try { & taskkill.exe /T /F /PID $id *> $null } catch {}
    }
    if ($survivors.Count -gt 0) { Start-Sleep -Milliseconds 500 }
    return ((Get-ProcTreePids -RootPid $RootPid).Count -eq 0)
}

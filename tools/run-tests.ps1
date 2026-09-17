<#
.SYNOPSIS
    Runs the Obi Wag test suites: PowerShell (Pester 5) and/or Python (pytest).

.DESCRIPTION
    Single entry point for the repo's tests. Before 0.69.72 there was none, and
    nothing documented how to invoke the suite -- which is how the PowerShell
    suite came to be silently un-runnable for anyone who just typed
    `Invoke-Pester`.

    HOSTS: runs correctly under both PowerShell 7 (`pwsh`, supported versions) and Windows PowerShell 5.1 (`powershell.exe` -- a separate
    binary at System32\WindowsPowerShell\v1.0, NOT an alias for pwsh). The
    distinction matters because their defaults differ: 5.1's
    `Set-Content -Encoding UTF8` emits a BOM and its `Get-Content` decodes as
    ANSI, while 7.x is BOM-less UTF-8 on both. The host in use is printed in the
    output so a host-specific failure is diagnosable.

    PESTER VERSION: this pins Pester 5 explicitly. Both 3.4.0 and 5.7.1 are
    installed and a bare `Import-Module Pester` resolves the highest version, so
    an unpinned run is a coin flip against whichever major version happens to be
    newest. The suite is Pester 5 syntax as of 0.69.72.

    PSMODULEPATH: NOT modified unconditionally. Verified on this workstation that
    both hosts resolve Pester 5.7.1, New-Guid, and Get-FileHash with an untouched
    PSModulePath. Blanket-prepending the Windows PowerShell 5.1 module directory
    is wrong under 7.x -- it invites Desktop-edition modules into Core. The
    prepend is applied only as a targeted REPAIR: on Desktop (5.1) AND only when
    New-Guid/Get-FileHash are actually missing, which is the signature of a
    PowerShell 7 Utility module shadowing 5.1's by path order.

.PARAMETER Path
    Specific test file(s) or directories to run. Paths are routed by contents:
    *.ps1 targets go to Pester and *.py targets go to pytest. A directory can
    select either or both suites. Default: all *.tests.ps1 tracked in git
    (PowerShell), hooks/tests, and tools/peer_review/tests (Python).

.PARAMETER PowerShellOnly
    Skip the Python suite.

.PARAMETER PythonOnly
    Skip the PowerShell suite.

.PARAMETER Detailed
    Show per-test output instead of only failures and the summary.

.PARAMETER ShardCount
    Split the default repository-wide Pester file list into this many deterministic round-robin
    shards. Requires PowerShellOnly, ShardIndex, and no explicit Path. Intended for release gates
    whose host foreground ceiling is shorter than the measured monolithic suite.

.PARAMETER ShardIndex
    One-based shard to select when ShardCount is supplied.

.PARAMETER ListTargets
    With sharding, print the exact selected Pester paths without importing Pester or running tests.
    Release orchestration uses this to prove shard completeness and disjointness before execution.

.EXAMPLE
    .\tools\run-tests.ps1
    # Everything: every tracked/untracked Pester test + both Python suites

.EXAMPLE
    .\tools\run-tests.ps1 -Path tools\run-grep-gates.tests.ps1
    # One file

.EXAMPLE
    .\tools\run-tests.ps1 -PowerShellOnly -Detailed

.EXAMPLE
    .\tools\run-tests.ps1 -PowerShellOnly -ShardIndex 1 -ShardCount 3
#>
[CmdletBinding()]
param(
    [string[]]$Path,
    [switch]$PowerShellOnly,
    [switch]$PythonOnly,
    [switch]$Detailed,
    [ValidateRange(0, 64)][int]$ShardCount = 0,
    [ValidateRange(0, 64)][int]$ShardIndex = 0,
    [switch]$ListTargets
)

$RepoRoot = Split-Path -Parent $PSScriptRoot
$failures = 0

# Windows PowerShell's external `powershell.exe -File` binding passes
# `-Path one.tests.ps1,two.tests.ps1` as one string even though Path is string[]. Accept that
# documented CLI shape explicitly; otherwise Pester receives a nonexistent comma-containing path
# and, before DF-12, the runner could report that exception as SUITE GREEN.
if ($Path) {
    $expandedPaths = @()
    foreach ($pathArg in @($Path)) {
        $expandedPaths += @($pathArg -split ',' | Where-Object { $_ } | ForEach-Object { $_.Trim() })
    }
    $Path = @($expandedPaths)
}

if ($PowerShellOnly -and $PythonOnly) {
    Write-Error '-PowerShellOnly and -PythonOnly cannot be combined.'
    exit 2
}

$shardingRequested = ($ShardCount -gt 0 -or $ShardIndex -gt 0)
if (($ShardCount -gt 0) -ne ($ShardIndex -gt 0)) {
    Write-Error '-ShardCount and -ShardIndex must be supplied together.'
    exit 2
}
if ($shardingRequested) {
    if ($ShardIndex -gt $ShardCount) {
        Write-Error '-ShardIndex must be between 1 and ShardCount.'
        exit 2
    }
    if (-not $PowerShellOnly -or $PythonOnly -or $Path) {
        Write-Error 'Sharding requires -PowerShellOnly and cannot be combined with -PythonOnly or -Path.'
        exit 2
    }
}
if ($ListTargets -and -not $shardingRequested) {
    Write-Error '-ListTargets is supported only with -ShardCount and -ShardIndex.'
    exit 2
}

# Classify explicit paths once. Historically -Path narrowed only Pester while
# silently running the full Python suite afterward, turning a focused check into
# a multi-minute repository gate. Route each target to the suite it actually
# contains instead.
$requestedPowerShellTargets = @()
$requestedPythonTargets = @()
foreach ($requestedPath in @($Path)) {
    $resolvedPath = if ([System.IO.Path]::IsPathRooted($requestedPath)) {
        $requestedPath
    } else {
        Join-Path $RepoRoot $requestedPath
    }
    if (-not (Test-Path -LiteralPath $resolvedPath)) {
        Write-Error "Requested test target does not exist: $requestedPath"
        exit 2
    }
    if (Test-Path -LiteralPath $resolvedPath -PathType Container) {
        if (@(Get-ChildItem -LiteralPath $resolvedPath -Recurse -File -Filter '*.tests.ps1' -ErrorAction SilentlyContinue).Count -gt 0) {
            $requestedPowerShellTargets += $resolvedPath
        }
        if (@(Get-ChildItem -LiteralPath $resolvedPath -Recurse -File -Filter 'test_*.py' -ErrorAction SilentlyContinue).Count -gt 0) {
            $requestedPythonTargets += $resolvedPath
        }
    } elseif ([System.IO.Path]::GetExtension($resolvedPath) -ieq '.ps1') {
        $requestedPowerShellTargets += $resolvedPath
    } elseif ([System.IO.Path]::GetExtension($resolvedPath) -ieq '.py') {
        $requestedPythonTargets += $resolvedPath
    } else {
        Write-Error "Cannot route test path to Pester or pytest: $requestedPath"
        exit 2
    }
}

if ($Path -and $requestedPowerShellTargets.Count -eq 0 -and $requestedPythonTargets.Count -eq 0) {
    Write-Error 'No Pester or pytest targets were found under the requested path(s).'
    exit 2
}

if ($Path -and $PowerShellOnly -and $requestedPowerShellTargets.Count -eq 0) {
    Write-Error 'No PowerShell test targets were selected.'
    exit 2
}
if ($Path -and $PythonOnly -and $requestedPythonTargets.Count -eq 0) {
    Write-Error 'No Python test targets were selected.'
    exit 2
}

# Deliberately NOT 'Stop'. Under 'Stop', a native command writing to stderr raises a
# terminating RemoteException inside a test body -- so a test that shells out to git
# fails on a benign notice like "LF will be replaced by CRLF", with the git warning
# reported as the assertion failure. Pester does its own error handling per test; the
# runner must not impose a preference that turns tool chatter into false failures.
$ErrorActionPreference = 'Continue'

Write-Host "Host: PowerShell $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)" -ForegroundColor DarkGray

if (-not $PythonOnly -and (-not $Path -or $requestedPowerShellTargets.Count -gt 0)) {
    # Targeted repair only -- see PSMODULEPATH in .DESCRIPTION. On Windows PowerShell
    # 5.1, a PowerShell 7 Utility module can shadow 5.1's by PSModulePath order and
    # New-Guid / Get-FileHash vanish; prepending 5.1's own module dir restores them.
    # Never do this on Core: it pulls Desktop-edition modules into 7.x.
    if ($PSVersionTable.PSEdition -eq 'Desktop' -and
        (-not (Get-Command New-Guid -ErrorAction SilentlyContinue) -or
         -not (Get-Command Get-FileHash -ErrorAction SilentlyContinue))) {
        Write-Host '  Repairing PSModulePath (PS7 Utility module is shadowing 5.1)' -ForegroundColor Yellow
        $env:PSModulePath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\Modules;$env:PSModulePath"
    }

    $targets = if ($Path) { $requestedPowerShellTargets } else {
        Push-Location $RepoRoot
        try {
            @(git ls-files --cached --others --exclude-standard -- '*.tests.ps1' | Sort-Object -Unique)
        } finally { Pop-Location }
    }
    if (-not $targets) {
        Write-Error 'No PowerShell test files found.'
        exit 2
    }
    $targets = @($targets | ForEach-Object {
        if ([System.IO.Path]::IsPathRooted($_)) { $_ } else { Join-Path $RepoRoot $_ }
    })

    if ($shardingRequested) {
        $allTargets = @($targets)
        $targets = @(for ($i = 0; $i -lt $allTargets.Count; $i++) {
            if (($i % $ShardCount) + 1 -eq $ShardIndex) { $allTargets[$i] }
        })
        if ($targets.Count -eq 0) {
            Write-Error "Pester shard $ShardIndex/$ShardCount selected zero files."
            exit 2
        }
        Write-Host "Pester shard: $ShardIndex/$ShardCount ($($targets.Count)/$($allTargets.Count) files)" -ForegroundColor DarkGray
    }
    if ($ListTargets) {
        foreach ($target in $targets) { Write-Output "TARGET=$target" }
        exit 0
    }

    $pester = Get-Module Pester -ListAvailable |
        Where-Object { $_.Version.Major -ge 5 } |
        Sort-Object Version -Descending | Select-Object -First 1
    if (-not $pester) {
        Write-Error "Pester 5+ not found. Install with: Install-Module Pester -MinimumVersion 5.0 -Scope CurrentUser -Force"
        exit 2
    }
    Import-Module Pester -RequiredVersion $pester.Version -ErrorAction Stop

    Write-Host "`n=== PowerShell suite (Pester $($pester.Version), $($targets.Count) file(s)) ===" -ForegroundColor Cyan

    $cfg = New-PesterConfiguration
    $cfg.Run.Path = $targets
    $cfg.Run.PassThru = $true
    $cfg.Output.Verbosity = if ($Detailed) { 'Detailed' } else { 'None' }
    $r = $null
    $pesterInvocationFailed = $false
    try {
        $r = Invoke-Pester -Configuration $cfg -ErrorAction Stop
    } catch {
        Write-Host "PowerShell: RUNNER ERROR - $($_.Exception.Message)" -ForegroundColor Red
        $pesterInvocationFailed = $true
        $failures++
    }

    if ($null -eq $r) {
        if (-not $pesterInvocationFailed) { $failures++ }
        Write-Host 'PowerShell: PASSED=0 FAILED=1 SKIPPED=0' -ForegroundColor Red
    } elseif ($r.Result -eq 'Failed' -and [int]$r.FailedCount -eq 0) {
        # Discovery and BeforeAll failures can leave PassedCount positive and FailedCount zero.
        Write-Host 'PowerShell: CONTAINER OR DISCOVERY FAILURE' -ForegroundColor Red
        foreach ($container in @($r.Containers)) {
            foreach ($record in @($container.ErrorRecord)) {
                if ($record) { Write-Host "  $($record.Exception.Message)" -ForegroundColor Red }
            }
        }
        $failures++
        Write-Host "PowerShell: PASSED=$($r.PassedCount) FAILED=1 SKIPPED=$($r.SkippedCount)" -ForegroundColor Red
    } elseif (([int]$r.PassedCount + [int]$r.FailedCount + [int]$r.SkippedCount) -eq 0) {
        Write-Host 'PowerShell: ZERO TESTS EXECUTED' -ForegroundColor Red
        $failures++
        Write-Host 'PowerShell: PASSED=0 FAILED=1 SKIPPED=0' -ForegroundColor Red
    } else {
        foreach ($t in $r.Tests) {
            if ($t.Result -ne 'Passed') {
                Write-Host "  [$($t.Result)] $($t.ExpandedPath)" -ForegroundColor Red
                $msg = ($t.ErrorRecord.Exception.Message -split "`n")[0]
                if ($msg) { Write-Host "         $msg" -ForegroundColor DarkGray }
            }
        }
        $color = if ($r.FailedCount -gt 0) { 'Red' } else { 'Green' }
        Write-Host "PowerShell: PASSED=$($r.PassedCount) FAILED=$($r.FailedCount) SKIPPED=$($r.SkippedCount)" -ForegroundColor $color
        $failures += $r.FailedCount
    }
}

if (-not $PowerShellOnly -and (-not $Path -or $requestedPythonTargets.Count -gt 0)) {
    Write-Host "`n=== Python hook suite (pytest) ===" -ForegroundColor Cyan
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host 'Python: SKIPPED (python not on PATH)' -ForegroundColor Yellow
        $pyExit = 0
    } else {
        # Initialized before the try: if python cannot start, the finally still runs and
        # $pyExit must not be $null -- `$null -ne 0` would report a phantom failure.
        $pyExit = 0
        $pythonTargets = if ($Path) {
            @($requestedPythonTargets)
        } else {
            @('hooks/tests', 'tools/peer_review/tests', 'tools/tests')
        }
        # PowerShell unwraps a one-item expression result to a scalar. Re-wrap
        # before splatting or a path string is expanded character-by-character
        # and pytest receives only its drive letter (for example, "C").
        $pythonTargets = @($pythonTargets)
        Push-Location $RepoRoot
        try {
            # Multiple repository roots contain a package named `tests`. Importlib mode keeps
            # their same-named modules isolated instead of resolving the later file through the
            # first package imported during collection.
            & python -m pytest @pythonTargets -q --no-header --import-mode=importlib
            $pyExit = $LASTEXITCODE
        } finally { Pop-Location }
    }
    if ($pyExit -ne 0) {
        Write-Host "Python: FAILED (exit $pyExit)" -ForegroundColor Red
        $failures += 1
    } else {
        Write-Host 'Python: PASSED' -ForegroundColor Green
    }
}

Write-Host ''
if ($failures -gt 0) {
    Write-Host "SUITE FAILED ($failures failing)" -ForegroundColor Red
} else {
    Write-Host 'SUITE GREEN' -ForegroundColor Green
}
exit $failures

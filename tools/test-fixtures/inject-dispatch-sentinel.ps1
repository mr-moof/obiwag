<#
.SYNOPSIS
    Test helper that emits the dispatch sentinel string for verifying Phase-Output Validation.

.DESCRIPTION
    Reads .obi/runtime/test-fixtures/dispatch-sentinel.txt and writes its content to stdout, as if
    it were the return value of a stuck Agent dispatch. Used by the contrived test described in the
    plan's Definition of Done to verify that the Phase-Output Validation contract in
    orchestration/obi-auto.md fires Step 2 (classify) on encountering the sentinel.

    This is NOT production code. It exists to make the otherwise-non-reproducible sentinel
    failure mode deterministic for test purposes.

.EXAMPLE
    .\tools\test-fixtures\inject-dispatch-sentinel.ps1
    # Prints "[Tool result missing due to internal error]"
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$FixturePath = Join-Path $RepoRoot '.obi\runtime\test-fixtures\dispatch-sentinel.txt'

if (-not (Test-Path $FixturePath)) {
    $FixtureDir = Split-Path -Parent $FixturePath
    New-Item -ItemType Directory -Force -Path $FixtureDir | Out-Null
    Set-Content -Path $FixturePath -Value '[Tool result missing due to internal error]' -Encoding ascii -NoNewline
}

Get-Content -Path $FixturePath -Raw

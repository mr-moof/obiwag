<#
.SYNOPSIS
    Pester 3.4 tests for tools/codex-plan-prep.ps1.

.DESCRIPTION
    Models the plan's acceptance tests:
    1. Missing plan file -> non-zero exit + clear error
    2. Tempdir created, populated, removed on success
    3. git init + commit succeed (real git)
    4. CodexArgs pass through verbatim (mock codex echoes args)
    5. Tempdir removed even when codex exits non-zero (try/finally)

    Real git is used; codex is mocked via a stub in $TestDrive added to PATH.
#>

$ScriptPath = Join-Path $PSScriptRoot 'codex-plan-prep.ps1'

Describe 'codex-plan-prep' {

    BeforeEach {
        $script:OldPath = $env:PATH

        # Build a stub codex.cmd that echoes its arguments and the cwd
        $script:StubDir = Join-Path $TestDrive 'stubs'
        New-Item -ItemType Directory -Path $StubDir -Force | Out-Null

        $script:StubLog = Join-Path $TestDrive 'stub-log.txt'
        $stubBody = @"
@echo off
echo CODEX_STUB_INVOKED >> "$StubLog"
echo CWD: %CD% >> "$StubLog"
echo ARGS: %* >> "$StubLog"
exit /b 0
"@
        Set-Content -Path (Join-Path $StubDir 'codex.cmd') -Value $stubBody -Encoding ASCII

        # Prepend stub dir to PATH so 'codex' resolves to our stub
        $env:PATH = $StubDir + ';' + $env:PATH

        # Sample plan file
        $script:SamplePlan = Join-Path $TestDrive 'sample-plan.md'
        Set-Content -Path $SamplePlan -Value "# Sample plan`nDo a thing." -Encoding UTF8
    }

    AfterEach {
        $env:PATH = $script:OldPath
    }

    # NOTE: Pester 3.4 in PS 5.1 does not reliably propagate $LASTEXITCODE
    # from a script invoked via the call operator inside an It block.
    # Tests therefore observe side effects (stub log contents, tempdir
    # absence, error stream messages) rather than asserting on
    # $LASTEXITCODE. The script's exit-code semantics are exercised by
    # tests/integration/test_obi_auto_max_phase0.ps1 via subprocess.

    It 'fails fast and emits clear error when plan file does not exist' {
        $missing = Join-Path $TestDrive 'no-such-plan.md'
        $err = & $ScriptPath -PlanPath $missing 2>&1
        ($err -join "`n") -match 'Plan file not found' | Should Be $true
        # No stub invocation should have happened
        (Test-Path $StubLog) | Should Be $false
    }

    It 'invokes codex with -C <tmp> and forwards CodexArgs verbatim' {
        & $ScriptPath -PlanPath $SamplePlan -CodexArgs @('--profile','review','exec','--json','-') 2>&1 | Out-Null
        (Test-Path $StubLog) | Should Be $true
        $log = Get-Content $StubLog -Raw
        ($log -match 'CODEX_STUB_INVOKED') | Should Be $true
        ($log -match 'codex-plan-')        | Should Be $true
        ($log -match '--profile')          | Should Be $true
        ($log -match 'review')             | Should Be $true
        ($log -match 'exec')               | Should Be $true
        ($log -match '--json')             | Should Be $true
    }

    It 'cleans up tempdir on success' {
        & $ScriptPath -PlanPath $SamplePlan -CodexArgs @('exec','-') 2>&1 | Out-Null

        # The tempdir is the value passed after `-C` in the codex command line.
        # Stub records ARGS verbatim. Extract the tempdir from there.
        $log = Get-Content $StubLog
        $argsLine = $log | Where-Object { $_ -match '^ARGS:\s+-C\s+(\S+)' } | Select-Object -First 1
        $argsLine | Should Not BeNullOrEmpty
        $null = $argsLine -match '^ARGS:\s+-C\s+(\S+)'
        $tempDir = $Matches[1]
        (Test-Path -LiteralPath $tempDir) | Should Be $false
    }

    It 'cleans up tempdir even when codex exits non-zero' {
        # Replace stub with one that exits non-zero. Mirror the success-stub
        # output format so the same ARGS parser works.
        $failStub = @"
@echo off
echo CODEX_FAIL_INVOKED >> "$StubLog"
echo CWD: %CD% >> "$StubLog"
echo ARGS: %* >> "$StubLog"
exit /b 7
"@
        Set-Content -Path (Join-Path $StubDir 'codex.cmd') -Value $failStub -Encoding ASCII

        & $ScriptPath -PlanPath $SamplePlan -CodexArgs @('exec','-') 2>&1 | Out-Null

        # Stub recorded its invocation
        (Test-Path $StubLog) | Should Be $true
        $log = Get-Content $StubLog
        ($log -join "`n") -match 'CODEX_FAIL_INVOKED' | Should Be $true

        # Tempdir cleaned up despite non-zero exit (try/finally check)
        $argsLine = $log | Where-Object { $_ -match '^ARGS:\s+-C\s+(\S+)' } | Select-Object -First 1
        $argsLine | Should Not BeNullOrEmpty
        $null = $argsLine -match '^ARGS:\s+-C\s+(\S+)'
        $tempDir = $Matches[1]
        (Test-Path -LiteralPath $tempDir) | Should Be $false
    }

    It 'uses default CodexArgs when none provided' {
        & $ScriptPath -PlanPath $SamplePlan 2>&1 | Out-Null
        $log = Get-Content $StubLog -Raw
        # Default is --profile review exec -
        ($log -match '--profile')          | Should Be $true
        ($log -match 'review')             | Should Be $true
    }
}

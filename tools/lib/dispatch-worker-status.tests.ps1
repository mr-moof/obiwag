<#
.SYNOPSIS
    Pester 5 tests: Write-DispatchStatus must be TERMINATING on every durability step.

.DESCRIPTION
    Peer finding PR-002 (run 20260901T221915Z): the supervisor runs under
    $ErrorActionPreference = 'Continue', so a non-terminating cmdlet error inside the status
    writer was printed and ignored, the try/catch fault boundary never fired, and the status
    could stay 'running' forever. These tests use REAL failure modes (locked destination, a
    file where a directory is needed, a directory where a file is needed) rather than mocks, so
    they exercise the exact -ErrorAction Stop additions.
#>
BeforeAll {
    . (Join-Path $PSScriptRoot 'dispatch-worker-lib.ps1')
}

Describe 'Write-DispatchStatus durability' {
    It 'writes atomically on the happy path and leaves no temp file' {
        $p = Join-Path $TestDrive 'status.json'
        Write-DispatchStatus -Status ([pscustomobject]@{ status = 'running' }) -Path $p
        (Get-Content -LiteralPath $p -Raw | ConvertFrom-Json).status | Should -Be 'running'
        Test-Path -LiteralPath "$p.tmp" | Should -Be $false
    }

    It 'throws when the atomic rename target is locked instead of continuing silently' {
        $p = Join-Path $TestDrive 'locked.json'
        $stream = [System.IO.File]::Open($p, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        try {
            $ErrorActionPreference = 'Continue'
            { Write-DispatchStatus -Status ([pscustomobject]@{ status = 'error' }) -Path $p } | Should -Throw
        } finally {
            $stream.Dispose()
        }
    }

    It 'throws when the parent directory cannot be created' {
        # A FILE occupies the path where the parent directory must be created.
        $blocker = Join-Path $TestDrive 'not-a-dir'
        Set-Content -LiteralPath $blocker -Value 'x' -Encoding ascii
        $ErrorActionPreference = 'Continue'
        { Write-DispatchStatus -Status ([pscustomobject]@{ status = 'error' }) -Path (Join-Path $blocker 'c.json') } | Should -Throw
    }

    It 'throws when the temp file cannot be written' {
        # The temp path "<Path>.tmp" is occupied by a DIRECTORY, so Set-Content fails.
        $p = Join-Path $TestDrive 'dir-tmp.json'
        New-Item -ItemType Directory -Path "$p.tmp" -Force | Out-Null
        $ErrorActionPreference = 'Continue'
        { Write-DispatchStatus -Status ([pscustomobject]@{ status = 'error' }) -Path $p } | Should -Throw
    }
}

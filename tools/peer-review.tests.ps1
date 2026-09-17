BeforeAll {
    $ScriptPath = Join-Path $PSScriptRoot 'peer-review.ps1'

    function ConvertTo-PeerChildText {
        param([object[]]$Records)
        $text = (@($Records) | ForEach-Object { $_.ToString() }) -join ' '
        return ($text -replace '\s+', ' ').Trim()
    }
}

Describe 'peer-review PowerShell entrypoint' {
    It 'exposes the complete durable lifecycle without provider passthrough' {
        $source = Get-Content -LiteralPath $ScriptPath -Raw
        foreach ($operation in @('preflight', 'run', 'start', 'status', 'wait', 'result', 'cancel')) {
            $source | Should -Match ([regex]::Escape("'$operation'"))
        }
        $source | Should -Not -Match 'CodexArgs|ClaudeArgs'
    }

    It 'exposes an explicit semantic primary platform for run and start' {
        $source = Get-Content -LiteralPath $ScriptPath -Raw
        $source | Should -Match ([regex]::Escape("[ValidateSet('codex', 'claude', 'claude-code')]"))
    }

    It 'behaviorally forwards platform to Python for run and start and omits it when absent' {
        $shim = Join-Path $TestDrive 'python-shim'
        New-Item -ItemType Directory -Path $shim -Force | Out-Null
        @'
@echo off
echo %*>"%OBI_PEER_ARGV_LOG%"
exit /b 0
'@ | Set-Content -LiteralPath (Join-Path $shim 'python.cmd') -Encoding Ascii
        $request = Join-Path $TestDrive 'request.json'
        '{}' | Set-Content -LiteralPath $request -Encoding Ascii
        $log = Join-Path $TestDrive 'python-argv.txt'
        $oldPath = $env:PATH
        $oldLog = $env:OBI_PEER_ARGV_LOG
        try {
            $env:PATH = "$shim;$oldPath"
            $env:OBI_PEER_ARGV_LOG = $log
            foreach ($operation in @('run', 'start')) {
                Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath $operation `
                    -RepoRoot $TestDrive -RequestFile $request -TimeoutSec 1 `
                    -Platform claude-code | Out-Null
                $LASTEXITCODE | Should -Be 0
                (Get-Content -LiteralPath $log -Raw) | Should -Match '--platform claude-code'
                (Get-Content -LiteralPath $log -Raw) | Should -Match '--authorization auto'
            }

            Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath preflight `
                -RepoRoot $TestDrive -RequestFile $request -Platform codex | Out-Null
            $LASTEXITCODE | Should -Be 0
            (Get-Content -LiteralPath $log -Raw) | Should -Match '--platform codex'
            (Get-Content -LiteralPath $log -Raw) | Should -Not -Match '--authorization'

            Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath run `
                -RepoRoot $TestDrive -RequestFile $request -TimeoutSec 1 `
                -Authorization approved -ApprovalScopeSha256 (('a' * 64) -join '') | Out-Null
            $LASTEXITCODE | Should -Be 0
            (Get-Content -LiteralPath $log -Raw) | Should -Match '--authorization approved'
            (Get-Content -LiteralPath $log -Raw) | Should -Match ('--approval-scope-sha256 ' + ('a' * 64 -join ''))

            Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath run `
                -RepoRoot $TestDrive -RequestFile $request -TimeoutSec 1 | Out-Null
            $LASTEXITCODE | Should -Be 0
            (Get-Content -LiteralPath $log -Raw) | Should -Not -Match '--platform'
        } finally {
            $env:PATH = $oldPath
            $env:OBI_PEER_ARGV_LOG = $oldLog
        }
    }

    It 'requires RequestFile only for preflight, run, and start' {
        foreach ($operation in @('preflight', 'run', 'start')) {
            $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath $operation -RepoRoot $TestDrive 2>&1
            (ConvertTo-PeerChildText -Records $output) | Should -Match 'RequestFile is required'
            $LASTEXITCODE | Should -Be 2
        }
    }

    It 'documents that RequestFile is the schema-version 1 JSON semantic request' {
        $help = Get-Help $ScriptPath -Parameter RequestFile | Out-String -Width 4096
        $help | Should -Match 'schema-version 1 JSON semantic request'
        $help | Should -Match 'not a free-form Markdown prompt'
    }

    It 'requires RunId for broker observation operations' {
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath status -RepoRoot $TestDrive 2>&1
        (ConvertTo-PeerChildText -Records $output) | Should -Match 'RunId is required'
        $LASTEXITCODE | Should -Be 2
    }

    It 'applies distinct foreground and broker timeout ceilings' {
        $run = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath run -RepoRoot $TestDrive -RequestFile missing.json -TimeoutSec 241 2>&1
        (ConvertTo-PeerChildText -Records $run) | Should -Match 'between 1 and 240'
        $LASTEXITCODE | Should -Be 2

        $start = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath start -RepoRoot $TestDrive -RequestFile missing.json -TimeoutSec 3601 2>&1
        (ConvertTo-PeerChildText -Records $start) | Should -Match 'between 1 and\s+3600'
        $LASTEXITCODE | Should -Be 2
    }

    It 'bounds public wait duration' {
        $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath wait -RepoRoot $TestDrive -RunId sample -WaitSec 241 2>&1
        (ConvertTo-PeerChildText -Records $output) | Should -Match 'WaitSec must be between 1 and 240'
        $LASTEXITCODE | Should -Be 2
    }
}

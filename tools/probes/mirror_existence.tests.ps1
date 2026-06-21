<#
.SYNOPSIS
    Pester 3.4 tests for tools/probes/mirror_existence.ps1.

.DESCRIPTION
    Behavioral tests stub the native `gh` CLI. The probe re-dot-sources
    _lib.ps1, so mocking Invoke-GhApi directly is unreliable; stubbing `gh`
    survives the re-import because the probe never redefines `gh`.
#>

$LibPath   = Join-Path $PSScriptRoot '_lib.ps1'
$ProbePath = Join-Path $PSScriptRoot 'mirror_existence.ps1'

function Set-GhStub {
    param([string]$Stdout = '', [int]$ExitCode = 0, [string]$Stderr = '')
    $global:GhStubStdout   = $Stdout
    $global:GhStubExitCode = $ExitCode
    $global:GhStubStderr   = $Stderr
    Mock Get-Command { [pscustomobject]@{ Name = 'gh' } } -ParameterFilter { $Name -eq 'gh' }
    function global:gh {
        $global:LASTEXITCODE = $global:GhStubExitCode
        if ($global:GhStubStderr) {
            $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            Write-Error $global:GhStubStderr
            $ErrorActionPreference = $eap
        }
        if ($global:GhStubStdout) { $global:GhStubStdout }
    }
}

function Remove-GhStub {
    if (Test-Path function:\gh) { Remove-Item function:\gh -ErrorAction SilentlyContinue }
    Remove-Variable -Name GhStubStdout, GhStubExitCode, GhStubStderr -Scope Global -ErrorAction SilentlyContinue
}

Describe 'mirror_existence probe' {

    BeforeEach {
        . $LibPath
    }

    AfterEach {
        Remove-GhStub
    }

    Context 'Schema' {

        It 'data shape includes mirror_exists, last_sync, target_path' {
            $r = New-ProbeResult -Probe 'mirror_existence' -Status 'ok' -Data @{
                mirror_exists = $true
                last_sync = '2026-05-01T00:00:00Z'
                target_path = 'owner/quiet-shift'
            }
            $r.data.ContainsKey('mirror_exists') | Should Be $true
            $r.data.ContainsKey('last_sync')     | Should Be $true
            $r.data.ContainsKey('target_path')   | Should Be $true
        }

        It 'preserves not_found status when target repo absent' {
            $r = New-ProbeResult -Probe 'mirror_existence' -Status 'not_found' -Data @{
                mirror_exists = $false
                last_sync = ''
                target_path = 'x/y'
            }
            $r.status | Should Be 'not_found'
            $r.data.mirror_exists | Should Be $false
        }
    }

    Context 'Script structure' {

        It 'script parses cleanly' {
            (Test-Path $ProbePath) | Should Be $true
            $errors = $null
            [System.Management.Automation.PSParser]::Tokenize((Get-Content $ProbePath -Raw), [ref]$errors) | Out-Null
            $errors.Count | Should Be 0
        }

        It 'requires both SourceProj and Repo' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[Parameter\(Mandatory\)\]\s*\[string\]\$SourceProj') | Should Be $true
            ($content -match '\[Parameter\(Mandatory\)\]\s*\[string\]\$Repo') | Should Be $true
        }

        It 'targets the repos/<owner/repo> endpoint' {
            $content = Get-Content $ProbePath -Raw
            ($content -match 'repos/\$Repo') | Should Be $true
        }
    }

    Context 'GitHub repo mapping (stubbed gh)' {

        It 'maps an existing repo: mirror_exists, last_sync=pushed_at, target_path=full_name' {
            Set-GhStub -ExitCode 0 -Stdout '{"id":1296269,"full_name":"octocat/Hello-World","pushed_at":"2026-01-01T00:00:00Z"}'
            $json = & $ProbePath -SourceProj 'octocat/source' -Repo 'octocat/Hello-World' -JsonlPath (Join-Path $TestDrive 'mi-200.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status            | Should Be 'ok'
            $obj.data.mirror_exists | Should Be $true
            $obj.data.last_sync     | Should Be '2026-01-01T00:00:00Z'
            $obj.data.target_path   | Should Be 'octocat/Hello-World'
        }

        It 'maps a missing repo: not_found, mirror_exists false' {
            Set-GhStub -ExitCode 1 -Stderr 'HTTP 404 Not Found'
            $json = & $ProbePath -SourceProj 'octocat/source' -Repo 'octocat/does-not-exist' -JsonlPath (Join-Path $TestDrive 'mi-404.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status             | Should Be 'not_found'
            $obj.data.mirror_exists | Should Be $false
            # target_path falls back to the input repo when no full_name returned
            $obj.data.target_path   | Should Be 'octocat/does-not-exist'
        }
    }
}

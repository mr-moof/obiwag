<#
.SYNOPSIS
    Pester 5 tests for tools/probes/runner_tags.ps1.

.DESCRIPTION
    Behavioral tests stub the native `gh` CLI. The probe re-dot-sources
    _lib.ps1, so mocking Invoke-GhApi directly is unreliable; stubbing `gh`
    survives the re-import because the probe never redefines `gh`.
#>
BeforeAll {


$LibPath   = Join-Path $PSScriptRoot '_lib.ps1'
$ProbePath = Join-Path $PSScriptRoot 'runner_tags.ps1'

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


}

Describe 'runner_tags probe' {

    BeforeEach {
        . $LibPath
    }

    AfterEach {
        Remove-GhStub
    }

    Context 'Schema' {

        It 'returns probe name runner_tags' {
            $r = New-ProbeResult -Probe 'runner_tags' -Status 'ok'
            $r.probe | Should -Be 'runner_tags'
        }

        It 'data shape includes available_tags and shared_runners' {
            $r = New-ProbeResult -Probe 'runner_tags' -Status 'ok' -Data @{available_tags = @('self-hosted'); shared_runners = $true}
            $r.data.ContainsKey('available_tags')  | Should -Be $true
            $r.data.ContainsKey('shared_runners') | Should -Be $true
            $r.data.available_tags[0]              | Should -Be 'self-hosted'
            $r.data.shared_runners                 | Should -Be $true
        }
    }

    Context 'Script structure' {

        It 'script file parses cleanly' {
            (Test-Path $ProbePath) | Should -Be $true
            $errors = $null
            [System.Management.Automation.PSParser]::Tokenize((Get-Content $ProbePath -Raw), [ref]$errors) | Out-Null
            $errors.Count | Should -Be 0
        }

        It 'requires string Repo' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[Parameter\(Mandatory\)\]\s*\[string\]\$Repo') | Should -Be $true
        }

        It 'targets repos/owner/repo/actions/runners endpoint' {
            $content = Get-Content $ProbePath -Raw
            ($content -match 'repos/\$Repo/actions/runners') | Should -Be $true
        }
    }

    Context 'GitHub runners mapping (stubbed gh)' {

        It 'collects sorted-unique label names and reports shared_runners false when runners exist' {
            Set-GhStub -ExitCode 0 -Stdout '{"total_count":2,"runners":[{"id":1,"name":"w1","status":"online","labels":[{"id":1,"name":"self-hosted","type":"read-only"},{"id":2,"name":"custom-build","type":"custom"}]},{"id":2,"name":"w2","status":"online","labels":[{"id":3,"name":"self-hosted","type":"read-only"},{"id":4,"name":"windows","type":"custom"}]}]}'
            $json = & $ProbePath -Repo 'octocat/Hello-World' -JsonlPath (Join-Path $TestDrive 'rt-on.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status               | Should -Be 'ok'
            $obj.data.shared_runners  | Should -Be $false
            # Sorted unique across both runners: custom-build, self-hosted, windows
            @($obj.data.available_tags).Count | Should -Be 3
            ($obj.data.available_tags -contains 'self-hosted') | Should -Be $true
            ($obj.data.available_tags -contains 'custom-build')  | Should -Be $true
            ($obj.data.available_tags -contains 'windows')     | Should -Be $true
        }

        It 'reports shared_runners true and empty tags when total_count is 0' {
            Set-GhStub -ExitCode 0 -Stdout '{"total_count":0,"runners":[]}'
            $json = & $ProbePath -Repo 'octocat/Hello-World' -JsonlPath (Join-Path $TestDrive 'rt-off.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.data.shared_runners          | Should -Be $true
            @($obj.data.available_tags).Count | Should -Be 0
        }
    }

}

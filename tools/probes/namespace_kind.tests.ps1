<#
.SYNOPSIS
    Pester 3.4 tests for tools/probes/namespace_kind.ps1.

.DESCRIPTION
    Tests the schema/shape, the script structure, and the GitHub user/org
    mapping. Behavioral tests stub the native `gh` CLI (the probe re-dot-sources
    _lib.ps1, so mocking Invoke-GhApi directly is unreliable; stubbing `gh`
    survives the re-import because the probe never redefines `gh`).
    Library functions carry their own unit tests in _lib.tests.ps1.
#>

$LibPath   = Join-Path $PSScriptRoot '_lib.ps1'
$ProbePath = Join-Path $PSScriptRoot 'namespace_kind.ps1'

# Install a `gh` stub that returns canned stdout (exit 0) or an error message
# on stderr (non-zero exit). Mirrors how the real `gh api` behaves so
# Invoke-GhApi / Resolve-ProbeStatus classify the result correctly.
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

Describe 'namespace_kind probe' {

    BeforeEach {
        . $LibPath
    }

    AfterEach {
        Remove-GhStub
    }

    Context 'Schema' {

        It 'returns probe name namespace_kind' {
            $r = New-ProbeResult -Probe 'namespace_kind' -Status 'ok' -InputData @{namespace = 'x'} -Data @{kind = 'User'}
            $r.probe | Should Be 'namespace_kind'
        }

        It 'data shape includes kind, id, full_path keys' {
            $r = New-ProbeResult -Probe 'namespace_kind' -Status 'ok' -Data @{kind='User'; id=1; full_path='octocat'}
            $r.data.ContainsKey('kind')      | Should Be $true
            $r.data.ContainsKey('id')        | Should Be $true
            $r.data.ContainsKey('full_path') | Should Be $true
        }
    }

    Context 'Probe-script integration' {

        It 'script file exists and parses without errors' {
            (Test-Path $ProbePath) | Should Be $true
            $errors = $null
            [System.Management.Automation.PSParser]::Tokenize((Get-Content $ProbePath -Raw), [ref]$errors) | Out-Null
            $errors.Count | Should Be 0
        }

        It 'script declares Mandatory Namespace param' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[Parameter\(Mandatory\)\]\s*\[string\]\$Namespace') | Should Be $true
        }

        It 'script dot-sources _lib.ps1' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\. \(Join-Path \$PSScriptRoot ''_lib\.ps1''\)') | Should Be $true
        }

        It 'script invokes Invoke-GhApi against the users endpoint' {
            $content = Get-Content $ProbePath -Raw
            ($content -match 'Invoke-GhApi -Path "users/') | Should Be $true
        }

        It 'script URI-encodes the namespace input' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[uri\]::EscapeDataString\(\$Namespace\)') | Should Be $true
        }
    }

    Context 'GitHub user/org mapping (stubbed gh)' {

        It 'maps a User account: kind=User, id, full_path=login' {
            Set-GhStub -Stdout '{"login":"octocat","id":583231,"type":"User"}' -ExitCode 0
            $json = & $ProbePath -Namespace octocat -JsonlPath (Join-Path $TestDrive 'ns-user.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status         | Should Be 'ok'
            $obj.data.kind      | Should Be 'User'
            $obj.data.id        | Should Be 583231
            $obj.data.full_path | Should Be 'octocat'
        }

        It 'maps an Organization account: kind=Organization' {
            Set-GhStub -Stdout '{"login":"github","id":9919,"type":"Organization"}' -ExitCode 0
            $json = & $ProbePath -Namespace github -JsonlPath (Join-Path $TestDrive 'ns-org.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.data.kind      | Should Be 'Organization'
            $obj.data.id        | Should Be 9919
            $obj.data.full_path | Should Be 'github'
        }

        It 'surfaces not_found and leaves kind empty on a 404' {
            Set-GhStub -ExitCode 1 -Stderr 'HTTP 404 Not Found'
            $json = & $ProbePath -Namespace nope-nope-nope -JsonlPath (Join-Path $TestDrive 'ns-404.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status    | Should Be 'not_found'
            $obj.data.kind | Should Be ''
        }
    }

    Context 'JSON output contract' {

        It 'New-ProbeResult input echoes the namespace' {
            $r = New-ProbeResult -Probe 'namespace_kind' -Status 'ok' -InputData @{namespace = 'octocat'}
            $r.input.namespace | Should Be 'octocat'
        }

        It 'kind defaults empty when API fails' {
            $r = New-ProbeResult -Probe 'namespace_kind' -Status 'auth_failure' -Data @{kind=''; id=$null; full_path=''} -ErrorText '401'
            $r.data.kind   | Should Be ''
            $r.status      | Should Be 'auth_failure'
            $r.error       | Should Be '401'
        }
    }
}

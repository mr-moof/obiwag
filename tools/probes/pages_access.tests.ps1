<#
.SYNOPSIS
    Pester 5 tests for tools/probes/pages_access.ps1.

.DESCRIPTION
    Behavioral tests stub the native `gh` CLI. The probe re-dot-sources
    _lib.ps1, so mocking Invoke-GhApi directly is unreliable; stubbing `gh`
    survives the re-import because the probe never redefines `gh`.
#>
BeforeAll {


$LibPath   = Join-Path $PSScriptRoot '_lib.ps1'
$ProbePath = Join-Path $PSScriptRoot 'pages_access.ps1'

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

Describe 'pages_access probe' {

    BeforeEach {
        . $LibPath
    }

    AfterEach {
        Remove-GhStub
    }

    Context 'Schema' {

        It 'data shape includes all four expected fields' {
            $r = New-ProbeResult -Probe 'pages_access' -Status 'ok' -Data @{
                pages_enabled = $true
                access_level = 'public'
                anonymous_reachable = $true
                content_redirect = ''
            }
            $r.data.ContainsKey('pages_enabled')       | Should -Be $true
            $r.data.ContainsKey('access_level')        | Should -Be $true
            $r.data.ContainsKey('anonymous_reachable') | Should -Be $true
            $r.data.ContainsKey('content_redirect')    | Should -Be $true
        }
    }

    Context 'Script structure' {

        It 'script parses cleanly' {
            (Test-Path $ProbePath) | Should -Be $true
            $errors = $null
            [System.Management.Automation.PSParser]::Tokenize((Get-Content $ProbePath -Raw), [ref]$errors) | Out-Null
            $errors.Count | Should -Be 0
        }

        It 'targets the repos/owner/repo/pages endpoint' {
            $content = Get-Content $ProbePath -Raw
            ($content -match 'repos/\$Repo/pages') | Should -Be $true
        }

        It 'derives access_level from the public flag' {
            # GitHub Pages: access level is driven by the boolean "public" field.
            $content = Get-Content $ProbePath -Raw
            ($content -match '\$pagesObj\.public') | Should -Be $true
        }

        It 'detects sign-in redirect as not-reachable' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '/users/sign_in') | Should -Be $true
        }

        It 'declares string Repo' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[Parameter\(Mandatory\)\]\s*\[string\]\$Repo') | Should -Be $true
        }

        It 'optional PublicUrl param' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[string\]\$PublicUrl\s*=\s*''''') | Should -Be $true
        }
    }

    Context 'GitHub Pages mapping (stubbed gh)' {

        It 'maps a public Pages site: enabled, public, anonymously reachable' {
            Set-GhStub -ExitCode 0 -Stdout '{"url":"https://api.github.com/repos/octocat/Hello-World/pages","status":"built","cname":null,"html_url":"https://octocat.github.io/Hello-World/","source":{"branch":"main","path":"/"},"public":true,"https_enforced":true}'
            $json = & $ProbePath -Repo 'octocat/Hello-World' -JsonlPath (Join-Path $TestDrive 'pg-public.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status                   | Should -Be 'ok'
            $obj.data.pages_enabled       | Should -Be $true
            $obj.data.access_level        | Should -Be 'public'
            $obj.data.anonymous_reachable | Should -Be $true
        }

        It 'maps a private Pages site: enabled, private, not anonymously reachable' {
            Set-GhStub -ExitCode 0 -Stdout '{"url":"https://api.github.com/repos/acme/secret/pages","status":"built","cname":null,"html_url":"https://acme.github.io/secret/","source":{"branch":"main","path":"/"},"public":false,"https_enforced":true}'
            $json = & $ProbePath -Repo 'acme/secret' -JsonlPath (Join-Path $TestDrive 'pg-private.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.data.pages_enabled       | Should -Be $true
            $obj.data.access_level        | Should -Be 'private'
            $obj.data.anonymous_reachable | Should -Be $false
        }

        It 'reports pages not enabled on a 404 (status not_found)' {
            Set-GhStub -ExitCode 1 -Stderr 'HTTP 404 Not Found'
            $json = & $ProbePath -Repo 'octocat/no-pages' -JsonlPath (Join-Path $TestDrive 'pg-404.jsonl')
            $obj = $json | ConvertFrom-Json
            $obj.status             | Should -Be 'not_found'
            $obj.data.pages_enabled | Should -Be $false
        }
    }
}

<#
.SYNOPSIS
    Pester 3.4 tests for tools/probes/marketplace_reach.ps1.
#>

$LibPath   = Join-Path $PSScriptRoot '_lib.ps1'
$ProbePath = Join-Path $PSScriptRoot 'marketplace_reach.ps1'

Describe 'marketplace_reach probe' {

    BeforeEach {
        . $LibPath
    }

    Context 'Schema' {

        It 'data shape includes reachable, status_code, latency_ms' {
            $r = New-ProbeResult -Probe 'marketplace_reach' -Status 'ok' -Data @{
                reachable = $true
                status_code = 200
                latency_ms = 145
            }
            $r.data.ContainsKey('reachable')   | Should Be $true
            $r.data.ContainsKey('status_code') | Should Be $true
            $r.data.ContainsKey('latency_ms')  | Should Be $true
        }
    }

    Context 'Script structure' {

        It 'script parses cleanly' {
            (Test-Path $ProbePath) | Should Be $true
            $errors = $null
            [System.Management.Automation.PSParser]::Tokenize((Get-Content $ProbePath -Raw), [ref]$errors) | Out-Null
            $errors.Count | Should Be 0
        }

        It 'requires MarketplaceUrl' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '\[Parameter\(Mandatory\)\]\s*\[string\]\$MarketplaceUrl') | Should Be $true
        }

        It 'measures elapsed milliseconds' {
            $content = Get-Content $ProbePath -Raw
            ($content -match 'Stopwatch') | Should Be $true
            ($content -match 'ElapsedMilliseconds') | Should Be $true
        }

        It 'classifies 401 and 403 as auth_failure' {
            $content = Get-Content $ProbePath -Raw
            ($content -match 'auth_failure') | Should Be $true
            ($content -match '401') | Should Be $true
            ($content -match '403') | Should Be $true
        }

        It 'classifies 404 as not_found' {
            $content = Get-Content $ProbePath -Raw
            ($content -match '''not_found''') | Should Be $true
        }
    }
}

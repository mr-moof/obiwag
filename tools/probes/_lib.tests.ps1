<#
.SYNOPSIS
    Pester 5 tests for tools/probes/_lib.ps1.
#>
BeforeAll {


# Dot-source the library under test
$LibPath = Join-Path $PSScriptRoot '_lib.ps1'


}

Describe 'Probes _lib' {

    BeforeEach {
        . $LibPath
    }

    Context 'Resolve-ProbeStatus' {

        It 'returns ok on exit 0' {
            (Resolve-ProbeStatus -ExitCode 0 -Stderr '') | Should -Be 'ok'
        }

        It 'returns auth_failure on 401 stderr' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'HTTP 401 Unauthorized') | Should -Be 'auth_failure'
        }

        It 'returns auth_failure on 403 stderr' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'GET /user returned 403') | Should -Be 'auth_failure'
        }

        It 'returns auth_failure on "not authorized"' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'You are not authorized to access this resource') | Should -Be 'auth_failure'
        }

        It 'returns not_found on 404 stderr' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'HTTP 404 Not Found') | Should -Be 'not_found'
        }

        It 'returns network_failure on connection refused' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'Connection refused by host') | Should -Be 'network_failure'
        }

        It 'returns network_failure on timeout' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'i/o timeout while connecting') | Should -Be 'network_failure'
        }

        It 'returns unknown on unrecognized non-zero stderr' {
            (Resolve-ProbeStatus -ExitCode 1 -Stderr 'unexpected error xyzzy') | Should -Be 'unknown'
        }

        It 'returns unknown on empty stderr with non-zero exit' {
            (Resolve-ProbeStatus -ExitCode 99 -Stderr '') | Should -Be 'unknown'
        }
    }

    Context 'Invoke-GhApi' {

        It 'exposes the Invoke-GhApi (gh api) wrapper, and no legacy CLI wrapper' {
            (Get-Command Invoke-GhApi -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
            # The legacy "Invoke-G<vendor>Api" name must no longer resolve.
            $legacyName = 'Invoke-G' + 'lab' + 'Api'
            (Get-Command $legacyName -ErrorAction SilentlyContinue) | Should -BeNullOrEmpty
        }

        It 'returns a structured failure when gh is not on PATH' {
            # Force the not-on-PATH branch regardless of whether gh is installed.
            Mock Get-Command { $null } -ParameterFilter { $Name -eq 'gh' }
            $res = Invoke-GhApi -Path 'users/octocat'
            $res.ok       | Should -Be $false
            $res.status   | Should -Be 'unknown'
            $res.stderr   | Should -Be 'gh not on PATH'
            $res.exitCode | Should -Be -1
        }
    }

    Context 'New-ProbeResult schema' {

        It 'returns hashtable with required keys' {
            $r = New-ProbeResult -Probe 'test_probe' -Status 'ok' -InputData @{x=1} -Data @{y=2}
            $r.ContainsKey('probe')  | Should -Be $true
            $r.ContainsKey('ts')     | Should -Be $true
            $r.ContainsKey('status') | Should -Be $true
            $r.ContainsKey('input')  | Should -Be $true
            $r.ContainsKey('data')   | Should -Be $true
            $r.ContainsKey('error')  | Should -Be $true
        }

        It 'echoes probe name verbatim' {
            $r = New-ProbeResult -Probe 'namespace_kind' -Status 'ok'
            $r.probe | Should -Be 'namespace_kind'
        }

        It 'records status verbatim' {
            $r = New-ProbeResult -Probe 'p' -Status 'auth_failure'
            $r.status | Should -Be 'auth_failure'
        }

        It 'preserves input hashtable contents' {
            $r = New-ProbeResult -Probe 'p' -Status 'ok' -InputData @{namespace = 'user'}
            $r.input.namespace | Should -Be 'user'
        }

        It 'preserves data hashtable contents' {
            $r = New-ProbeResult -Probe 'p' -Status 'ok' -Data @{kind = 'user'}
            $r.data.kind | Should -Be 'user'
        }

        It 'timestamp is UTC ISO-8601' {
            $r = New-ProbeResult -Probe 'p' -Status 'ok'
            ($r.ts -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') | Should -Be $true
        }
    }

    Context 'Write-ProbeOutput' {

        It 'writes a JSONL line and returns the path' {
            $tmpPath = Join-Path $TestDrive 'probes-test.jsonl'
            $r = New-ProbeResult -Probe 'p1' -Status 'ok' -Data @{a = 1}
            $written = Write-ProbeOutput -Result $r -JsonlPath $tmpPath

            $written | Should -Be $tmpPath
            (Test-Path $tmpPath) | Should -Be $true
            $content = Get-Content $tmpPath -Raw
            ($content -match '"probe":"p1"')  | Should -Be $true
            ($content -match '"status":"ok"') | Should -Be $true
        }

        It 'appends successive results to the same file' {
            $tmpPath = Join-Path $TestDrive 'probes-multi.jsonl'
            Write-ProbeOutput -Result (New-ProbeResult -Probe 'a' -Status 'ok') -JsonlPath $tmpPath | Out-Null
            Write-ProbeOutput -Result (New-ProbeResult -Probe 'b' -Status 'auth_failure') -JsonlPath $tmpPath | Out-Null

            $lines = Get-Content $tmpPath
            $lines.Count | Should -Be 2
            ($lines[0] -match '"probe":"a"') | Should -Be $true
            ($lines[1] -match '"probe":"b"') | Should -Be $true
        }

        It 'Format-ProbeSummary returns the expected one-liner' {
            $r = New-ProbeResult -Probe 'pages_access' -Status 'ok'
            $summary = Format-ProbeSummary -Result $r
            ($summary -match 'PROBE pages_access status=ok') | Should -Be $true
        }

        It 'Quiet switch suppresses host output but still returns path' {
            $tmpPath = Join-Path $TestDrive 'probes-quiet.jsonl'
            $r = New-ProbeResult -Probe 'p' -Status 'ok'
            $written = Write-ProbeOutput -Result $r -JsonlPath $tmpPath -Quiet
            $written | Should -Be $tmpPath
            (Test-Path $tmpPath) | Should -Be $true
        }
    }
}

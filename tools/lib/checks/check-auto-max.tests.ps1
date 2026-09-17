<#
.SYNOPSIS
    Pester 5 tests for tools/lib/checks/check-auto-max.ps1 (Test-AutoMaxConfig function).
#>

Describe 'Test-AutoMaxConfig' {

    BeforeEach {
        $script:ScriptDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
        # Remap: $PSScriptRoot is tools/lib/checks; ScriptDir must be tools/
        $script:ScriptDir = Join-Path (Split-Path -Parent $PSScriptRoot) '..'
        $script:ScriptDir = (Resolve-Path $script:ScriptDir).Path

        # Dot-source the module under test (and its dependencies)
        . (Join-Path $ScriptDir 'lib\common.ps1')
        . (Join-Path $ScriptDir 'lib\checks\check-auto-max.ps1')

        $script:RepoRoot = Join-Path $TestDrive 'fakerepo'
        if (Test-Path $RepoRoot) { Remove-Item -LiteralPath $RepoRoot -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot '.obi') -Force | Out-Null
        $script:ConfigPath = Join-Path $RepoRoot '.obi\auto-max.yaml'

        # Verify loader is reachable
        $script:LoaderPath = Join-Path $ScriptDir 'load-auto-max-config.ps1'
        (Test-Path $LoaderPath) | Should -Be $true
    }

    Context 'Absent config file' {

        It 'reports valid when auto-max.yaml does not exist' {
            $result = Test-AutoMaxConfig
            $result.Valid | Should -Be $true
            $result.Errors.Count | Should -Be 0
        }
    }

    Context 'Valid config' {

        It 'reports valid for a well-formed config' {
            $body = @"
phase0:
  required: true
  peer_review: true
pipeline:
  max_iterations: 3
auto_memory:
  enabled: true
  confidence_threshold: 0.7
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $result = Test-AutoMaxConfig
            $result.Valid | Should -Be $true
            $result.Errors.Count | Should -Be 0
        }
    }

    Context 'Range violations' {

        It 'rejects pipeline.max_iterations out of range' {
            $body = @"
pipeline:
  max_iterations: 99
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $result = Test-AutoMaxConfig
            $result.Valid | Should -Be $false
            ($result.Errors -join "`n") -match 'out of range' | Should -Be $true
            ($result.Errors -join "`n") -match 'pipeline.max_iterations' | Should -Be $true
        }

        It 'rejects auto_memory.confidence_threshold out of range' {
            $body = @"
auto_memory:
  confidence_threshold: 1.5
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $result = Test-AutoMaxConfig
            $result.Valid | Should -Be $false
            ($result.Errors -join "`n") -match 'confidence_threshold' | Should -Be $true
            ($result.Errors -join "`n") -match 'out of range' | Should -Be $true
        }
    }

    Context 'Type violations' {

        It 'rejects string where boolean expected' {
            $body = @"
phase0:
  required: yes
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $result = Test-AutoMaxConfig
            $result.Valid | Should -Be $false
            ($result.Errors -join "`n") -match 'Wrong type' | Should -Be $true
        }
    }
}

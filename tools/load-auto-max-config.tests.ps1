<#
.SYNOPSIS
    Pester 3.4 tests for tools/load-auto-max-config.ps1.
#>

$ScriptPath = Join-Path $PSScriptRoot 'load-auto-max-config.ps1'

Describe 'load-auto-max-config' {

    BeforeEach {
        $script:RepoRoot = Join-Path $TestDrive 'fakerepo'
        if (Test-Path $RepoRoot) { Remove-Item -LiteralPath $RepoRoot -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot '.obi') -Force | Out-Null
        $script:ConfigPath = Join-Path $RepoRoot '.obi\auto-max.yaml'
    }

    Context 'Defaults when no config file' {

        It 'returns all default keys' {
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['phase0.required'] | Should Be $true
            $cfg['phase0.codex_review'] | Should Be $true
            $cfg['grep_gates.fail_fast'] | Should Be $true
            $cfg['probes.parallel'] | Should Be $false
            $cfg['pipeline.max_iterations'] | Should Be 3
            $cfg['auto_memory.enabled'] | Should Be $true
            $cfg['auto_memory.confidence_threshold'] | Should Be 0.7
        }
    }

    Context 'Override single value' {

        It 'overrides pipeline.max_iterations from auto-max.yaml' {
            $body = @"
pipeline:
  max_iterations: 5
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['pipeline.max_iterations'] | Should Be 5
            # Other defaults intact
            $cfg['phase0.required'] | Should Be $true
        }

        It 'overrides boolean values' {
            $body = @"
auto_memory:
  enabled: false
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['auto_memory.enabled'] | Should Be $false
        }

        It 'overrides float values' {
            $body = @"
auto_memory:
  confidence_threshold: 0.85
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['auto_memory.confidence_threshold'] | Should Be 0.85
        }
    }

    Context 'Override multiple values' {

        It 'merges multiple overrides correctly' {
            $body = @"
phase0:
  required: false
  codex_review: false
pipeline:
  max_iterations: 10
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['phase0.required'] | Should Be $false
            $cfg['phase0.codex_review'] | Should Be $false
            $cfg['pipeline.max_iterations'] | Should Be 10
            # Untouched defaults
            $cfg['grep_gates.fail_fast'] | Should Be $true
            $cfg['auto_memory.enabled'] | Should Be $true
        }
    }

    Context 'Unknown override keys' {

        It 'preserves unknown keys for forward compat' {
            $body = @"
future_block:
  some_key: hello
"@
            Set-Content -Path $ConfigPath -Value $body -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['future_block.some_key'] | Should Be 'hello'
        }
    }

    Context 'Type coercion' {

        It 'parses int as integer' {
            Set-Content -Path $ConfigPath -Value "pipeline:`n  max_iterations: 7" -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['pipeline.max_iterations'].GetType().Name | Should Be 'Int32'
        }

        It 'parses true/false as boolean' {
            Set-Content -Path $ConfigPath -Value "phase0:`n  required: false" -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['phase0.required'].GetType().Name | Should Be 'Boolean'
        }

        It 'parses float as double' {
            Set-Content -Path $ConfigPath -Value "auto_memory:`n  confidence_threshold: 0.95" -Encoding UTF8
            $cfg = & $ScriptPath -RepoRoot $RepoRoot
            $cfg['auto_memory.confidence_threshold'].GetType().Name | Should Be 'Double'
        }
    }
}

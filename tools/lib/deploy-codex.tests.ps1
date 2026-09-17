<# Pester 5 tests for the Codex deployment module's required runtime policy copy. #>
BeforeAll {
    . (Join-Path $PSScriptRoot 'common.ps1')
    . (Join-Path $PSScriptRoot 'deploy-common.ps1')
    . (Join-Path $PSScriptRoot 'deploy-codex.ps1')
}

Describe 'Deploy Codex runtime policy' {
    BeforeEach {
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()
        $script:RepoRoot = Join-Path $TempRoot 'obiwag-agents'
        $script:PhasesDir = Join-Path $RepoRoot 'phases'
        $script:PlatformsDir = Join-Path $RepoRoot 'platforms'
        $script:CodexSource = Join-Path $PlatformsDir 'codex'
        $script:HooksDir = Join-Path $RepoRoot 'hooks'
        $script:SkillsDir = Join-Path $RepoRoot 'skills'
        $script:ScriptDir = Join-Path $RepoRoot 'tools'
        $script:ToolsTarget = Join-Path $TempRoot 'obi-tools'
        $script:CodexTarget = Join-Path $TempRoot '.codex'
        foreach ($path in @(
            $RepoRoot, $PhasesDir, $CodexSource, $HooksDir, $SkillsDir, $ScriptDir,
            $ToolsTarget, $CodexTarget
        )) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
        Set-Content -LiteralPath (Join-Path $CodexSource 'AGENTS.md') -Value '# agents' -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $CodexSource 'obi.config.toml') `
            -Value 'model = "gpt-5.6-terra"' -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $CodexSource 'hooks.json') -Value '{}' -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $CodexSource 'validate-codex.py') `
            -Value 'raise SystemExit(0)' -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $ScriptDir 'tool.ps1') -Value '# tool' -Encoding UTF8
        $script:deployFailures = [System.Collections.Generic.List[object]]::new()
    }

    It 'routes phase-table.json to the trusted OBI_HOME sibling during Codex-only deployment' {
        $source = Join-Path $PhasesDir 'phase-table.json'
        Set-Content -LiteralPath $source -Value '{"schema_version":7}' -Encoding UTF8

        $output = Invoke-CodexDeploy -DryRun 6>&1

        $deployFailures.Count | Should -Be 0
        ($output -join "`n") | Should -Match ([regex]::Escape(
            "Would copy: $source -> $(Join-Path $ToolsTarget 'phases\phase-table.json')"
        ))
        ($output -join "`n") | Should -Match ([regex]::Escape(
            "Would copy: $(Join-Path $CodexSource 'obi.config.toml') -> $(Join-Path $CodexTarget 'obi.config.toml')"
        ))
    }

    It 'fails closed when the required phase-table.json source is missing' {
        Invoke-CodexDeploy -DryRun 6>&1 | Out-Null

        $deployFailures.Count | Should -Be 1
        $deployFailures[0].Stage | Should -Be 'Copy-SingleFile'
        $deployFailures[0].Detail | Should -Match 'Missing source: .*phase-table\.json'
    }
}

<#
.SYNOPSIS
    Pester tests for lib/deploy-common.ps1 (OPT-12, #186).

.DESCRIPTION
    Split out of tools/deploy.tests.ps1. Exercises the REAL Copy-SingleFile,
    Copy-DirectoryContents, and Add-DeployFailure from the extracted module
    (dot-sourced below) rather than re-declared copies. Compatible with
    Requires Pester 5. Uses isolated temp directories.
#>
BeforeAll {

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'deploy-common.ps1')

}

Describe 'Deploy Common Helpers' {

    BeforeEach {
        # Create isolated temp structure mimicking the repo + home
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()

        # Mock "repo" structure
        $script:RepoRoot = Join-Path $TempRoot 'obiwag-agents'
        $script:PhasesDir = Join-Path $RepoRoot 'phases'
        $script:OrchDir = Join-Path $RepoRoot 'orchestration'
        $script:OrchUtilDir = Join-Path $OrchDir 'utilities'
        $script:OrchAgentsDir = Join-Path $OrchDir 'agents'
        $script:PoliciesDir = Join-Path $RepoRoot 'policies'
        $script:DocsDir = Join-Path $RepoRoot 'docs'
        $script:SkillsDir = Join-Path $RepoRoot 'skills'
        $script:HooksDir = Join-Path $RepoRoot 'hooks'
        $script:HooksCoreDir = Join-Path $HooksDir 'core'
        $script:UsersDir = Join-Path $RepoRoot 'users'
        $script:PlatformsDir = Join-Path $RepoRoot 'platforms'
        $script:ClaudePlatformDir = Join-Path $PlatformsDir 'claude-code'
        $script:ClaudeAgentsSourceDir = Join-Path $ClaudePlatformDir 'agents'

        # Mock "home" structure
        $script:HomeDir = Join-Path $TempRoot 'home'
        $script:ClaudeTarget = Join-Path $HomeDir '.claude'
        $script:CommandsTarget = Join-Path $ClaudeTarget 'commands'
        $script:AgentsTarget = Join-Path $ClaudeTarget 'agents'

        # Create directories
        $dirs = @(
            $RepoRoot, $PhasesDir, $OrchDir, $OrchUtilDir, $OrchAgentsDir,
            $PoliciesDir, $DocsDir, $SkillsDir, $HooksDir, $HooksCoreDir,
            $UsersDir, $PlatformsDir, $ClaudePlatformDir, $ClaudeAgentsSourceDir,
            $HomeDir, $ClaudeTarget, $CommandsTarget, $AgentsTarget
        )
        foreach ($d in $dirs) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
        }

        # Create phase directories with command.md
        $script:PhaseNames = @(
            '01-discovery', '02-author', '03-simplify', '04-review',
            '05-integrate', '06-re-review', '07-readme',
            '08-readme-review', '09-release', '10-learning'
        )
        foreach ($phase in $PhaseNames) {
            $phaseDir = Join-Path $PhasesDir $phase
            New-Item -ItemType Directory -Path $phaseDir -Force | Out-Null
            Set-Content (Join-Path $phaseDir 'command.md') "# $phase command" -Encoding UTF8
        }

        # Fail-closed accumulator (issue #136) — the REAL Add-DeployFailure
        # (dot-sourced above) appends to this $script:-scoped list.
        $script:deployFailures = [System.Collections.Generic.List[object]]::new()
    }

    Context 'Copy-SingleFile' {

        It 'Copies file to destination' {
            $src = Join-Path $RepoRoot 'testfile.txt'
            $dst = Join-Path $ClaudeTarget 'testfile.txt'
            Set-Content $src 'hello' -Encoding UTF8

            $result = Copy-SingleFile -Source $src -Destination $dst
            $result | Should -Be $true
            (Test-Path $dst) | Should -Be $true
            (Get-Content $dst) | Should -Be 'hello'
        }

        It 'Creates parent directories if missing' {
            $src = Join-Path $RepoRoot 'testfile.txt'
            $dst = Join-Path $ClaudeTarget 'sub\deep\testfile.txt'
            Set-Content $src 'data' -Encoding UTF8

            Copy-SingleFile -Source $src -Destination $dst | Out-Null
            (Test-Path $dst) | Should -Be $true
        }

        It 'Returns false for missing source' {
            $result = Copy-SingleFile -Source 'C:\nonexistent\file.txt' -Destination (Join-Path $ClaudeTarget 'x.txt')
            $result | Should -Be $false
        }

        It 'Does not copy in DryRun mode' {
            $src = Join-Path $RepoRoot 'drytest.txt'
            $dst = Join-Path $ClaudeTarget 'drytest.txt'
            Set-Content $src 'content' -Encoding UTF8

            $result = Copy-SingleFile -Source $src -Destination $dst -DryRun
            $result | Should -Be $true
            (Test-Path $dst) | Should -Be $false
        }

        It 'Overwrites existing file' {
            $src = Join-Path $RepoRoot 'overwrite.txt'
            $dst = Join-Path $ClaudeTarget 'overwrite.txt'
            Set-Content $src 'new content' -Encoding UTF8
            Set-Content $dst 'old content' -Encoding UTF8

            Copy-SingleFile -Source $src -Destination $dst | Out-Null
            (Get-Content $dst) | Should -Be 'new content'
        }
    }

    Context 'Copy-DirectoryContents' {

        It 'Copies all files recursively' {
            $srcDir = Join-Path $RepoRoot 'srcdir'
            $dstDir = Join-Path $ClaudeTarget 'dstdir'
            New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
            New-Item -ItemType Directory -Path (Join-Path $srcDir 'sub') -Force | Out-Null
            Set-Content (Join-Path $srcDir 'a.txt') 'aaa' -Encoding UTF8
            Set-Content (Join-Path (Join-Path $srcDir 'sub') 'b.txt') 'bbb' -Encoding UTF8

            Copy-DirectoryContents -Source $srcDir -Destination $dstDir
            (Test-Path (Join-Path $dstDir 'a.txt')) | Should -Be $true
            (Test-Path (Join-Path (Join-Path $dstDir 'sub') 'b.txt')) | Should -Be $true
        }

        It 'Creates destination if it does not exist' {
            $srcDir = Join-Path $RepoRoot 'srcdir2'
            $dstDir = Join-Path $ClaudeTarget 'newdst'
            New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
            Set-Content (Join-Path $srcDir 'file.txt') 'data' -Encoding UTF8

            Copy-DirectoryContents -Source $srcDir -Destination $dstDir
            (Test-Path $dstDir) | Should -Be $true
            (Test-Path (Join-Path $dstDir 'file.txt')) | Should -Be $true
        }

        It 'Does nothing for missing source' {
            $dstDir = Join-Path $ClaudeTarget 'nodst'
            Copy-DirectoryContents -Source 'C:\nonexistent\dir' -Destination $dstDir
            (Test-Path $dstDir) | Should -Be $false
        }

        It 'Does not copy in DryRun mode' {
            $srcDir = Join-Path $RepoRoot 'drysrc'
            $dstDir = Join-Path $ClaudeTarget 'drydst'
            New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
            Set-Content (Join-Path $srcDir 'file.txt') 'data' -Encoding UTF8

            Copy-DirectoryContents -Source $srcDir -Destination $dstDir -DryRun
            (Test-Path $dstDir) | Should -Be $false
        }
    }

    Context 'Fail-closed behavior (issue #136)' {

        It 'Copy-SingleFile records a failure when source is missing' {
            $script:deployFailures.Clear()
            $result = Copy-SingleFile -Source (Join-Path $TestDrive 'does-not-exist.md') -Destination (Join-Path $CommandsTarget 'x.md')
            $result | Should -Be $false
            $script:deployFailures.Count | Should -Be 1
            $script:deployFailures[0].Stage | Should -Be 'Copy-SingleFile'
        }

        It 'Copy-SingleFile does NOT record a failure when -Optional is set' {
            $script:deployFailures.Clear()
            $result = Copy-SingleFile -Source (Join-Path $TestDrive 'does-not-exist.md') -Destination (Join-Path $CommandsTarget 'x.md') -Optional
            $result | Should -Be $false
            $script:deployFailures.Count | Should -Be 0
        }

        It 'Copy-SingleFile does NOT record a failure on successful copy' {
            $script:deployFailures.Clear()
            $src = Join-Path $PhasesDir '01-discovery\command.md'
            $dst = Join-Path $CommandsTarget 'discovery.md'
            $result = Copy-SingleFile -Source $src -Destination $dst
            $result | Should -Be $true
            $script:deployFailures.Count | Should -Be 0
        }

        It 'Copy-DirectoryContents records a failure when source is missing' {
            $script:deployFailures.Clear()
            Copy-DirectoryContents -Source (Join-Path $TestDrive 'missing-dir') -Destination (Join-Path $ClaudeTarget 'x')
            $script:deployFailures.Count | Should -Be 1
            $script:deployFailures[0].Stage | Should -Be 'Copy-DirectoryContents'
        }

        It 'Copy-DirectoryContents does NOT record a failure when -Optional is set' {
            $script:deployFailures.Clear()
            Copy-DirectoryContents -Source (Join-Path $TestDrive 'missing-dir') -Destination (Join-Path $ClaudeTarget 'x') -Optional
            $script:deployFailures.Count | Should -Be 0
        }

        It 'Copy-DirectoryContents succeeds with no failures when source exists' {
            $script:deployFailures.Clear()
            Copy-DirectoryContents -Source $PhasesDir -Destination (Join-Path $ClaudeTarget 'phases-copy')
            $script:deployFailures.Count | Should -Be 0
        }

        It 'Failure accumulator records Stage and Detail' {
            $script:deployFailures.Clear()
            Add-DeployFailure -Stage 'TestStage' -Detail 'test detail'
            $script:deployFailures.Count | Should -Be 1
            $script:deployFailures[0].Stage | Should -Be 'TestStage'
            $script:deployFailures[0].Detail | Should -Be 'test detail'
        }

        It 'Multiple failures accumulate in order' {
            $script:deployFailures.Clear()
            Copy-SingleFile -Source 'C:\missing-1.md' -Destination 'C:\x.md' | Out-Null
            Copy-SingleFile -Source 'C:\missing-2.md' -Destination 'C:\y.md' | Out-Null
            $script:deployFailures.Count | Should -Be 2
        }
    }

    Context 'Retired ~/.agents skill cleanup' {

        BeforeEach {
            $script:Retired = Join-Path $HomeDir '.agents\skills\codex-adversarial-review'
            New-Item -ItemType Directory -Path $Retired -Force | Out-Null
            Set-Content -Path (Join-Path $Retired 'SKILL.md') `
                -Value "---`nname: codex-adversarial-review`n---`n`nA single-purpose wrapper around the Codex CLI.`n" -Encoding UTF8
            Set-Content -Path (Join-Path $Retired 'invoke.py') `
                -Value "# codex --profile review exec --json -`nCODEX_TIMEOUT_SEC_OVERRIDE = '1800'`n" -Encoding UTF8
            Set-Content -Path (Join-Path $Retired '_codex_core.py') `
                -Value 'SIGNAL_SOURCE = "codex-adversarial-review"' -Encoding UTF8
        }

        It 'removes the matching retired Obi skill only' {
            $result = Remove-RetiredObiAgentSkills -UserProfileRoot $HomeDir
            $result | Should -Be $true
            (Test-Path -LiteralPath $Retired) | Should -Be $false
            $script:deployFailures.Count | Should -Be 0
        }

        It 'preserves the matching retired skill during dry-run' {
            $result = Remove-RetiredObiAgentSkills -UserProfileRoot $HomeDir -DryRun
            $result | Should -Be $true
            (Test-Path -LiteralPath $Retired) | Should -Be $true
            $script:deployFailures.Count | Should -Be 0
        }

        It 'preserves a same-named directory that lacks the Obi signature' {
            Set-Content -Path (Join-Path $Retired 'SKILL.md') -Value '# personal skill' -Encoding UTF8
            $result = Remove-RetiredObiAgentSkills -UserProfileRoot $HomeDir
            $result | Should -Be $false
            (Test-Path -LiteralPath $Retired) | Should -Be $true
            $script:deployFailures.Count | Should -Be 1
            $script:deployFailures[0].Stage | Should -Be 'RetiredSkillCleanup'
        }

        It 'fails closed for a spoofed name without the released wrapper markers' {
            Set-Content -Path (Join-Path $Retired 'invoke.py') -Value '# custom reviewer' -Encoding UTF8
            $result = Remove-RetiredObiAgentSkills -UserProfileRoot $HomeDir
            $result | Should -Be $false
            (Test-Path -LiteralPath $Retired) | Should -Be $true
            $script:deployFailures.Count | Should -Be 1
            $script:deployFailures[0].Stage | Should -Be 'RetiredSkillCleanup'
        }
    }

    Context 'Retired OBI_HOME tool cleanup' {

        BeforeEach {
            $script:ObiHome = Join-Path $TempRoot 'obi-tools'
            $script:ObiTools = Join-Path $ObiHome 'tools'
            New-Item -ItemType Directory -Path (Join-Path $ObiTools 'schemas') -Force | Out-Null
            Set-Content -Path (Join-Path $ObiTools 'codex-run.ps1') `
                -Value '# Run codex non-interactively with observable streaming' -Encoding UTF8
            Set-Content -Path (Join-Path $ObiTools 'codex-plan-prep.ps1') `
                -Value '# Run codex.cmd against a plan file outside any git repo' -Encoding UTF8
            Set-Content -Path (Join-Path $ObiTools 'schemas\codex-plan-critique.schema.json') `
                -Value '{"title":"Codex plan-critique findings"}' -Encoding UTF8
        }

        It 'removes only matching legacy runtime files' {
            $keep = Join-Path $ObiTools 'peer-review.ps1'
            Set-Content -Path $keep -Value '# canonical' -Encoding UTF8
            $result = Remove-RetiredObiTools -ToolsTarget $ObiHome
            $result | Should -Be $true
            (Test-Path -LiteralPath (Join-Path $ObiTools 'codex-run.ps1')) | Should -Be $false
            (Test-Path -LiteralPath (Join-Path $ObiTools 'codex-plan-prep.ps1')) | Should -Be $false
            (Test-Path -LiteralPath (Join-Path $ObiTools 'schemas\codex-plan-critique.schema.json')) | Should -Be $false
            (Test-Path -LiteralPath $keep) | Should -Be $true
            $script:deployFailures.Count | Should -Be 0
        }

        It 'preserves matching files during dry-run' {
            $result = Remove-RetiredObiTools -ToolsTarget $ObiHome -DryRun
            $result | Should -Be $true
            (Test-Path -LiteralPath (Join-Path $ObiTools 'codex-run.ps1')) | Should -Be $true
            (Test-Path -LiteralPath (Join-Path $ObiTools 'codex-plan-prep.ps1')) | Should -Be $true
        }

        It 'fails closed for a non-matching exact path' {
            Set-Content -Path (Join-Path $ObiTools 'codex-run.ps1') -Value '# user file' -Encoding UTF8
            $result = Remove-RetiredObiTools -ToolsTarget $ObiHome
            $result | Should -Be $false
            (Test-Path -LiteralPath (Join-Path $ObiTools 'codex-run.ps1')) | Should -Be $true
            $script:deployFailures.Stage | Should -Contain 'RetiredToolCleanup'
        }
    }
}

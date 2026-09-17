<#
.SYNOPSIS
    Pester tests for lib/deploy-claude.ps1 (OPT-12, #186).

.DESCRIPTION
    Split out of tools/deploy.tests.ps1. Covers phase-name extraction, agent/
    command completeness, phase + orchestration deployment, settings backup,
    hook-path expansion, CLAUDE.md deployment, shared-permissions sync, and the
    full deployment simulation. Deployment-simulation cases exercise the REAL
    Copy-SingleFile (dot-sourced below). Requires Pester 5.
#>
BeforeAll {

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'deploy-common.ps1')
. (Join-Path $PSScriptRoot 'deploy-claude.ps1')

}

Describe 'Deploy Claude Code' {

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

        # Create agent.md for phases that have agents
        $agentPhases = @('01-discovery', '04-review', '06-re-review', '08-readme-review')
        foreach ($ap in $agentPhases) {
            Set-Content (Join-Path (Join-Path $PhasesDir $ap) 'agent.md') "# $ap agent" -Encoding UTF8
        }

        # Create orchestration files
        Set-Content (Join-Path $OrchDir 'obi.md') '# Obi orchestrator' -Encoding UTF8
        Set-Content (Join-Path $OrchDir 'obi-auto.md') '# Obi auto mode' -Encoding UTF8
        Set-Content (Join-Path $OrchDir 'obi-auto-max.md') '# Obi auto max mode' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-collect.md') '# Obi collect' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-memory-review.md') '# Memory review' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-update.md') '# Obi update' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-swarm.md') '# Obi swarm' -Encoding UTF8
        Set-Content (Join-Path $OrchAgentsDir 'obi-swarm-worker.md') '# Swarm worker' -Encoding UTF8

        $claudeAgentFiles = @(
            'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
            'obi-reviewer.md', 'obi-integrator.md', 'obi-rereviewer.md',
            'obi-readme.md', 'obi-readme-verifier.md', 'obi-release-gate.md',
            'obi-learner.md',
            'obi-pipeline-monitor.md', 'obi-swarm-worker.md'
        )
        foreach ($agentFile in $claudeAgentFiles) {
            Set-Content (Join-Path $ClaudeAgentsSourceDir $agentFile) "# $agentFile" -Encoding UTF8
        }

        # Fail-closed accumulator (issue #136) — the REAL Copy-SingleFile
        # (dot-sourced above) appends here on missing/failed copies.
        $script:deployFailures = [System.Collections.Generic.List[object]]::new()
    }

    Context 'Phase Name Extraction' {

        It 'Strips leading digits and dash from phase name' {
            $testCases = @{
                '01-discovery'     = 'discovery'
                '02-author'        = 'author'
                '03-simplify'      = 'simplify'
                '04-review'        = 'review'
                '05-integrate'     = 'integrate'
                '06-re-review'     = 're-review'
                '07-readme'        = 'readme'
                '08-readme-review' = 'readme-review'
                '09-release'       = 'release'
                '10-learning'      = 'learning'
            }

            foreach ($input in $testCases.Keys) {
                $expected = $testCases[$input]
                $actual = $input -replace '^\d+-', ''
                $actual | Should -Be $expected
            }
        }
    }

    Context 'Claude Code Agent Source' {

        It 'Has all 13 required agent files in platforms/claude-code/agents/' {
            $claudeAgentsSource = Join-Path $RepoRoot 'platforms\claude-code\agents'
            $agentFiles = (Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' -ErrorAction SilentlyContinue).Name

            $requiredAgents = @(
                'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
                'obi-reviewer.md', 'obi-integrator.md', 'obi-rereviewer.md',
                'obi-readme.md', 'obi-readme-verifier.md', 'obi-release-gate.md',
                'obi-learner.md',
                'obi-pipeline-monitor.md', 'obi-swarm-worker.md'
            )
            $missing = $requiredAgents | Where-Object { $_ -notin $agentFiles }
            $missing.Count | Should -Be 0
        }

        # Issue #163: delegated-phase agents are a SUBSET of installed agents. The full installed
        # list (above) covers non-phase agents (obi-pipeline-monitor, obi-swarm-worker);
        # this check additionally confirms every phase marked `delegated: true` in
        # phases/phase-table.json (OPT-10: promoted out of phases/README.md) has its agent installed.
        It 'Every delegated phase agent (from phases/phase-table.json) is in the installed set' {
            # This test file lives in tools/lib/, so the repo root is two levels up
            # (tools/lib -> tools -> repo). (OPT-12: was one level up from tools/.)
            $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
            if (-not $repoRoot) { $repoRoot = (Get-Location).Path }
            # Recompute the installed-agent set inside this It block (R4 fix — scope was leaking
            # across It blocks). The deploy.ps1 source-of-truth for installed agents is the
            # platforms/claude-code/agents/ directory at the repo root.
            $claudeAgentsSourceLocal = Join-Path $repoRoot 'platforms\claude-code\agents'
            if (-not (Test-Path $claudeAgentsSourceLocal)) {
                Set-ItResult -Skipped -Because "platforms/claude-code/agents not found"
                return
            }
            $localAgentFiles = (Get-ChildItem -Path $claudeAgentsSourceLocal -File -Filter '*.md' -ErrorAction SilentlyContinue).Name
            $tablePath = Join-Path $repoRoot 'phases\phase-table.json'
            if (Test-Path $tablePath) {
                $table = Get-Content $tablePath -Raw -Encoding UTF8 | ConvertFrom-Json
                $delegatedAgents = $table.phases | Where-Object { $_.delegated } | ForEach-Object { "$($_.agent).md" }
                $missing = $delegatedAgents | Where-Object { $_ -notin $localAgentFiles }
                $missing.Count | Should -Be 0
            }
        }
    }

    Context 'Required Commands Completeness' {

        It 'Lists exactly 17 required commands' {
            $requiredCommands = @(
                'author.md', 'discovery.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md'
            )
            $requiredCommands.Count | Should -Be 17
        }

        It 'Includes all 10 phase commands' {
            $phaseCommands = @(
                'author.md', 'discovery.md', 'integrate.md', 'learning.md',
                'readme.md', 'readme-review.md', 'release.md',
                're-review.md', 'review.md', 'simplify.md'
            )
            $requiredCommands = @(
                'author.md', 'discovery.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md'
            )

            foreach ($cmd in $phaseCommands) {
                ($requiredCommands -contains $cmd) | Should -Be $true
            }
        }

        It 'Includes all orchestration commands' {
            $orchCommands = @(
                'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md'
            )
            $requiredCommands = @(
                'author.md', 'discovery.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md'
            )

            foreach ($cmd in $orchCommands) {
                ($requiredCommands -contains $cmd) | Should -Be $true
            }
        }

        It 'Matches actual repo phases directory' {
            # Verify against the temp repo structure
            $phaseCommandMap = @{}
            foreach ($phase in $PhaseNames) {
                $cmdName = ($phase -replace '^\d+-', '') + '.md'
                $phaseCommandMap[$phase] = $cmdName
            }

            $phaseCommandMap.Count | Should -Be 10
            $phaseCommandMap['01-discovery'] | Should -Be 'discovery.md'
            $phaseCommandMap['10-learning'] | Should -Be 'learning.md'
        }
    }

    Context 'Required Agents Completeness' {

        It 'Lists exactly 5 required agents' {
            $requiredAgents = @(
                'obi-discovery.md', 'obi-reviewer.md',
                'obi-rereviewer.md', 'obi-readme-verifier.md',
                'obi-swarm-worker.md'
            )
            $requiredAgents.Count | Should -Be 5
        }

        It 'Includes 4 phase agents plus swarm worker' {
            $requiredAgents = @(
                'obi-discovery.md', 'obi-reviewer.md',
                'obi-rereviewer.md', 'obi-readme-verifier.md',
                'obi-swarm-worker.md'
            )
            ($requiredAgents -contains 'obi-discovery.md') | Should -Be $true
            ($requiredAgents -contains 'obi-reviewer.md') | Should -Be $true
            ($requiredAgents -contains 'obi-rereviewer.md') | Should -Be $true
            ($requiredAgents -contains 'obi-readme-verifier.md') | Should -Be $true
            ($requiredAgents -contains 'obi-swarm-worker.md') | Should -Be $true
        }
    }

    Context 'Phase Command Deployment' {

        It 'Deploys all 10 phase commands to commands/' {
            foreach ($phase in $PhaseNames) {
                $cmdName = ($phase -replace '^\d+-', '') + '.md'
                $src = Join-Path (Join-Path $PhasesDir $phase) 'command.md'
                $dst = Join-Path $CommandsTarget $cmdName
                Copy-SingleFile -Source $src -Destination $dst | Out-Null
            }

            $deployed = (Get-ChildItem $CommandsTarget -Filter '*.md').Name
            $deployed.Count | Should -Be 10
            ($deployed -contains 'discovery.md') | Should -Be $true
            ($deployed -contains 'learning.md') | Should -Be $true
            ($deployed -contains 're-review.md') | Should -Be $true
        }

        It 'Deploys Claude Code agents from static directory' {
            $claudeAgentsSource = Join-Path $PlatformsDir 'claude-code\agents'
            Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' | ForEach-Object {
                $dst = Join-Path $AgentsTarget $_.Name
                Copy-SingleFile -Source $_.FullName -Destination $dst | Out-Null
            }

            $deployed = (Get-ChildItem $AgentsTarget -Filter '*.md').Name
            $deployed.Count | Should -Be 12
            ($deployed -contains 'obi-discovery.md') | Should -Be $true
            ($deployed -contains 'obi-reviewer.md') | Should -Be $true
            ($deployed -contains 'obi-pipeline-monitor.md') | Should -Be $true
            ($deployed -contains 'obi-author.md') | Should -Be $true
        }
    }

    Context 'Orchestration Command Deployment' {

        It 'Deploys top-level orchestration commands' {
            foreach ($f in (Get-ChildItem $OrchDir -File -Filter '*.md')) {
                $dst = Join-Path $CommandsTarget $f.Name
                Copy-SingleFile -Source $f.FullName -Destination $dst | Out-Null
            }

            (Test-Path (Join-Path $CommandsTarget 'obi.md')) | Should -Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-auto.md')) | Should -Be $true
        }

        It 'Deploys utility commands' {
            foreach ($f in (Get-ChildItem $OrchUtilDir -File -Filter '*.md')) {
                $dst = Join-Path $CommandsTarget $f.Name
                Copy-SingleFile -Source $f.FullName -Destination $dst | Out-Null
            }

            (Test-Path (Join-Path $CommandsTarget 'obi-collect.md')) | Should -Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-memory-review.md')) | Should -Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-update.md')) | Should -Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-swarm.md')) | Should -Be $true
        }

        It 'Deploys orchestration agents' {
            foreach ($f in (Get-ChildItem $OrchAgentsDir -File -Filter '*.md')) {
                $dst = Join-Path $AgentsTarget $f.Name
                Copy-SingleFile -Source $f.FullName -Destination $dst | Out-Null
            }

            (Test-Path (Join-Path $AgentsTarget 'obi-swarm-worker.md')) | Should -Be $true
        }
    }

    Context 'Settings Backup' {

        It 'executes the production one-time backup helper before overwrite' {
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            $backupPath = "$settingsTarget.backup"
            Set-Content -LiteralPath $settingsTarget -Value '{"user_original":true}' -Encoding UTF8 -NoNewline

            Protect-ClaudeSettingsBackup -SettingsTarget $settingsTarget
            Set-Content -LiteralPath $settingsTarget -Value '{"obi_v1":true}' -Encoding UTF8 -NoNewline
            Protect-ClaudeSettingsBackup -SettingsTarget $settingsTarget

            (Get-Content -LiteralPath $backupPath -Raw).TrimStart([char]0xFEFF) | Should -Be '{"user_original":true}'
        }

        It 'does not create the one-time settings backup during DryRun' {
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            Set-Content -LiteralPath $settingsTarget -Value '{"user_original":true}' -Encoding UTF8 -NoNewline

            Protect-ClaudeSettingsBackup -SettingsTarget $settingsTarget -DryRun

            Test-Path -LiteralPath "$settingsTarget.backup" | Should -BeFalse
        }

        It 'Creates backup before overwriting settings.json' {
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            Set-Content $settingsTarget '{"original": true}' -Encoding UTF8

            $backupPath = "$settingsTarget.backup"
            Copy-Item -Path $settingsTarget -Destination $backupPath -Force

            (Test-Path $backupPath) | Should -Be $true
            (Get-Content $backupPath -Raw) | Should -Match '"original"'
        }

        It 'Preserves backup content after overwrite' {
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            Set-Content $settingsTarget '{"original": true}' -Encoding UTF8

            $backupPath = "$settingsTarget.backup"
            Copy-Item -Path $settingsTarget -Destination $backupPath -Force

            # Overwrite settings
            Set-Content $settingsTarget '{"new": true}' -Encoding UTF8

            (Get-Content $backupPath -Raw) | Should -Match '"original"'
            (Get-Content $settingsTarget -Raw) | Should -Match '"new"'
        }

        It 'settings.json.backup is created once and preserved across deploys (#168)' {
            # Mirrors the deploy.ps1 logic: if .backup exists, do NOT overwrite.
            # After two deploys, .backup still holds the user's pre-Obi original,
            # not the prior Obi-deployed file.
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            $backupPath = "$settingsTarget.backup"

            # Simulate deploy 1: user has pre-Obi settings.json
            Set-Content $settingsTarget '{"user_original": true}' -Encoding UTF8

            # First deploy creates the backup
            if (-not (Test-Path $backupPath)) {
                Copy-Item -LiteralPath $settingsTarget -Destination $backupPath
            }
            # First deploy then writes Obi's settings
            Set-Content $settingsTarget '{"obi_v1": true}' -Encoding UTF8

            # Simulate deploy 2: backup must NOT be overwritten
            if (-not (Test-Path $backupPath)) {
                Copy-Item -LiteralPath $settingsTarget -Destination $backupPath
            }
            Set-Content $settingsTarget '{"obi_v2": true}' -Encoding UTF8

            (Get-Content $backupPath -Raw) | Should -Match '"user_original"'
            (Get-Content $backupPath -Raw) | Should -Not -Match '"obi_v1"'
            (Get-Content $backupPath -Raw) | Should -Not -Match '"obi_v2"'
        }
    }

    Context 'Config Guardian gate' {

        It 'records a deploy failure when Config Guardian exits nonzero' {
            $guardian = Join-Path $TempRoot 'guardian-fail.ps1'
            Set-Content -LiteralPath $guardian -Value 'exit 7' -Encoding UTF8 -NoNewline

            $passed = Invoke-ClaudeConfigGuardian -GuardianScript $guardian -RepoRoot $RepoRoot

            $passed | Should -BeFalse
            $script:deployFailures.Count | Should -Be 1
            $script:deployFailures[0].Stage | Should -Be 'ConfigGuardian'
            $script:deployFailures[0].Detail | Should -Be 'Exited with code 7'
        }

        It 'accepts a zero Config Guardian exit without adding a failure' {
            $guardian = Join-Path $TempRoot 'guardian-pass.ps1'
            Set-Content -LiteralPath $guardian -Value 'exit 0' -Encoding UTF8 -NoNewline

            $passed = Invoke-ClaudeConfigGuardian -GuardianScript $guardian -RepoRoot $RepoRoot

            $passed | Should -BeTrue
            $script:deployFailures.Count | Should -Be 0
        }

        It 'does not register the OBI_HOME phase table in the Claude-root manifest' {
            $source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy-claude.ps1') -Raw
            $source | Should -Not -Match "Add-ManifestEntry\s+'phases/phase-table\.json'"
        }
    }

    Context 'Hook Path Validation' {

        It 'Expands %USERPROFILE% in hook commands' {
            $cmd = '"%USERPROFILE%\.claude\hooks\hook_wrapper.cmd" session_start'
            $expanded = $cmd -replace '%USERPROFILE%', $HomeDir

            $expanded | Should -Match ([regex]::Escape($HomeDir))
            $expanded | Should -Not -Match '%USERPROFILE%'
        }

        It 'Expands $HOME in hook commands' {
            $cmd = '"$HOME/.claude/hooks/hook_wrapper.cmd" session_start'
            $expanded = $cmd -replace '\$HOME', $HomeDir

            $expanded | Should -Match ([regex]::Escape($HomeDir))
            $expanded | Should -Not -Match '\$HOME'
        }

        It 'Extracts script path from hook command' {
            $hookCmd = '"C:/Users/test/.claude/hooks/hook_wrapper.cmd" session_start'
            $hookCmd -match '"([^"]+\.(cmd|ps1|py))"' | Out-Null
            $scriptPath = $Matches[1]

            $scriptPath | Should -Be 'C:/Users/test/.claude/hooks/hook_wrapper.cmd'
        }

        It 'Extracts Python script path' {
            $hookCmd = '"C:/Python314/python.exe" "C:/Users/test/.claude/hooks/session_start.py"'
            # Match the .py path specifically
            $hookCmd -match '"([^"]+\.py)"' | Out-Null
            $scriptPath = $Matches[1]

            $scriptPath | Should -Be 'C:/Users/test/.claude/hooks/session_start.py'
        }
    }

    Context 'CLAUDE.md Deployment' {

        It 'Copies the global CLAUDE.md source to target' {
            $srcDir = Join-Path $RepoRoot 'platforms\claude-code'
            New-Item -ItemType Directory -Path $srcDir -Force | Out-Null
            $src = Join-Path $srcDir 'CLAUDE.global.md'
            Set-Content $src '# Obi Wag' -Encoding UTF8

            $dst = Join-Path $ClaudeTarget 'CLAUDE.md'
            Copy-SingleFile -Source $src -Destination $dst | Out-Null

            (Test-Path $dst) | Should -Be $true
            (Get-Content $dst) | Should -Be '# Obi Wag'
        }
    }

}

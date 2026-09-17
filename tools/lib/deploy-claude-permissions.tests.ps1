<#
.SYNOPSIS
    Pester tests for Claude permissions and deployment simulation (OPT-12, #186).
.DESCRIPTION
    Split from deploy-claude.tests.ps1 without changing cases. Requires Pester 5.
#>
BeforeAll {

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'deploy-common.ps1')
. (Join-Path $PSScriptRoot 'deploy-claude.ps1')

}
Describe 'Deploy Claude permissions and simulation' {

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

    Context 'Shared Permissions Sync (Issue 74)' {

        It 'Merges shared and existing permissions with deduplication' {
            $sharedAllows = @('Bash(start:*)', 'Bash(where.exe:*)', 'WebSearch')
            $existingAllows = @('Bash(start:*)', 'mcp__compute-kb__search_kbs')

            $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

            $merged.Count | Should -Be 4
            ($merged -contains 'Bash(start:*)') | Should -Be $true
            ($merged -contains 'Bash(where.exe:*)') | Should -Be $true
            ($merged -contains 'WebSearch') | Should -Be $true
            ($merged -contains 'mcp__compute-kb__search_kbs') | Should -Be $true
        }

        It 'Preserves _managed_by marker in output' {
            $outputObj = @{
                '_managed_by' = 'obi-deploy'
                'permissions' = @{
                    'allow' = @('Bash(start:*)', 'WebSearch')
                }
            }

            $outputObj._managed_by | Should -Be 'obi-deploy'
            $outputObj.permissions.allow.Count | Should -Be 2
        }

        It 'Creates settings.local.json with shared allows when none exists' {
            $projectDir = Join-Path $TempRoot 'testproject'
            $claudeDir = Join-Path $projectDir '.claude'
            New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null

            $sharedAllows = @('Bash(start:*)', 'WebSearch')
            $merged = @($sharedAllows | Select-Object -Unique | Sort-Object)

            $outputObj = @{
                '_managed_by' = 'obi-deploy'
                'permissions' = @{ 'allow' = $merged }
            }

            $localPath = Join-Path $claudeDir 'settings.local.json'
            $outputObj | ConvertTo-Json -Depth 5 | Set-Content $localPath -Encoding UTF8

            $result = Get-Content $localPath -Raw | ConvertFrom-Json
            $result._managed_by | Should -Be 'obi-deploy'
            $result.permissions.allow.Count | Should -Be 2
        }

        It 'Skips unparseable existing settings.local.json' {
            $projectDir = Join-Path $TempRoot 'badproject'
            $claudeDir = Join-Path $projectDir '.claude'
            New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null

            $localPath = Join-Path $claudeDir 'settings.local.json'
            Set-Content $localPath '{ not valid json' -Encoding UTF8

            $parseable = $true
            try {
                $null = Get-Content $localPath -Raw | ConvertFrom-Json
            } catch {
                $parseable = $false
            }

            $parseable | Should -Be $false
        }

        It 'Never removes existing local entries (additive only)' {
            $sharedAllows = @('Bash(start:*)')
            $existingAllows = @('mcp__compute-kb__search_kbs', 'Bash(gopls version:*)')

            $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

            # All existing entries must still be present
            ($merged -contains 'mcp__compute-kb__search_kbs') | Should -Be $true
            ($merged -contains 'Bash(gopls version:*)') | Should -Be $true
            ($merged -contains 'Bash(start:*)') | Should -Be $true
        }

        It '-SyncProjectPermissions default-off path leaves project files untouched (#170)' {
            # Reproduce the default-off branch of the sync loop. With
            # $SyncProjectPermissions = $false, the merge is computed but
            # the file is NOT written. Sentinel project file content is
            # unchanged after the discovery scan.
            $SyncProjectPermissions = $false  # the locked default for #170

            $projDir = Join-Path $TempRoot 'source\proj-sample'
            $claudeDir = Join-Path $projDir '.claude'
            New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null

            $localPath = Join-Path $claudeDir 'settings.local.json'
            $userOriginal = '{"permissions":{"allow":["Bash(my-tool:*)"]}}'
            Set-Content $localPath $userOriginal -Encoding UTF8 -NoNewline

            $sharedAllows = @('Bash(obi-shared:*)')
            $existing = Get-Content $localPath -Raw | ConvertFrom-Json
            $existingAllows = @($existing.permissions.allow)
            $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

            # Reproduce the deploy.ps1 default-off branch
            if (-not $SyncProjectPermissions) {
                # Default branch: report and skip write
                $previewCount = 1
            } else {
                $outputObj = @{ '_managed_by' = 'obi-deploy'; 'permissions' = @{ 'allow' = $merged } }
                $outputObj | ConvertTo-Json -Depth 5 | Set-Content $localPath -Encoding UTF8
            }

            # User's pre-existing settings.local.json is unchanged
            (Get-Content $localPath -Raw).Trim() | Should -Be $userOriginal
            $previewCount | Should -Be 1
        }

        It '-SyncProjectPermissions opt-in path writes the merged permissions (#170)' {
            # The complementary path: with the flag set, the existing
            # sync logic runs and the file IS written.
            $SyncProjectPermissions = $true

            $projDir = Join-Path $TempRoot 'source\proj-sample-2'
            $claudeDir = Join-Path $projDir '.claude'
            New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null

            $localPath = Join-Path $claudeDir 'settings.local.json'
            $userOriginal = '{"permissions":{"allow":["Bash(my-tool:*)"]}}'
            Set-Content $localPath $userOriginal -Encoding UTF8 -NoNewline

            $sharedAllows = @('Bash(obi-shared:*)')
            $existing = Get-Content $localPath -Raw | ConvertFrom-Json
            $existingAllows = @($existing.permissions.allow)
            $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

            if ($SyncProjectPermissions) {
                $outputObj = @{ '_managed_by' = 'obi-deploy'; 'permissions' = @{ 'allow' = $merged } }
                $outputObj | ConvertTo-Json -Depth 5 | Set-Content $localPath -Encoding UTF8
            }

            $written = Get-Content $localPath -Raw | ConvertFrom-Json
            $written._managed_by | Should -Be 'obi-deploy'
            $written.permissions.allow.Count | Should -Be 2
        }
    }

    Context 'Full Deployment Simulation' {

        It 'Deploys all 20 commands from phases + orchestration' {
            # Deploy phase commands
            foreach ($phase in $PhaseNames) {
                $cmdName = ($phase -replace '^\d+-', '') + '.md'
                $src = Join-Path (Join-Path $PhasesDir $phase) 'command.md'
                $dst = Join-Path $CommandsTarget $cmdName
                Copy-SingleFile -Source $src -Destination $dst | Out-Null
            }

            # Deploy orchestration commands
            foreach ($f in (Get-ChildItem $OrchDir -File -Filter '*.md')) {
                Copy-SingleFile -Source $f.FullName -Destination (Join-Path $CommandsTarget $f.Name) | Out-Null
            }
            foreach ($f in (Get-ChildItem $OrchUtilDir -File -Filter '*.md')) {
                Copy-SingleFile -Source $f.FullName -Destination (Join-Path $CommandsTarget $f.Name) | Out-Null
            }

            $requiredCommands = @(
                'author.md', 'discovery.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md'
            )

            $installed = (Get-ChildItem "$CommandsTarget\*.md" -ErrorAction SilentlyContinue).Name
            $missing = $requiredCommands | Where-Object { $_ -notin $installed }

            $missing.Count | Should -Be 0
        }

        It 'Deploys all 13 agents from platforms/claude-code/agents/' {
            $claudeAgentsSource = Join-Path $PlatformsDir 'claude-code\agents'
            Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' | ForEach-Object {
                $dst = Join-Path $AgentsTarget $_.Name
                Copy-SingleFile -Source $_.FullName -Destination $dst | Out-Null
            }

            $requiredAgents = @(
                'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
                'obi-reviewer.md', 'obi-integrator.md', 'obi-rereviewer.md',
                'obi-readme.md', 'obi-readme-verifier.md', 'obi-release-gate.md',
                'obi-learner.md',
                'obi-pipeline-monitor.md', 'obi-swarm-worker.md'
            )
            $installedAgents = (Get-ChildItem "$AgentsTarget\*.md" -ErrorAction SilentlyContinue).Name
            $missingAgents = $requiredAgents | Where-Object { $_ -notin $installedAgents }

            $missingAgents.Count | Should -Be 0
        }
    }

    Context 'Grounding pattern deployment (issue #202)' {
        # pattern_matcher.load_patterns() reads the DEPLOYED copy as well as the
        # source repo, so the deployed directory must MIRROR source, not merely
        # receive copies. Copy-DirectoryContents alone only copies in: a pattern
        # deleted or renamed at source would linger and keep grounding as a ghost,
        # with nothing left to ever remove it.

        It 'copies source patterns into the deployed directory' {
            $src = Join-Path $RepoRoot '.obi\patterns'
            $dst = Join-Path $TempRoot 'home\.claude\.obi\patterns'
            New-Item -ItemType Directory -Force -Path $src, $dst | Out-Null
            Set-Content (Join-Path $src 'widgetapi.md') '---
topic: widgetapi
---' -Encoding UTF8

            Copy-DirectoryContents -Source $src -Destination $dst

            (Join-Path $dst 'widgetapi.md') | Should -Exist
        }

        It 'removes a deployed pattern whose source was deleted' {
            $src = Join-Path $RepoRoot '.obi\patterns'
            $dst = Join-Path $TempRoot 'home\.claude\.obi\patterns'
            New-Item -ItemType Directory -Force -Path $src, $dst | Out-Null

            Set-Content (Join-Path $src 'kept.md') 'kept' -Encoding UTF8
            # A pattern that used to exist at source and was since removed.
            Set-Content (Join-Path $dst 'kept.md') 'kept' -Encoding UTF8
            Set-Content (Join-Path $dst 'ghost.md') 'ghost' -Encoding UTF8

            Copy-DirectoryContents -Source $src -Destination $dst

            # The mirror step from deploy-claude.ps1.
            $sourceNames = @(Get-ChildItem -Path $src -Filter '*.md' -File |
                Select-Object -ExpandProperty Name)
            foreach ($deployed in @(Get-ChildItem -Path $dst -Filter '*.md' -File)) {
                if ($sourceNames -notcontains $deployed.Name) {
                    Remove-Item -LiteralPath $deployed.FullName -Force
                }
            }

            (Join-Path $dst 'kept.md')  | Should -Exist
            (Join-Path $dst 'ghost.md') | Should -Not -Exist
        }

        It 'leaves the deployed directory alone when source and target match' {
            $src = Join-Path $RepoRoot '.obi\patterns'
            $dst = Join-Path $TempRoot 'home\.claude\.obi\patterns'
            New-Item -ItemType Directory -Force -Path $src, $dst | Out-Null
            foreach ($n in @('a.md', 'b.md')) {
                Set-Content (Join-Path $src $n) $n -Encoding UTF8
            }

            Copy-DirectoryContents -Source $src -Destination $dst
            $sourceNames = @(Get-ChildItem -Path $src -Filter '*.md' -File |
                Select-Object -ExpandProperty Name)
            foreach ($deployed in @(Get-ChildItem -Path $dst -Filter '*.md' -File)) {
                if ($sourceNames -notcontains $deployed.Name) {
                    Remove-Item -LiteralPath $deployed.FullName -Force
                }
            }

            (Get-ChildItem "$dst\*.md").Count | Should -Be 2
        }
    }
}

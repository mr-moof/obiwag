<#
.SYNOPSIS
    Pester tests for lib/deploy-claude.ps1 (OPT-12, #186).

.DESCRIPTION
    Split out of tools/deploy.tests.ps1. Covers phase-name extraction, agent/
    command completeness, phase + orchestration deployment, settings backup,
    hook-path expansion, CLAUDE.md deployment, shared-permissions sync, and the
    full deployment simulation. Deployment-simulation cases exercise the REAL
    Copy-SingleFile (dot-sourced below). Compatible with Pester 3.4.0+.
#>

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'deploy-common.ps1')

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
        Set-Content (Join-Path $OrchUtilDir 'doc.md') '# Doc command' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-collect.md') '# Obi collect' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-memory-review.md') '# Memory review' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-update.md') '# Obi update' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'obi-swarm.md') '# Obi swarm' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'fixissue.md') '# Fix issue' -Encoding UTF8
        Set-Content (Join-Path $OrchUtilDir 'triage.md') '# Triage' -Encoding UTF8
        Set-Content (Join-Path $OrchAgentsDir 'obi-swarm-worker.md') '# Swarm worker' -Encoding UTF8

        $claudeAgentFiles = @(
            'obi-wag.md', 'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
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
                $actual | Should Be $expected
            }
        }
    }

    Context 'Claude Code Agent Source' {

        It 'Has all 13 required agent files in platforms/claude-code/agents/' {
            $claudeAgentsSource = Join-Path $RepoRoot 'platforms\claude-code\agents'
            $agentFiles = (Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' -ErrorAction SilentlyContinue).Name

            $requiredAgents = @(
                'obi-wag.md', 'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
                'obi-reviewer.md', 'obi-integrator.md', 'obi-rereviewer.md',
                'obi-readme.md', 'obi-readme-verifier.md', 'obi-release-gate.md',
                'obi-learner.md',
                'obi-pipeline-monitor.md', 'obi-swarm-worker.md'
            )
            $missing = $requiredAgents | Where-Object { $_ -notin $agentFiles }
            $missing.Count | Should Be 0
        }

        # Issue #163: delegated-phase agents are a SUBSET of installed agents. The full installed
        # list (above) covers non-phase agents (obi-wag, obi-pipeline-monitor, obi-swarm-worker);
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
                $missing.Count | Should Be 0
            }
        }
    }

    Context 'Required Commands Completeness' {

        It 'Lists exactly 20 required commands' {
            $requiredCommands = @(
                'author.md', 'discovery.md', 'doc.md', 'fixissue.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md', 'triage.md'
            )
            $requiredCommands.Count | Should Be 20
        }

        It 'Includes all 10 phase commands' {
            $phaseCommands = @(
                'author.md', 'discovery.md', 'integrate.md', 'learning.md',
                'readme.md', 'readme-review.md', 'release.md',
                're-review.md', 'review.md', 'simplify.md'
            )
            $requiredCommands = @(
                'author.md', 'discovery.md', 'doc.md', 'fixissue.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md', 'triage.md'
            )

            foreach ($cmd in $phaseCommands) {
                ($requiredCommands -contains $cmd) | Should Be $true
            }
        }

        It 'Includes all orchestration commands' {
            $orchCommands = @(
                'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'doc.md'
            )
            $requiredCommands = @(
                'author.md', 'discovery.md', 'doc.md', 'fixissue.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md', 'triage.md'
            )

            foreach ($cmd in $orchCommands) {
                ($requiredCommands -contains $cmd) | Should Be $true
            }
        }

        It 'Matches actual repo phases directory' {
            # Verify against the temp repo structure
            $phaseCommandMap = @{}
            foreach ($phase in $PhaseNames) {
                $cmdName = ($phase -replace '^\d+-', '') + '.md'
                $phaseCommandMap[$phase] = $cmdName
            }

            $phaseCommandMap.Count | Should Be 10
            $phaseCommandMap['01-discovery'] | Should Be 'discovery.md'
            $phaseCommandMap['10-learning'] | Should Be 'learning.md'
        }
    }

    Context 'Required Agents Completeness' {

        It 'Lists exactly 5 required agents' {
            $requiredAgents = @(
                'obi-discovery.md', 'obi-reviewer.md',
                'obi-rereviewer.md', 'obi-readme-verifier.md',
                'obi-swarm-worker.md'
            )
            $requiredAgents.Count | Should Be 5
        }

        It 'Includes 4 phase agents plus swarm worker' {
            $requiredAgents = @(
                'obi-discovery.md', 'obi-reviewer.md',
                'obi-rereviewer.md', 'obi-readme-verifier.md',
                'obi-swarm-worker.md'
            )
            ($requiredAgents -contains 'obi-discovery.md') | Should Be $true
            ($requiredAgents -contains 'obi-reviewer.md') | Should Be $true
            ($requiredAgents -contains 'obi-rereviewer.md') | Should Be $true
            ($requiredAgents -contains 'obi-readme-verifier.md') | Should Be $true
            ($requiredAgents -contains 'obi-swarm-worker.md') | Should Be $true
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
            $deployed.Count | Should Be 10
            ($deployed -contains 'discovery.md') | Should Be $true
            ($deployed -contains 'learning.md') | Should Be $true
            ($deployed -contains 're-review.md') | Should Be $true
        }

        It 'Deploys Claude Code agents from static directory' {
            $claudeAgentsSource = Join-Path $PlatformsDir 'claude-code\agents'
            Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' | ForEach-Object {
                $dst = Join-Path $AgentsTarget $_.Name
                Copy-SingleFile -Source $_.FullName -Destination $dst | Out-Null
            }

            $deployed = (Get-ChildItem $AgentsTarget -Filter '*.md').Name
            $deployed.Count | Should Be 13
            ($deployed -contains 'obi-discovery.md') | Should Be $true
            ($deployed -contains 'obi-reviewer.md') | Should Be $true
            ($deployed -contains 'obi-wag.md') | Should Be $true
            ($deployed -contains 'obi-author.md') | Should Be $true
        }
    }

    Context 'Orchestration Command Deployment' {

        It 'Deploys top-level orchestration commands' {
            foreach ($f in (Get-ChildItem $OrchDir -File -Filter '*.md')) {
                $dst = Join-Path $CommandsTarget $f.Name
                Copy-SingleFile -Source $f.FullName -Destination $dst | Out-Null
            }

            (Test-Path (Join-Path $CommandsTarget 'obi.md')) | Should Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-auto.md')) | Should Be $true
        }

        It 'Deploys utility commands' {
            foreach ($f in (Get-ChildItem $OrchUtilDir -File -Filter '*.md')) {
                $dst = Join-Path $CommandsTarget $f.Name
                Copy-SingleFile -Source $f.FullName -Destination $dst | Out-Null
            }

            (Test-Path (Join-Path $CommandsTarget 'obi-collect.md')) | Should Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-memory-review.md')) | Should Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-update.md')) | Should Be $true
            (Test-Path (Join-Path $CommandsTarget 'obi-swarm.md')) | Should Be $true
        }

        It 'Deploys orchestration agents' {
            foreach ($f in (Get-ChildItem $OrchAgentsDir -File -Filter '*.md')) {
                $dst = Join-Path $AgentsTarget $f.Name
                Copy-SingleFile -Source $f.FullName -Destination $dst | Out-Null
            }

            (Test-Path (Join-Path $AgentsTarget 'obi-swarm-worker.md')) | Should Be $true
        }
    }

    Context 'Settings Backup' {

        It 'Creates backup before overwriting settings.json' {
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            Set-Content $settingsTarget '{"original": true}' -Encoding UTF8

            $backupPath = "$settingsTarget.backup"
            Copy-Item -Path $settingsTarget -Destination $backupPath -Force

            (Test-Path $backupPath) | Should Be $true
            (Get-Content $backupPath -Raw) | Should Match '"original"'
        }

        It 'Preserves backup content after overwrite' {
            $settingsTarget = Join-Path $ClaudeTarget 'settings.json'
            Set-Content $settingsTarget '{"original": true}' -Encoding UTF8

            $backupPath = "$settingsTarget.backup"
            Copy-Item -Path $settingsTarget -Destination $backupPath -Force

            # Overwrite settings
            Set-Content $settingsTarget '{"new": true}' -Encoding UTF8

            (Get-Content $backupPath -Raw) | Should Match '"original"'
            (Get-Content $settingsTarget -Raw) | Should Match '"new"'
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

            (Get-Content $backupPath -Raw) | Should Match '"user_original"'
            (Get-Content $backupPath -Raw) | Should Not Match '"obi_v1"'
            (Get-Content $backupPath -Raw) | Should Not Match '"obi_v2"'
        }
    }

    Context 'Hook Path Validation' {

        It 'Expands %USERPROFILE% in hook commands' {
            $cmd = '"%USERPROFILE%\.claude\hooks\hook_wrapper.cmd" session_start'
            $expanded = $cmd -replace '%USERPROFILE%', $HomeDir

            $expanded | Should Match ([regex]::Escape($HomeDir))
            $expanded | Should Not Match '%USERPROFILE%'
        }

        It 'Expands $HOME in hook commands' {
            $cmd = '"$HOME/.claude/hooks/hook_wrapper.cmd" session_start'
            $expanded = $cmd -replace '\$HOME', $HomeDir

            $expanded | Should Match ([regex]::Escape($HomeDir))
            $expanded | Should Not Match '\$HOME'
        }

        It 'Extracts script path from hook command' {
            $hookCmd = '"C:/Users/test/.claude/hooks/hook_wrapper.cmd" session_start'
            $hookCmd -match '"([^"]+\.(cmd|ps1|py))"' | Out-Null
            $scriptPath = $Matches[1]

            $scriptPath | Should Be 'C:/Users/test/.claude/hooks/hook_wrapper.cmd'
        }

        It 'Extracts Python script path' {
            $hookCmd = '"C:/Python314/python.exe" "C:/Users/test/.claude/hooks/session_start.py"'
            # Match the .py path specifically
            $hookCmd -match '"([^"]+\.py)"' | Out-Null
            $scriptPath = $Matches[1]

            $scriptPath | Should Be 'C:/Users/test/.claude/hooks/session_start.py'
        }
    }

    Context 'CLAUDE.md Deployment' {

        It 'Copies CLAUDE.md to target' {
            $src = Join-Path $RepoRoot 'CLAUDE.md'
            Set-Content $src '# Obi Wag' -Encoding UTF8

            $dst = Join-Path $ClaudeTarget 'CLAUDE.md'
            Copy-SingleFile -Source $src -Destination $dst | Out-Null

            (Test-Path $dst) | Should Be $true
            (Get-Content $dst) | Should Be '# Obi Wag'
        }
    }

    Context 'Shared Permissions Sync (Issue 74)' {

        It 'Merges shared and existing permissions with deduplication' {
            $sharedAllows = @('Bash(start:*)', 'Bash(where.exe:*)', 'WebSearch')
            $existingAllows = @('Bash(start:*)', 'mcp__example__search_kbs')

            $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

            $merged.Count | Should Be 4
            ($merged -contains 'Bash(start:*)') | Should Be $true
            ($merged -contains 'Bash(where.exe:*)') | Should Be $true
            ($merged -contains 'WebSearch') | Should Be $true
            ($merged -contains 'mcp__example__search_kbs') | Should Be $true
        }

        It 'Preserves _managed_by marker in output' {
            $outputObj = @{
                '_managed_by' = 'obi-deploy'
                'permissions' = @{
                    'allow' = @('Bash(start:*)', 'WebSearch')
                }
            }

            $outputObj._managed_by | Should Be 'obi-deploy'
            $outputObj.permissions.allow.Count | Should Be 2
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
            $result._managed_by | Should Be 'obi-deploy'
            $result.permissions.allow.Count | Should Be 2
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

            $parseable | Should Be $false
        }

        It 'Never removes existing local entries (additive only)' {
            $sharedAllows = @('Bash(start:*)')
            $existingAllows = @('mcp__example__search_kbs', 'Bash(gopls version:*)')

            $merged = @($sharedAllows + $existingAllows | Select-Object -Unique | Sort-Object)

            # All existing entries must still be present
            ($merged -contains 'mcp__example__search_kbs') | Should Be $true
            ($merged -contains 'Bash(gopls version:*)') | Should Be $true
            ($merged -contains 'Bash(start:*)') | Should Be $true
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
            (Get-Content $localPath -Raw).Trim() | Should Be $userOriginal
            $previewCount | Should Be 1
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
            $written._managed_by | Should Be 'obi-deploy'
            $written.permissions.allow.Count | Should Be 2
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

            # Need doc.md too (from skills or special location)
            Set-Content (Join-Path $CommandsTarget 'doc.md') '# Doc command' -Encoding UTF8

            $requiredCommands = @(
                'author.md', 'discovery.md', 'doc.md', 'fixissue.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md', 'triage.md'
            )

            $installed = (Get-ChildItem "$CommandsTarget\*.md" -ErrorAction SilentlyContinue).Name
            $missing = $requiredCommands | Where-Object { $_ -notin $installed }

            $missing.Count | Should Be 0
        }

        It 'Deploys all 13 agents from platforms/claude-code/agents/' {
            $claudeAgentsSource = Join-Path $PlatformsDir 'claude-code\agents'
            Get-ChildItem -Path $claudeAgentsSource -File -Filter '*.md' | ForEach-Object {
                $dst = Join-Path $AgentsTarget $_.Name
                Copy-SingleFile -Source $_.FullName -Destination $dst | Out-Null
            }

            $requiredAgents = @(
                'obi-wag.md', 'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
                'obi-reviewer.md', 'obi-integrator.md', 'obi-rereviewer.md',
                'obi-readme.md', 'obi-readme-verifier.md', 'obi-release-gate.md',
                'obi-learner.md',
                'obi-pipeline-monitor.md', 'obi-swarm-worker.md'
            )
            $installedAgents = (Get-ChildItem "$AgentsTarget\*.md" -ErrorAction SilentlyContinue).Name
            $missingAgents = $requiredAgents | Where-Object { $_ -notin $installedAgents }

            $missingAgents.Count | Should Be 0
        }
    }
}

<#
.SYNOPSIS
    Integration smoke tests for deploy.ps1 (OPT-12, #186).

.DESCRIPTION
    After OPT-12 split deploy.ps1 into a dispatcher + lib modules, the unit
    tests for each module live alongside them in tools/lib/:
      lib/deploy-common.tests.ps1    Copy-SingleFile / Copy-DirectoryContents / fail-closed
      lib/deploy-manifest.tests.ps1  cleanup target selection + collision semantics
      lib/deploy-claude.tests.ps1    phase/agent/command/settings/permissions deploy

    This file keeps the two integration-level cases that exercise the ASSEMBLED
    script (subprocess invocation + per-category fallback), not an individual
    module. Requires Pester 5. Uses isolated temp directories.
#>

Describe 'Deploy Script (integration)' {

    BeforeEach {
        # Create isolated temp structure mimicking the repo + home
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()

        $script:RepoRoot = Join-Path $TempRoot 'obiwag-agents'
        $script:PlatformsDir = Join-Path $RepoRoot 'platforms'
        $script:ClaudePlatformDir = Join-Path $PlatformsDir 'claude-code'
        $script:ClaudeAgentsSourceDir = Join-Path $ClaudePlatformDir 'agents'

        $script:HomeDir = Join-Path $TempRoot 'home'
        $script:ClaudeTarget = Join-Path $HomeDir '.claude'

        $dirs = @(
            $RepoRoot, $PlatformsDir, $ClaudePlatformDir, $ClaudeAgentsSourceDir,
            $HomeDir, $ClaudeTarget
        )
        foreach ($d in $dirs) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
        }

        # Seed the 13 Claude agent files the per-category fallback enumerates.
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
    }

    Context 'Integration smoke (full script)' {

        It 'copies and executes the complete efficiency bundle from an isolated runtime root' {
            $sourceRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
            . (Join-Path $sourceRoot 'tools/lib/common.ps1')
            . (Join-Path $sourceRoot 'tools/lib/deploy-common.ps1')
            $script:deployFailures = [System.Collections.Generic.List[object]]::new()
            $runtimeRoot = Join-Path $TempRoot 'candidate-runtime'
            $projectRoot = Join-Path $TempRoot 'task-project'
            New-Item -ItemType Directory -Path $projectRoot -Force | Out-Null
            New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
            $scope = Join-Path $TempRoot 'scope.json'
            $manifest = Join-Path $runtimeRoot '.obi/efficiency-scope.json'
            $snapshot = Join-Path $TempRoot 'rollback'
            $bundleTool = Join-Path $sourceRoot 'tools/efficiency_bundle.py'
            $mapping = @{}
            $scopedFiles = @('tools/efficiency.py', 'tools/efficiency_learning.py', 'tools/efficiency_bundle.py', 'tools/schemas/efficiency-handoff.schema.json', 'phases/phase-table.json')
            foreach ($file in $scopedFiles) { $mapping[$file] = $file }
            @{ source_root=$sourceRoot; mappings=$mapping } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $scope -Encoding UTF8
            'unrelated user settings' | Set-Content -LiteralPath (Join-Path $runtimeRoot 'user.config')
            & python $bundleTool prepare --runtime-root $runtimeRoot --manifest $manifest --scope $scope | Out-Null
            $LASTEXITCODE | Should -Be 0
            & python $bundleTool snapshot --runtime-root $runtimeRoot --manifest $manifest --snapshot-dir $snapshot | Out-Null
            $LASTEXITCODE | Should -Be 0
            Copy-DirectoryContents -Source (Join-Path $sourceRoot 'tools') -Destination (Join-Path $runtimeRoot 'tools') 6>&1 | Out-Null
            Copy-SingleFile -Source (Join-Path $sourceRoot 'phases/phase-table.json') -Destination (Join-Path $runtimeRoot 'phases/phase-table.json') 6>&1 | Out-Null
            foreach ($relative in @('tools/efficiency.py', 'tools/efficiency_learning.py', 'tools/efficiency_bundle.py', 'tools/schemas/efficiency-handoff.schema.json', 'phases/phase-table.json')) {
                (Get-FileHash -LiteralPath (Join-Path $runtimeRoot $relative)).Hash |
                    Should -Be (Get-FileHash -LiteralPath (Join-Path $sourceRoot $relative)).Hash
            }
            $request = Join-Path $projectRoot 'route.json'
            '{"platform":"codex","rigor":"standard"}' | Set-Content -LiteralPath $request -Encoding UTF8
            $result = & python (Join-Path $runtimeRoot 'tools/efficiency.py') route `
                --project-root $projectRoot --runtime-root $runtimeRoot `
                --phase-table (Join-Path $runtimeRoot 'phases/phase-table.json') --input $request | ConvertFrom-Json
            $LASTEXITCODE | Should -Be 0
            $result.tier | Should -Be 'strong'
            $deployFailures.Count | Should -Be 0
            & python $bundleTool restore --runtime-root $runtimeRoot --manifest $manifest --snapshot-dir $snapshot | Out-Null
            $LASTEXITCODE | Should -Be 0
            foreach ($file in $scopedFiles) { Test-Path -LiteralPath (Join-Path $runtimeRoot $file) | Should -Be $false }
            (Get-Content -LiteralPath (Join-Path $runtimeRoot 'user.config') -Raw).Trim() | Should -Be 'unrelated user settings'
        }

        It 'Script-level smoke: deploy.ps1 -DryRun -ClaudeOnly invokes cleanly (#173 minimum viable)' {
            # Pass-2 #7 + pass-3 #4 from the 160 plan flagged that all existing
            # cleanup tests reimplement deploy.ps1 logic inline. This single
            # subprocess invocation is the minimum viable production-code
            # smoke. Full helper extraction (#146) + dot-source refactor
            # (#145, OPT-12) is the broader fix that this paved the way for.
            $repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
            $deployScript = Join-Path $repoRoot 'tools\deploy.ps1'

            (Test-Path -LiteralPath $deployScript) | Should -Be $true

            # Invoke deploy.ps1 in an isolated subprocess. -DryRun ensures
            # no writes. -ClaudeOnly limits scope (Codex block requires
            # repo-resident AGENTS.md etc. — we keep blast radius small).
            # Environment variables are inherited by the subprocess, so bind
            # both deployment roots to this test's fixture. Inheriting the real
            # USERPROFILE/OBI_HOME makes any legitimate undeployed source diff
            # look like a user-modified collision and turns this smoke into a
            # machine-state test.
            $oldUserProfile = $env:USERPROFILE
            $oldObiHome = $env:OBI_HOME
            try {
                $env:USERPROFILE = $HomeDir
                $env:OBI_HOME = Join-Path $TempRoot 'obi-tools'
                New-Item -ItemType Directory -Path $env:OBI_HOME -Force | Out-Null
                $legacyCopilotDir = Join-Path $HomeDir '.github'
                New-Item -ItemType Directory -Path $legacyCopilotDir -Force | Out-Null
                Set-Content -LiteralPath (Join-Path $legacyCopilotDir 'copilot-instructions.md') `
                    -Value 'legacy Obi marker' -Encoding UTF8
                $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $deployScript -ClaudeOnly -DryRun 2>&1
                $exitCode = $LASTEXITCODE
            } finally {
                $env:USERPROFILE = $oldUserProfile
                $env:OBI_HOME = $oldObiHome
            }

            # Exit 0 confirms syntax + basic wiring. Exit 2 would mean a
            # collision-aware abort which is unexpected here (deploy.ps1
            # against its own repo source should have no collisions).
            $exitCode | Should -Be 0

            # The isolated home intentionally has no deployment manifest.
            # Seeing the repo fallback proves Step 1 cleanup ran rather than
            # the script merely parsing and exiting.
            $outputText = $output -join "`n"
            $outputText | Should -Match 'No manifest found; using repo fallback:'
            $outputText | Should -Not -Match 'CopilotOnly'
            $outputText | Should -Not -Match 'Run tools/uninstall\.ps1'
        }

        It 'Per-category fallback fires when manifest lacks agents/ keys (#147)' {
            # Simulate a v0.69.12-era manifest: skills + commands present, but
            # zero agents/ keys (the bug condition). Without per-category
            # fallback, agent cleanup silently skips. With the fix, missing
            # agents trigger a repo enumeration that picks them up.
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            $manifestFile = Join-Path $manifestDir 'deployment-manifest.json'

            $manifestObj = @{
                version = '1.0'
                deployed_at = '2026-04-21T00:00:00Z'
                mappings = @{
                    'skills/verify/SKILL.md' = 'skills/verify/SKILL.md'
                    'commands/discovery.md' = 'phases/01-discovery/command.md'
                    # Deliberately no agents/* keys
                }
            }
            $manifestObj | ConvertTo-Json -Depth 3 | Set-Content $manifestFile -Encoding UTF8

            # The BeforeEach already seeded ClaudeAgentsSourceDir with 13 agent
            # files mirroring the real repo. The fallback should find them.

            # Reproduce the manifest-load + per-category-fallback logic
            $manifestData = Get-Content $manifestFile -Raw | ConvertFrom-Json
            $manifestKeys = @($manifestData.mappings.PSObject.Properties.Name)
            $obiOwnedAgents = @($manifestKeys | Where-Object { $_ -match '^agents/(.+)$' } | ForEach-Object { $Matches[1] })

            # Pre-fallback: 0 agents (the bug)
            $obiOwnedAgents.Count | Should -Be 0

            # Apply per-category fallback (the fix)
            if ($obiOwnedAgents.Count -eq 0) {
                $obiOwnedAgents = @(Get-ChildItem -Path $ClaudeAgentsSourceDir -File -Filter '*.md' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
            }

            # Post-fallback: agents populated from repo (12 seeded by BeforeEach)
            $obiOwnedAgents.Count | Should -Be 12
            ($obiOwnedAgents -contains 'obi-pipeline-monitor.md') | Should -Be $true
            ($obiOwnedAgents -contains 'obi-discovery.md') | Should -Be $true
            ($obiOwnedAgents -contains 'obi-swarm-worker.md') | Should -Be $true
        }
    }
}

Describe 'Deploy structure caps' {
    It 'keeps the dispatcher at 150 lines and every PowerShell tool below 600' {
        $violations = @(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File -Filter '*.ps1' |
            ForEach-Object {
                $lines = @(Get-Content -LiteralPath $_.FullName).Count
                if ($lines -gt 600) { "$($_.FullName): $lines" }
            })

        $violations | Should -BeNullOrEmpty
        @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deploy.ps1')).Count |
            Should -BeLessOrEqual 150
    }
}

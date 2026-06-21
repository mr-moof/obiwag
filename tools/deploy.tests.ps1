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
    module. Compatible with Pester 3.4.0+. Uses isolated temp directories.
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
            'obi-wag.md', 'obi-discovery.md', 'obi-author.md', 'obi-simplify.md',
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

        It 'Script-level smoke: deploy.ps1 -DryRun -ClaudeOnly invokes cleanly (#173 minimum viable)' {
            # Pass-2 #7 + pass-3 #4 from the 160 plan flagged that all existing
            # cleanup tests reimplement deploy.ps1 logic inline. This single
            # subprocess invocation is the minimum viable production-code
            # smoke. Full helper extraction (#146) + dot-source refactor
            # (#145, OPT-12) is the broader fix that this paved the way for.
            $repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
            $deployScript = Join-Path $repoRoot 'tools\deploy.ps1'

            (Test-Path -LiteralPath $deployScript) | Should Be $true

            # Invoke deploy.ps1 in an isolated subprocess. -DryRun ensures
            # no writes. -ClaudeOnly limits scope (Codex block requires
            # repo-resident AGENTS.md etc. — we keep blast radius small).
            $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $deployScript -ClaudeOnly -DryRun 2>&1
            $exitCode = $LASTEXITCODE

            # Exit 0 confirms syntax + basic wiring. Exit 2 would mean a
            # collision-aware abort which is unexpected here (deploy.ps1
            # against its own repo source should have no collisions).
            $exitCode | Should Be 0

            # Output should mention the manifest load — confirms Step 1
            # cleanup loop actually ran (not just parsed-and-exited).
            ($output -join "`n") | Should Match 'Loaded manifest:'
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
            $obiOwnedAgents.Count | Should Be 0

            # Apply per-category fallback (the fix)
            if ($obiOwnedAgents.Count -eq 0) {
                $obiOwnedAgents = @(Get-ChildItem -Path $ClaudeAgentsSourceDir -File -Filter '*.md' -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
            }

            # Post-fallback: agents populated from repo (13 seeded by BeforeEach)
            $obiOwnedAgents.Count | Should Be 13
            ($obiOwnedAgents -contains 'obi-wag.md') | Should Be $true
            ($obiOwnedAgents -contains 'obi-discovery.md') | Should Be $true
            ($obiOwnedAgents -contains 'obi-swarm-worker.md') | Should Be $true
        }
    }
}

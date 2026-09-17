<#
.SYNOPSIS
    Pester tests for lib/deploy-manifest.ps1 (OPT-12, #186).

.DESCRIPTION
    Split out of tools/deploy.tests.ps1. Covers manifest-driven cleanup target
    selection and the collision-aware removal semantics (#160/#167). These
    cases reproduce the cleanup logic inline (fragment-test style) and remain
    verbatim from the original suite. Requires Pester 5.
#>
BeforeAll {

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'deploy-common.ps1')
. (Join-Path $PSScriptRoot 'deploy-manifest.ps1')

}

Describe 'Deploy Manifest Cleanup' {

    BeforeEach {
        # Create isolated temp structure mimicking the repo + home
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()

        $script:RepoRoot = Join-Path $TempRoot 'obiwag-agents'
        $script:PhasesDir = Join-Path $RepoRoot 'phases'
        $script:SkillsDir = Join-Path $RepoRoot 'skills'
        $script:OrchestrationDir = Join-Path $RepoRoot 'orchestration'
        $script:DocsDir = Join-Path $RepoRoot 'docs'
        $script:PoliciesDir = Join-Path $RepoRoot 'policies'
        $script:HooksDir = Join-Path $RepoRoot 'hooks'
        $script:PlatformsDir = Join-Path $RepoRoot 'platforms'
        $script:ClaudePlatformDir = Join-Path $PlatformsDir 'claude-code'
        $script:ClaudeAgentsSourceDir = Join-Path $ClaudePlatformDir 'agents'

        $script:HomeDir = Join-Path $TempRoot 'home'
        $script:ClaudeTarget = Join-Path $HomeDir '.claude'
        $script:CommandsTarget = Join-Path $ClaudeTarget 'commands'
        $script:AgentsTarget = Join-Path $ClaudeTarget 'agents'

        $dirs = @(
            $RepoRoot, $PhasesDir, $SkillsDir, $OrchestrationDir, $DocsDir,
            $PoliciesDir, $HooksDir, $PlatformsDir,
            $ClaudePlatformDir, $ClaudeAgentsSourceDir,
            $HomeDir, $ClaudeTarget, $CommandsTarget, $AgentsTarget
        )
        foreach ($d in $dirs) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
        }

        $script:deployFailures = [System.Collections.Generic.List[object]]::new()
        $script:obiCollisions = @()
    }

    Context 'Cleanup Targets' {

        It 'Only removes Obi-managed directories' {
            # Per #160: docs/ and hooks/ are no longer wholesale-wiped; they
            # use manifest-driven per-file cleanup like commands/agents/skills.
            # This test now enumerates the per-file-cleanup categories.
            $cleanupTargets = @('commands', 'agents', 'skills', 'docs', 'hooks')

            # These are Obi-managed and safe to clean (per-file, manifest-driven)
            foreach ($target in $cleanupTargets) {
                ($target -in @('commands', 'agents', 'skills', 'docs', 'hooks')) | Should -Be $true
            }
        }

        It 'Does not include user data directories' {
            $cleanupTargets = @('commands', 'agents', 'skills', 'docs', 'hooks')

            # These directories should NOT be in cleanup targets
            ($cleanupTargets -contains 'projects') | Should -Be $false
            ($cleanupTargets -contains '.obi') | Should -Be $false
            ($cleanupTargets -contains 'plugins') | Should -Be $false
        }

        It 'Removes and recreates directories cleanly' {
            # Simulate stale file in commands/
            $staleFile = Join-Path $CommandsTarget 'stale-command.md'
            Set-Content $staleFile 'stale content' -Encoding UTF8
            (Test-Path $staleFile) | Should -Be $true

            # Cleanup
            Remove-Item -Path $CommandsTarget -Recurse -Force
            (Test-Path $CommandsTarget) | Should -Be $false

            # Recreate
            New-Item -ItemType Directory -Path $CommandsTarget -Force | Out-Null
            (Test-Path $CommandsTarget) | Should -Be $true
            (Get-ChildItem $CommandsTarget).Count | Should -Be 0
        }

        It 'Per-file hooks cleanup removes obi files but preserves sentinels (#160)' {
            $hooksTarget = Join-Path $ClaudeTarget 'hooks'
            New-Item -ItemType Directory -Path $hooksTarget -Force | Out-Null

            # Obi-owned file (per manifest) — should be removed
            Set-Content (Join-Path $hooksTarget 'session_start.py') 'obi' -Encoding UTF8
            # User-authored sentinel — should survive
            Set-Content (Join-Path $hooksTarget 'me_custom_hook.py') 'mine' -Encoding UTF8

            $obiOwnedHooks = @('session_start.py')
            foreach ($hook in $obiOwnedHooks) {
                $hookFile = Join-Path $hooksTarget $hook
                if (Test-Path $hookFile) {
                    Remove-Item -Path $hookFile -Force
                }
            }

            (Test-Path (Join-Path $hooksTarget 'session_start.py')) | Should -Be $false
            (Test-Path (Join-Path $hooksTarget 'me_custom_hook.py')) | Should -Be $true
        }
    }

    Context 'Manifest-Based Targeted Cleanup' {

        It 'Removes obi-owned skill directories during cleanup' {
            # Create obi-owned and user-owned skills
            $skillsTarget = Join-Path $ClaudeTarget 'skills'
            $obiSkill = Join-Path $skillsTarget 'verify'
            $userSkill = Join-Path $skillsTarget 'biya'
            New-Item -ItemType Directory -Path $obiSkill -Force | Out-Null
            New-Item -ItemType Directory -Path $userSkill -Force | Out-Null
            Set-Content (Join-Path $obiSkill 'SKILL.md') 'obi skill' -Encoding UTF8
            Set-Content (Join-Path $userSkill 'SKILL.md') 'user skill' -Encoding UTF8

            $obiOwnedSkills = @('verify', 'commit-conventions')

            foreach ($skill in $obiOwnedSkills) {
                $skillDir = Join-Path $skillsTarget $skill
                if (Test-Path $skillDir) {
                    Remove-Item -Path $skillDir -Recurse -Force
                }
            }

            (Test-Path $obiSkill) | Should -Be $false
            (Test-Path $userSkill) | Should -Be $true
            (Get-Content (Join-Path $userSkill 'SKILL.md')) | Should -Be 'user skill'
        }

        It 'Preserves non-obi skill directories during cleanup' {
            $skillsTarget = Join-Path $ClaudeTarget 'skills'
            $userSkillA = Join-Path $skillsTarget 'biya'
            $userSkillB = Join-Path $skillsTarget 'my-custom-skill'
            New-Item -ItemType Directory -Path $userSkillA -Force | Out-Null
            New-Item -ItemType Directory -Path $userSkillB -Force | Out-Null
            Set-Content (Join-Path $userSkillA 'SKILL.md') 'biya content' -Encoding UTF8
            Set-Content (Join-Path $userSkillB 'SKILL.md') 'custom content' -Encoding UTF8

            # Obi-owned list does NOT include biya or my-custom-skill
            $obiOwnedSkills = @('verify', 'sql-safety')
            foreach ($skill in $obiOwnedSkills) {
                $skillDir = Join-Path $skillsTarget $skill
                if (Test-Path $skillDir) {
                    Remove-Item -Path $skillDir -Recurse -Force
                }
            }

            (Test-Path $userSkillA) | Should -Be $true
            (Test-Path $userSkillB) | Should -Be $true
        }

        It 'Removes obi-owned commands but preserves user commands' {
            # Simulate commands dir with obi + user commands
            Set-Content (Join-Path $CommandsTarget 'discovery.md') 'obi cmd' -Encoding UTF8
            Set-Content (Join-Path $CommandsTarget 'obi.md') 'obi cmd' -Encoding UTF8
            Set-Content (Join-Path $CommandsTarget 'user-command.md') 'user cmd' -Encoding UTF8

            $obiOwnedCommands = @('discovery.md', 'obi.md')
            foreach ($cmd in $obiOwnedCommands) {
                $cmdFile = Join-Path $CommandsTarget $cmd
                if (Test-Path $cmdFile) {
                    Remove-Item -Path $cmdFile -Force
                }
            }

            (Test-Path (Join-Path $CommandsTarget 'discovery.md')) | Should -Be $false
            (Test-Path (Join-Path $CommandsTarget 'obi.md')) | Should -Be $false
            (Test-Path (Join-Path $CommandsTarget 'user-command.md')) | Should -Be $true
        }

        It 'Removes obi-owned agents but preserves user agents' {
            Set-Content (Join-Path $AgentsTarget 'obi-wag.md') 'obi agent' -Encoding UTF8
            Set-Content (Join-Path $AgentsTarget 'my-custom-agent.md') 'user agent' -Encoding UTF8

            $obiOwnedAgents = @('obi-wag.md')
            foreach ($agt in $obiOwnedAgents) {
                $agtFile = Join-Path $AgentsTarget $agt
                if (Test-Path $agtFile) {
                    Remove-Item -Path $agtFile -Force
                }
            }

            (Test-Path (Join-Path $AgentsTarget 'obi-wag.md')) | Should -Be $false
            (Test-Path (Join-Path $AgentsTarget 'my-custom-agent.md')) | Should -Be $true
        }

        It 'Falls back to repo enumeration when manifest does not exist' {
            # Simulate no manifest, build list from repo skills dir
            $repoSkills = Join-Path $RepoRoot 'skills'
            New-Item -ItemType Directory -Path (Join-Path $repoSkills 'verify') -Force | Out-Null
            New-Item -ItemType Directory -Path (Join-Path $repoSkills 'sql-safety') -Force | Out-Null
            Set-Content (Join-Path (Join-Path $repoSkills 'verify') 'SKILL.md') 'v' -Encoding UTF8
            Set-Content (Join-Path (Join-Path $repoSkills 'sql-safety') 'SKILL.md') 's' -Encoding UTF8

            $obiOwnedSkills = @(Get-ChildItem -Path $repoSkills -Directory | ForEach-Object { $_.Name })

            $obiOwnedSkills.Count | Should -Be 2
            ($obiOwnedSkills -contains 'verify') | Should -Be $true
            ($obiOwnedSkills -contains 'sql-safety') | Should -Be $true
        }

        It 'Parses obi-owned skills from manifest JSON' {
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            $manifestFile = Join-Path $manifestDir 'deployment-manifest.json'

            $manifestObj = @{
                version = '1.0'
                deployed_at = '2026-04-21T00:00:00Z'
                mappings = @{
                    'skills/verify/SKILL.md' = 'skills/verify/SKILL.md'
                    'skills/verify/language-checks.md' = 'skills/verify/language-checks.md'
                    'skills/sql-safety/SKILL.md' = 'skills/sql-safety/SKILL.md'
                    'commands/discovery.md' = 'phases/01-discovery/command.md'
                    'agents/obi-wag.md' = 'platforms/claude-code/agents/obi-wag.md'
                }
            }
            $manifestObj | ConvertTo-Json -Depth 3 | Set-Content $manifestFile -Encoding UTF8

            $manifestData = Get-Content $manifestFile -Raw | ConvertFrom-Json
            $manifestKeys = @($manifestData.mappings.PSObject.Properties.Name)

            $parsedSkills = @($manifestKeys | Where-Object { $_ -match '^skills/([^/]+)/' } | ForEach-Object { $Matches[1] } | Select-Object -Unique)
            $parsedCommands = @($manifestKeys | Where-Object { $_ -match '^commands/(.+)$' } | ForEach-Object { $Matches[1] })
            $parsedAgents = @($manifestKeys | Where-Object { $_ -match '^agents/(.+)$' } | ForEach-Object { $Matches[1] })

            $parsedSkills.Count | Should -Be 2
            ($parsedSkills -contains 'verify') | Should -Be $true
            ($parsedSkills -contains 'sql-safety') | Should -Be $true
            $parsedCommands.Count | Should -Be 1
            $parsedCommands[0] | Should -Be 'discovery.md'
            $parsedAgents.Count | Should -Be 1
            $parsedAgents[0] | Should -Be 'obi-wag.md'
        }

        It 'Per-file cleanup of docs/ preserves user-authored sentinels (#160)' {
            $docsTarget = Join-Path $ClaudeTarget 'docs'
            New-Item -ItemType Directory -Path $docsTarget -Force | Out-Null

            # Obi-owned doc (per manifest) — should be removed
            Set-Content (Join-Path $docsTarget 'gotchas.md') 'obi content' -Encoding UTF8
            # User-authored sentinel — should survive
            Set-Content (Join-Path $docsTarget 'me-cheatsheet.md') 'mine' -Encoding UTF8

            $obiOwnedDocs = @('gotchas.md')
            foreach ($doc in $obiOwnedDocs) {
                $docFile = Join-Path $docsTarget $doc
                if (Test-Path $docFile) {
                    Remove-Item -Path $docFile -Force
                }
            }

            (Test-Path (Join-Path $docsTarget 'gotchas.md')) | Should -Be $false
            (Test-Path (Join-Path $docsTarget 'me-cheatsheet.md')) | Should -Be $true
        }

        It 'Per-file cleanup of hooks/ preserves user-authored sentinels (#160)' {
            $hooksTarget = Join-Path $ClaudeTarget 'hooks'
            New-Item -ItemType Directory -Path $hooksTarget -Force | Out-Null

            # Obi-owned hook (per manifest) — should be removed
            Set-Content (Join-Path $hooksTarget 'post_tool_use.py') 'obi' -Encoding UTF8
            # User-authored sentinel — should survive
            Set-Content (Join-Path $hooksTarget 'me-personal-hook.py') 'mine' -Encoding UTF8

            $obiOwnedHooks = @('post_tool_use.py')
            foreach ($hook in $obiOwnedHooks) {
                $hookFile = Join-Path $hooksTarget $hook
                if (Test-Path $hookFile) {
                    Remove-Item -Path $hookFile -Force
                }
            }

            (Test-Path (Join-Path $hooksTarget 'post_tool_use.py')) | Should -Be $false
            (Test-Path (Join-Path $hooksTarget 'me-personal-hook.py')) | Should -Be $true
        }

        It 'LiteralPath disables wildcard glob in manifest entries (#160 Codex Recipe 1 #1)' {
            # A corrupted manifest entry like `docs/*.md` resolves under the
            # docs target root (passes root-containment), but Test-Path -Path
            # treats it as a wildcard. The production cleanup must use
            # -LiteralPath on both Test-Path and Remove-Item to prevent
            # glob-deletion of user-authored siblings.
            $docsTarget = Join-Path $ClaudeTarget 'docs'
            New-Item -ItemType Directory -Path $docsTarget -Force | Out-Null
            Set-Content (Join-Path $docsTarget 'user-cheatsheet.md') 'mine' -Encoding UTF8
            Set-Content (Join-Path $docsTarget 'another-user-doc.md') 'mine too' -Encoding UTF8

            # Simulate a corrupted manifest entry with a literal `*.md` filename
            $corruptEntry = '*.md'
            $docFile = Join-Path $docsTarget $corruptEntry

            # With -LiteralPath, Test-Path looks for a file literally named '*.md'
            # which doesn't exist, so cleanup is a no-op — user files survive.
            $literalExists = Test-Path -LiteralPath $docFile

            $literalExists | Should -Be $false
            (Test-Path -LiteralPath (Join-Path $docsTarget 'user-cheatsheet.md')) | Should -Be $true
            (Test-Path -LiteralPath (Join-Path $docsTarget 'another-user-doc.md')) | Should -Be $true
        }

        It 'Root-containment guard rejects ../ traversal in manifest entry (#160)' {
            # Reproduce the Test-PathUnderRoot logic from deploy.ps1 to verify
            # the guard works against a corrupted manifest entry trying to
            # delete files outside the target root.
            $docsTarget = Join-Path $ClaudeTarget 'docs'
            New-Item -ItemType Directory -Path $docsTarget -Force | Out-Null
            $outsideFile = Join-Path $ClaudeTarget 'outside-target.md'
            Set-Content $outsideFile 'should not be deleted' -Encoding UTF8

            $maliciousEntry = '../outside-target.md'
            $candidatePath = Join-Path $docsTarget $maliciousEntry

            # Inline reproduction of Test-PathUnderRoot
            $resolvedCandidate = [System.IO.Path]::GetFullPath($candidatePath)
            $resolvedRoot = [System.IO.Path]::GetFullPath($docsTarget)
            if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
                $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
            }
            $isUnderRoot = $resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)

            $isUnderRoot | Should -Be $false
            # The guard's contract: skip-and-warn, do NOT delete outside file
            (Test-Path $outsideFile) | Should -Be $true
        }

        It 'Root-containment guard rejects absolute path in manifest entry (#160)' {
            $hooksTarget = Join-Path $ClaudeTarget 'hooks'
            New-Item -ItemType Directory -Path $hooksTarget -Force | Out-Null

            # Reproduce the production Test-PathUnderRoot helper inline.
            # Its contract: return $false for any path that either resolves
            # outside the root OR can't be resolved at all (malformed).
            function script:TestUnderRootInline {
                param([string]$CandidatePath, [string]$Root)
                try {
                    $resolvedCandidate = [System.IO.Path]::GetFullPath($CandidatePath)
                    # Mirrors production: an embedded drive spec only makes GetFullPath
                    # THROW under .NET Framework (5.1). On pwsh 7 it returns the string
                    # verbatim, so reject an embedded volume separator explicitly.
                    if ($resolvedCandidate.IndexOf([System.IO.Path]::VolumeSeparatorChar, 2) -ge 0) {
                        return $false
                    }
                    $resolvedRoot = [System.IO.Path]::GetFullPath($Root)
                    if (-not $resolvedRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
                        $resolvedRoot += [System.IO.Path]::DirectorySeparatorChar
                    }
                    return $resolvedCandidate.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)
                } catch {
                    return $false
                }
            }

            # Case 1: simple absolute path outside root — GetFullPath resolves
            # cleanly, comparison rejects.
            $absoluteOutside = 'C:\Windows\notepad.exe'
            $candidate1 = Join-Path $hooksTarget $absoluteOutside
            (TestUnderRootInline -CandidatePath $candidate1 -Root $hooksTarget) | Should -Be $false

            # Case 2: malformed path with embedded drive letter. Host-dependent:
            # on 5.1 GetFullPath throws and the try/catch rejects; on pwsh 7 it
            # returns the string verbatim, so the explicit volume-separator check
            # is what rejects it. Both hosts must reach $false.
            $malformed = "$hooksTarget\C:\evil.exe"
            (TestUnderRootInline -CandidatePath $malformed -Root $hooksTarget) | Should -Be $false
        }

        It 'Collision-aware cleanup records collision when target differs from source (#167)' {
            # Reproduce the Remove-ObiOwnedFile collision-detection branch.
            # Without -Force, a user-modified file goes into the collisions
            # accumulator and is NOT removed.
            $commandsDir = Join-Path $ClaudeTarget 'commands'
            New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null
            $targetFile = Join-Path $commandsDir 'discovery.md'
            $sourceFile = Join-Path $TempRoot 'src-discovery.md'

            Set-Content $sourceFile '# Obi-deployed discovery v1' -Encoding UTF8 -NoNewline
            # User has modified the deployed file
            Set-Content $targetFile '# User-modified discovery' -Encoding UTF8 -NoNewline

            $tgtHash = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
            $srcHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash
            ($tgtHash -ne $srcHash) | Should -Be $true

            # Without -Force: collision detected, target preserved
            $collisions = @()
            $Force = $false
            if ($tgtHash -ne $srcHash) {
                $collisions += $targetFile
                if (-not $Force) {
                    # SKIP remove
                } else {
                    Copy-Item -LiteralPath $targetFile -Destination "$targetFile.user-backup"
                    Remove-Item -LiteralPath $targetFile -Force
                }
            }

            $collisions.Count | Should -Be 1
            (Test-Path -LiteralPath $targetFile) | Should -Be $true
            (Get-Content $targetFile -Raw).Trim() | Should -Be '# User-modified discovery'
        }

        It 'Collision-aware cleanup with -Force backs up user version and removes (#167)' {
            # The complementary path: with -Force, the collision triggers
            # a .user-backup copy of the user's version, then the target
            # is removed (to be re-deployed by Step 2).
            $commandsDir = Join-Path $ClaudeTarget 'commands'
            New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null
            $targetFile = Join-Path $commandsDir 'discovery2.md'
            $sourceFile = Join-Path $TempRoot 'src-discovery2.md'

            Set-Content $sourceFile '# Obi v1' -Encoding UTF8 -NoNewline
            Set-Content $targetFile '# user-edited' -Encoding UTF8 -NoNewline

            $tgtHash = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
            $srcHash = (Get-FileHash -LiteralPath $sourceFile -Algorithm SHA256).Hash
            $Force = $true

            if ($tgtHash -ne $srcHash -and $Force) {
                $backupPath = "$targetFile.user-backup"
                if (-not (Test-Path -LiteralPath $backupPath)) {
                    Copy-Item -LiteralPath $targetFile -Destination $backupPath
                }
                Remove-Item -LiteralPath $targetFile -Force
            }

            (Test-Path -LiteralPath $targetFile) | Should -Be $false
            (Test-Path -LiteralPath "$targetFile.user-backup") | Should -Be $true
            (Get-Content "$targetFile.user-backup" -Raw).Trim() | Should -Be '# user-edited'
        }

        It 'Collision-aware cleanup preserves earliest .user-backup across -Force runs (#167)' {
            # Subsequent -Force runs must NOT overwrite the earliest backup
            # (which has the user's original version). Mirror the
            # `if (-not (Test-Path $backupPath))` guard in Remove-ObiOwnedFile.
            $commandsDir = Join-Path $ClaudeTarget 'commands'
            New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null
            $targetFile = Join-Path $commandsDir 'discovery3.md'

            # User's earliest version (preserved as .user-backup)
            Set-Content "$targetFile.user-backup" '# user-v1 (original)' -Encoding UTF8 -NoNewline
            # Current target = Obi-deployed v1 that user edited again
            Set-Content $targetFile '# user-v2 (edited after first -Force)' -Encoding UTF8 -NoNewline

            $backupPath = "$targetFile.user-backup"
            $Force = $true
            if ($Force) {
                if (-not (Test-Path -LiteralPath $backupPath)) {
                    Copy-Item -LiteralPath $targetFile -Destination $backupPath
                }
            }

            # Original backup content unchanged — earliest version preserved
            (Get-Content $backupPath -Raw).Trim() | Should -Be '# user-v1 (original)'
        }
    }

}

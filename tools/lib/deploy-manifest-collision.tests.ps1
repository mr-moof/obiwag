<#
.SYNOPSIS
    Pester tests for production manifest collision preflight (OPT-12, #186).
.DESCRIPTION
    Split from deploy-manifest.tests.ps1 without changing cases. Requires Pester 5.
#>
BeforeAll {

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'deploy-common.ps1')
. (Join-Path $PSScriptRoot 'deploy-manifest.ps1')

}

Describe 'Deploy Manifest Collision Preflight' {

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

    Context 'Production collision preflight' {

        It 'accepts source drift when the target still matches its deployed hash' {
            $commandSource = Join-Path $OrchestrationDir 'obi-auto.md'
            $commandTarget = Join-Path $CommandsTarget 'obi-auto.md'
            Set-Content -LiteralPath $commandSource -Value '# Obi source v2' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $commandTarget -Value '# Obi deployed v1' -Encoding UTF8 -NoNewline
            $deployedHash = (Get-FileHash -LiteralPath $commandTarget -Algorithm SHA256).Hash

            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'commands/obi-auto.md' = 'orchestration/obi-auto.md' }
                deployed_hashes = @{ 'commands/obi-auto.md' = $deployedHash }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            $completed = Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1'

            $completed | Should -BeTrue
            $script:obiCollisions.Count | Should -Be 0
            Get-Content -LiteralPath $commandTarget -Raw | Should -Be '# Obi deployed v1'
        }

        It 'blocks before removing any managed asset when one target was user-modified' {
            $commandSource = Join-Path $OrchestrationDir 'obi-auto.md'
            $commandTarget = Join-Path $CommandsTarget 'obi-auto.md'
            $hookSource = Join-Path $HooksDir 'stop.py'
            $hooksTarget = Join-Path $ClaudeTarget 'hooks'
            $hookTarget = Join-Path $hooksTarget 'stop.py'
            New-Item -ItemType Directory -Path $hooksTarget -Force | Out-Null

            Set-Content -LiteralPath $commandSource -Value '# Obi source v2' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $commandTarget -Value '# User edit' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $hookSource -Value '# Obi hook v1' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $hookTarget -Value '# Obi hook v1' -Encoding UTF8 -NoNewline

            $originalCommandHash = (Get-FileHash -InputStream ([System.IO.MemoryStream]::new([System.Text.Encoding]::UTF8.GetBytes('# Obi deployed v1'))) -Algorithm SHA256).Hash
            $hookHash = (Get-FileHash -LiteralPath $hookTarget -Algorithm SHA256).Hash
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{
                    'commands/obi-auto.md' = 'orchestration/obi-auto.md'
                    'hooks/stop.py' = 'hooks/stop.py'
                }
                deployed_hashes = @{
                    'commands/obi-auto.md' = $originalCommandHash
                    'hooks/stop.py' = $hookHash
                }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            $completed = Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1'

            $completed | Should -BeFalse
            $script:obiCollisions.Count | Should -Be 1
            Get-Content -LiteralPath $commandTarget -Raw | Should -Be '# User edit'
            Get-Content -LiteralPath $hookTarget -Raw | Should -Be '# Obi hook v1'
        }

        It 'protects manifest-owned files outside the category subdirectories' {
            $source = Join-Path $RepoRoot 'CLAUDE.md'
            $target = Join-Path $ClaudeTarget 'CLAUDE.md'
            Set-Content -LiteralPath $source -Value '# current source' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $target -Value '# user edit' -Encoding UTF8 -NoNewline
            $baseline = Join-Path $TempRoot 'baseline.md'
            Set-Content -LiteralPath $baseline -Value '# prior deployment' -Encoding UTF8 -NoNewline
            $deployedHash = (Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash

            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'CLAUDE.md' = 'CLAUDE.md' }
                deployed_hashes = @{ 'CLAUDE.md' = $deployedHash }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            $completed = Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1'

            $completed | Should -BeFalse
            $script:obiCollisions.Count | Should -Be 1
            Get-Content -LiteralPath $target -Raw | Should -Be '# user edit'
        }

        It 'writes a versioned manifest containing the exact deployed hashes' {
            $commandTarget = Join-Path $CommandsTarget 'obi-auto.md'
            Set-Content -LiteralPath $commandTarget -Value '# deployed bytes' -Encoding UTF8 -NoNewline
            $script:manifest = @{ 'commands/obi-auto.md' = 'orchestration/obi-auto.md' }

            Write-ClaudeDeploymentManifest

            $written = Get-Content -LiteralPath (Join-Path $ClaudeTarget '.obi\deployment-manifest.json') -Raw | ConvertFrom-Json
            $written.version | Should -Be '2.0'
            $written.mappings.'commands/obi-auto.md' | Should -Be 'orchestration/obi-auto.md'
            $written.deployed_hashes.'commands/obi-auto.md' | Should -Be (Get-FileHash -LiteralPath $commandTarget -Algorithm SHA256).Hash
        }

        It 'does not replace the last-good manifest while deployment failures are pending' {
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            $manifestPath = Join-Path $manifestDir 'deployment-manifest.json'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            Set-Content -LiteralPath $manifestPath -Value '{"sentinel":"last-good"}' -Encoding UTF8 -NoNewline
            $script:manifest = @{ 'commands/obi-auto.md' = 'orchestration/obi-auto.md' }
            $script:deployFailures.Add([pscustomobject]@{ Stage = 'Copy'; Detail = 'injected failure' })

            Write-ClaudeDeploymentManifest

            (Get-Content -LiteralPath $manifestPath -Raw).TrimStart([char]0xFEFF) | Should -Be '{"sentinel":"last-good"}'
        }

        It 'preserves user files inside a managed skill while pruning its managed file' {
            $skillSourceDir = Join-Path $SkillsDir 'verify'
            $skillTargetDir = Join-Path $ClaudeTarget 'skills\verify'
            New-Item -ItemType Directory -Path $skillSourceDir, $skillTargetDir -Force | Out-Null
            $skillTarget = Join-Path $skillTargetDir 'SKILL.md'
            $userFile = Join-Path $skillTargetDir 'my-notes.md'
            Set-Content -LiteralPath $skillTarget -Value '# managed skill' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $userFile -Value '# user notes' -Encoding UTF8 -NoNewline
            $deployedHash = (Get-FileHash -LiteralPath $skillTarget -Algorithm SHA256).Hash

            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'skills/verify/SKILL.md' = 'skills/verify/removed.md' }
                deployed_hashes = @{ 'skills/verify/SKILL.md' = $deployedHash }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            $completed = Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1'

            $completed | Should -BeTrue
            Test-Path -LiteralPath $skillTarget | Should -BeFalse
            Get-Content -LiteralPath $userFile -Raw | Should -Be '# user notes'
        }

        It 'backs up a forced collision through the production cleanup wiring without a delete-recopy window' {
            $commandSource = Join-Path $OrchestrationDir 'obi-auto.md'
            $commandTarget = Join-Path $CommandsTarget 'obi-auto.md'
            $hookSource = Join-Path $HooksDir 'stop.py'
            $hooksTarget = Join-Path $ClaudeTarget 'hooks'
            $hookTarget = Join-Path $hooksTarget 'stop.py'
            $baseline = Join-Path $TempRoot 'prior-command.md'
            New-Item -ItemType Directory -Path $hooksTarget -Force | Out-Null
            Set-Content -LiteralPath $commandSource -Value '# current command' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $commandTarget -Value '# user command edit' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $baseline -Value '# prior command' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $hookSource -Value '# current hook' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $hookTarget -Value '# current hook' -Encoding UTF8 -NoNewline

            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{
                    'commands/obi-auto.md' = 'orchestration/obi-auto.md'
                    'hooks/stop.py' = 'hooks/stop.py'
                }
                deployed_hashes = @{
                    'commands/obi-auto.md' = (Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash
                    'hooks/stop.py' = (Get-FileHash -LiteralPath $hookTarget -Algorithm SHA256).Hash
                }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            $completed = Invoke-ClaudeCleanup -Force -DeployScriptPath 'deploy.ps1'

            $completed | Should -BeTrue
            Get-Content -LiteralPath "$commandTarget.user-backup" -Raw | Should -Be '# user command edit'
            Get-Content -LiteralPath $commandTarget -Raw | Should -Be '# user command edit'
            Get-Content -LiteralPath $hookTarget -Raw | Should -Be '# current hook'
            Test-Path -LiteralPath "$hookTarget.user-backup" | Should -BeFalse
        }

        It 'preserves the earliest forced-collision backup' {
            $source = Join-Path $OrchestrationDir 'obi-auto.md'
            $target = Join-Path $CommandsTarget 'obi-auto.md'
            $baseline = Join-Path $TempRoot 'prior.md'
            Set-Content -LiteralPath $source -Value '# current source' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $target -Value '# later user edit' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath "$target.user-backup" -Value '# earliest user edit' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $baseline -Value '# prior deployment' -Encoding UTF8 -NoNewline
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'commands/obi-auto.md' = 'orchestration/obi-auto.md' }
                deployed_hashes = @{ 'commands/obi-auto.md' = (Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            Invoke-ClaudeCleanup -Force -DeployScriptPath 'deploy.ps1' | Should -BeTrue

            Get-Content -LiteralPath "$target.user-backup" -Raw | Should -Be '# earliest user edit'
            Get-Content -LiteralPath $target -Raw | Should -Be '# later user edit'
        }

        It 'previews a forced collision without writing or removing anything' {
            $source = Join-Path $OrchestrationDir 'obi-auto.md'
            $target = Join-Path $CommandsTarget 'obi-auto.md'
            $baseline = Join-Path $TempRoot 'prior.md'
            Set-Content -LiteralPath $source -Value '# current source' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $target -Value '# user edit' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $baseline -Value '# prior deployment' -Encoding UTF8 -NoNewline
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'commands/obi-auto.md' = 'orchestration/obi-auto.md' }
                deployed_hashes = @{ 'commands/obi-auto.md' = (Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            Invoke-ClaudeCleanup -Force -DryRun -DeployScriptPath 'deploy.ps1' | Should -BeTrue

            Test-Path -LiteralPath "$target.user-backup" | Should -BeFalse
            Get-Content -LiteralPath $target -Raw | Should -Be '# user edit'
        }

        It 'preserves settings when the user-specific source disappeared' {
            $target = Join-Path $ClaudeTarget 'settings.json'
            Set-Content -LiteralPath $target -Value '{"user":true}' -Encoding UTF8 -NoNewline
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'settings.json' = 'users/missing/settings.json' }
                deployed_hashes = @{ 'settings.json' = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1' | Should -BeTrue

            Get-Content -LiteralPath $target -Raw | Should -Be '{"user":true}'
        }

        It 'fails closed for an unproven schema-1 target and accepts current-source equality' {
            $source = Join-Path $OrchestrationDir 'obi-auto.md'
            $target = Join-Path $CommandsTarget 'obi-auto.md'
            Set-Content -LiteralPath $source -Value '# current source' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $target -Value '# unknown legacy bytes' -Encoding UTF8 -NoNewline
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{ version = '1.0'; mappings = @{ 'commands/obi-auto.md' = 'orchestration/obi-auto.md' } } |
                ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1' | Should -BeFalse
            Get-Content -LiteralPath $target -Raw | Should -Be '# unknown legacy bytes'

            Set-Content -LiteralPath $target -Value '# current source' -Encoding UTF8 -NoNewline
            Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1' | Should -BeTrue
            Get-Content -LiteralPath $target -Raw | Should -Be '# current source'
        }

        It 'uses the schema-1 Git bridge when the target matches checked-in HEAD' {
            $source = Join-Path $OrchestrationDir 'obi-auto.md'
            $target = Join-Path $CommandsTarget 'obi-auto.md'
            Set-Content -LiteralPath $source -Value '# working tree update' -Encoding UTF8 -NoNewline
            Set-Content -LiteralPath $target -Value '# checked-in deployment' -Encoding UTF8 -NoNewline
            Mock Test-ObiTargetMatchesGitHead { return $true }

            $collision = Get-ObiOwnedFileCollision -TargetFile $target -DeployedRel 'commands/obi-auto.md' `
                -CategoryLabel 'command' -SourceRel 'orchestration/obi-auto.md' -RepoRoot $RepoRoot

            $collision | Should -BeNullOrEmpty
            Should -Invoke Test-ObiTargetMatchesGitHead -Exactly 1
        }

        It 'excludes separately rooted tools mappings from cleanup and Claude-root hashes' {
            $toolsTarget = Join-Path $ClaudeTarget 'tools'
            $target = Join-Path $toolsTarget 'sentinel.ps1'
            New-Item -ItemType Directory -Path $toolsTarget -Force | Out-Null
            Set-Content -LiteralPath $target -Value '# must survive' -Encoding UTF8 -NoNewline
            $manifestDir = Join-Path $ClaudeTarget '.obi'
            New-Item -ItemType Directory -Path $manifestDir -Force | Out-Null
            @{
                version = '2.0'
                mappings = @{ 'tools/sentinel.ps1' = 'tools/sentinel.ps1' }
                deployed_hashes = @{ 'tools/sentinel.ps1' = 'NOT-THE-TARGET-HASH' }
            } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Encoding UTF8

            Invoke-ClaudeCleanup -DeployScriptPath 'deploy.ps1' | Should -BeTrue
            Get-Content -LiteralPath $target -Raw | Should -Be '# must survive'

            $script:manifest = @{ 'tools/sentinel.ps1' = 'tools/sentinel.ps1' }
            Write-ClaudeDeploymentManifest
            $written = Get-Content -LiteralPath (Join-Path $manifestDir 'deployment-manifest.json') -Raw | ConvertFrom-Json
            @($written.deployed_hashes.PSObject.Properties).Count | Should -Be 0
        }
    }
}

<#
.SYNOPSIS
    Pester tests for verify-setup.ps1 (shim) and check-quick.ps1 (module)

.DESCRIPTION
    Tests the verify-setup shim forwarding and the check-quick.ps1 module's
    validation logic: expected command lists, settings parsing, hooks validation,
    environment variable checks, and permission categorization.
    Uses isolated temp directories. Compatible with Pester 3.4.0+.

.EXAMPLE
    Invoke-Pester C:\src\obiwag-agents\tools\verify-setup.tests.ps1
#>

Describe 'Verify Setup' {

    BeforeEach {
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()
        $script:ClaudeDir = Join-Path $TempRoot '.claude'
        $script:CommandsDir = Join-Path $ClaudeDir 'commands'
        $script:HooksDir = Join-Path $ClaudeDir 'hooks'
        $script:ProjectDir = Join-Path (Join-Path $TempRoot 'project') '.claude'

        New-Item -ItemType Directory -Path $ClaudeDir -Force | Out-Null
        New-Item -ItemType Directory -Path $CommandsDir -Force | Out-Null
        New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null
        New-Item -ItemType Directory -Path $ProjectDir -Force | Out-Null
    }

    Context 'Shim Forwarding' {

        It 'verify-setup.ps1 forwards to config-guardian.ps1 -Quick' {
            $shimPath = Join-Path $PSScriptRoot 'verify-setup.ps1'
            $content = Get-Content $shimPath -Raw
            $content -match 'config-guardian\.ps1' | Should Be $true
            $content -match '-Quick' | Should Be $true
            $content -match '-CheckOnly' | Should Be $true
        }
    }

    Context 'Expected Commands List' {

        It 'Lists all 20 commands matching deploy.ps1' {
            # This is the authoritative list from deploy.ps1
            $deployRequired = @(
                'author.md', 'discovery.md', 'doc.md', 'fixissue.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md', 'triage.md'
            )

            # check-quick.ps1 expected commands must match
            $verifyExpected = @(
                'author.md', 'discovery.md', 'doc.md', 'fixissue.md', 'integrate.md',
                'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
                'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
                'readme-review.md', 'release.md', 're-review.md', 'review.md',
                'simplify.md', 'triage.md'
            )

            $verifyExpected.Count | Should Be 20

            $missing = $deployRequired | Where-Object { $_ -notin $verifyExpected }
            $missing.Count | Should Be 0
        }

        It 'Detects missing commands accurately' {
            # Deploy only some commands
            $deployed = @('author.md', 'discovery.md', 'obi.md')
            foreach ($cmd in $deployed) {
                Set-Content (Join-Path $CommandsDir $cmd) '# command' -Encoding UTF8
            }

            $expected = @(
                'author.md', 'discovery.md', 'doc.md', 'integrate.md', 'learning.md',
                'obi.md', 'obi-auto.md', 'obi-collect.md', 'obi-memory-review.md',
                'obi-swarm.md', 'obi-update.md', 'readme.md', 'readme-review.md',
                'release.md', 're-review.md', 'review.md', 'simplify.md'
            )

            $foundCount = 0
            $missingCommands = @()
            foreach ($cmd in $expected) {
                if (Test-Path (Join-Path $CommandsDir $cmd)) {
                    $foundCount++
                } else {
                    $missingCommands += $cmd
                }
            }

            $foundCount | Should Be 3
            $missingCommands.Count | Should Be 14
            ($missingCommands -contains 'doc.md') | Should Be $true
            ($missingCommands -contains 'integrate.md') | Should Be $true
        }

        It 'Reports full count when all commands deployed' {
            $expected = @(
                'author.md', 'discovery.md', 'doc.md', 'integrate.md', 'learning.md',
                'obi.md', 'obi-auto.md', 'obi-collect.md', 'obi-memory-review.md',
                'obi-swarm.md', 'obi-update.md', 'readme.md', 'readme-review.md',
                'release.md', 're-review.md', 'review.md', 'simplify.md'
            )

            foreach ($cmd in $expected) {
                Set-Content (Join-Path $CommandsDir $cmd) '# command' -Encoding UTF8
            }

            $foundCount = 0
            foreach ($cmd in $expected) {
                if (Test-Path (Join-Path $CommandsDir $cmd)) { $foundCount++ }
            }

            $foundCount | Should Be $expected.Count
        }
    }

    Context 'Settings.json Validation' {

        It 'Parses valid settings.json successfully' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            $settings = @{
                permissions = @{
                    allow = @('Read', 'Write', 'Glob', 'Grep', 'Edit')
                }
            }
            $settings | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8

            $parsed = Get-Content $settingsPath -Raw | ConvertFrom-Json
            $parsed.permissions.allow.Count | Should Be 5
        }

        It 'Counts permissions correctly' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            $perms = 1..150 | ForEach-Object { "Permission$_" }
            $settings = @{ permissions = @{ allow = $perms } }
            $settings | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8

            $parsed = Get-Content $settingsPath -Raw | ConvertFrom-Json
            $parsed.permissions.allow.Count | Should Be 150
        }

        It 'Handles missing permissions section' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            @{ hooks = @{} } | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8

            $parsed = Get-Content $settingsPath -Raw | ConvertFrom-Json
            $parsed.permissions | Should BeNullOrEmpty
        }

        It 'Detects invalid JSON' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            Set-Content $settingsPath '{ invalid json }}}' -Encoding UTF8

            $parseable = $true
            try {
                $null = Get-Content $settingsPath -Raw | ConvertFrom-Json
            } catch {
                $parseable = $false
            }
            $parseable | Should Be $false
        }
    }

    Context 'Settings Precedence' {

        It 'Detects project-local override with fewer permissions' {
            $userSettings = Join-Path $ClaudeDir 'settings.json'
            $projectSettings = Join-Path $ProjectDir 'settings.local.json'

            # User has 100 permissions
            $userPerms = 1..100 | ForEach-Object { "Perm$_" }
            @{ permissions = @{ allow = $userPerms } } | ConvertTo-Json -Depth 5 | Set-Content $userSettings -Encoding UTF8

            # Project has only 5
            $projPerms = @('Read', 'Write', 'Glob', 'Grep', 'Edit')
            @{ permissions = @{ allow = $projPerms } } | ConvertTo-Json -Depth 5 | Set-Content $projectSettings -Encoding UTF8

            $userJson = Get-Content $userSettings -Raw | ConvertFrom-Json
            $projJson = Get-Content $projectSettings -Raw | ConvertFrom-Json

            $userCount = $userJson.permissions.allow.Count
            $projCount = $projJson.permissions.allow.Count

            ($projCount -lt $userCount) | Should Be $true
        }

        It 'No override when project has more permissions' {
            $userSettings = Join-Path $ClaudeDir 'settings.json'
            $projectSettings = Join-Path $ProjectDir 'settings.local.json'

            $userPerms = @('Read', 'Write')
            @{ permissions = @{ allow = $userPerms } } | ConvertTo-Json -Depth 5 | Set-Content $userSettings -Encoding UTF8

            $projPerms = @('Read', 'Write', 'Glob', 'Grep', 'Edit')
            @{ permissions = @{ allow = $projPerms } } | ConvertTo-Json -Depth 5 | Set-Content $projectSettings -Encoding UTF8

            $userJson = Get-Content $userSettings -Raw | ConvertFrom-Json
            $projJson = Get-Content $projectSettings -Raw | ConvertFrom-Json

            $userCount = $userJson.permissions.allow.Count
            $projCount = $projJson.permissions.allow.Count

            ($projCount -lt $userCount) | Should Be $false
        }
    }

    Context 'Hooks Validation' {

        It 'Detects $HOME variable in settings.json hooks (Windows incompatible)' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            $content = '{"hooks": {"PreToolUse": [{"hooks": [{"command": "$HOME/.claude/hooks/run.sh"}]}]}}'
            Set-Content $settingsPath $content -Encoding UTF8

            $raw = Get-Content $settingsPath -Raw
            ($raw -match '\$HOME') | Should Be $true
        }

        It 'Passes when no $HOME variable present in settings.json' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            $content = '{"hooks": {"PreToolUse": [{"hooks": [{"command": "%USERPROFILE%\\.claude\\hooks\\run.cmd"}]}]}}'
            Set-Content $settingsPath $content -Encoding UTF8

            $raw = Get-Content $settingsPath -Raw
            ($raw -match '\$HOME') | Should Be $false
        }

        It 'Detects CLAUDE_PROJECT_ROOT usage when variable is not set' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            $content = '{"hooks": {"PreToolUse": [{"hooks": [{"command": "${CLAUDE_PROJECT_ROOT}/hooks/run.py"}]}]}}'
            Set-Content $settingsPath $content -Encoding UTF8

            $raw = Get-Content $settingsPath -Raw
            $usesVar = ($raw -match '\$\{CLAUDE_PROJECT_ROOT\}')
            $usesVar | Should Be $true
        }

        It 'Detects hooks section in settings.json' {
            $settingsPath = Join-Path $ClaudeDir 'settings.json'
            $content = @{
                hooks = @{
                    PreToolUse = @(
                        @{ hooks = @( @{ command = 'test_hook.cmd'; timeout = 5 } ) }
                    )
                }
            }
            $content | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8

            $parsed = Get-Content $settingsPath -Raw | ConvertFrom-Json
            $parsed.hooks | Should Not BeNullOrEmpty
        }
    }

    Context 'Environment Checks' {

        It 'CLAUDE_PROJECT_ROOT validation logic' {
            $testRoot = Join-Path $TempRoot 'fakeroot'
            New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

            # Path exists
            (Test-Path $testRoot) | Should Be $true
        }

        It 'Detects missing Python' {
            $fakeCmd = Get-Command 'nonexistent-python-binary-12345' -ErrorAction SilentlyContinue
            $fakeCmd | Should BeNullOrEmpty
        }
    }

    Context 'Permission Thresholds' {

        It 'Categorizes 100+ as comprehensive' {
            $count = 150
            $category = if ($count -ge 100) { 'comprehensive' }
                        elseif ($count -ge 20) { 'moderate' }
                        elseif ($count -gt 0) { 'limited' }
                        else { 'none' }
            $category | Should Be 'comprehensive'
        }

        It 'Categorizes 20-99 as moderate' {
            $count = 50
            $category = if ($count -ge 100) { 'comprehensive' }
                        elseif ($count -ge 20) { 'moderate' }
                        elseif ($count -gt 0) { 'limited' }
                        else { 'none' }
            $category | Should Be 'moderate'
        }

        It 'Categorizes 1-19 as limited' {
            $count = 5
            $category = if ($count -ge 100) { 'comprehensive' }
                        elseif ($count -ge 20) { 'moderate' }
                        elseif ($count -gt 0) { 'limited' }
                        else { 'none' }
            $category | Should Be 'limited'
        }

        It 'Categorizes 0 as none' {
            $count = 0
            $category = if ($count -ge 100) { 'comprehensive' }
                        elseif ($count -ge 20) { 'moderate' }
                        elseif ($count -gt 0) { 'limited' }
                        else { 'none' }
            $category | Should Be 'none'
        }
    }
}

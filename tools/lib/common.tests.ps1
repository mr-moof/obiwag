<#
.SYNOPSIS
    Pester tests for tools/lib/common.ps1

.DESCRIPTION
    Tests shared output helpers and Test-HookCommandPaths function.
    Requires Pester 5.

.EXAMPLE
    .\tools\run-tests.ps1 -Path tools\lib\common.tests.ps1
#>
BeforeAll {

# Dot-source the module under test
$ScriptDir = $PSScriptRoot
. (Join-Path $ScriptDir 'common.ps1')

}

Describe 'Shared Helpers (common.ps1)' {

    Context 'Output Helpers Exist' {

        It 'Defines Write-Header' {
            (Get-Command Write-Header -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Write-Step' {
            (Get-Command Write-Step -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Write-Check' {
            (Get-Command Write-Check -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Write-Info' {
            (Get-Command Write-Info -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Write-Problem' {
            (Get-Command Write-Problem -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Write-Detail' {
            (Get-Command Write-Detail -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Write-Repair' {
            (Get-Command Write-Repair -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Test-HookCommandPaths' {

        BeforeEach {
            $script:TempDir = Join-Path $TestDrive (New-Guid).ToString()
            New-Item -ItemType Directory -Path $TempDir -Force | Out-Null
        }

        It 'Returns valid result for missing settings file' {
            $result = Test-HookCommandPaths -SettingsPath 'C:\nonexistent\settings.json'
            $result.Valid | Should -Be $true
            $result.MissingPaths.Count | Should -Be 0
        }

        It 'Returns valid result for settings with no hooks' {
            $settingsPath = Join-Path $TempDir 'settings.json'
            @{ permissions = @{ allow = @('Read') } } | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $true
        }

        It 'Detects missing hook script path' {
            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    SessionStart = @(
                        @{
                            hooks = @(
                                @{
                                    type = 'command'
                                    command = '"C:/nonexistent/hooks/hook_wrapper.cmd" session_start'
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $false
            $result.MissingPaths.Count | Should -Be 1
            ($result.MissingPaths[0] -like '*hook_wrapper.cmd') | Should -Be $true
        }

        It 'Reports valid when hook script exists' {
            $hooksDir = Join-Path $TempDir 'hooks'
            New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
            Set-Content (Join-Path $hooksDir 'hook_wrapper.cmd') '@echo off' -Encoding UTF8

            $settingsPath = Join-Path $TempDir 'settings.json'
            $wrapperPath = (Join-Path $hooksDir 'hook_wrapper.cmd') -replace '\\', '/'
            $settings = @{
                hooks = @{
                    SessionStart = @(
                        @{
                            hooks = @(
                                @{
                                    type = 'command'
                                    command = "`"$wrapperPath`" session_start"
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $true
            $result.MissingPaths.Count | Should -Be 0
        }

        It 'Validates script paths in exec-form args' {
            $hooksDir = Join-Path $TempDir 'hooks'
            New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
            $scriptPath = Join-Path $hooksDir 'user_prompt_submit.py'
            Set-Content $scriptPath '# placeholder' -Encoding UTF8

            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    UserPromptSubmit = @(
                        @{
                            hooks = @(
                                @{
                                    type    = 'command'
                                    command = 'python'
                                    args    = @(($scriptPath -replace '\\', '/'))
                                    timeout = 10
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $true
            $result.MissingPaths.Count | Should -Be 0
        }

        It 'Detects a missing script path in exec-form args' {
            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    UserPromptSubmit = @(
                        @{
                            hooks = @(
                                @{
                                    type    = 'command'
                                    command = 'python'
                                    args    = @('C:/missing/user_prompt_submit.py')
                                    timeout = 10
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $false
            $result.MissingPaths | Should -Contain 'C:/missing/user_prompt_submit.py'
        }

        It 'Discovers scripts under hook events added after the original event list' {
            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    PostToolUseFailure = @(
                        @{
                            hooks = @(
                                @{
                                    type    = 'command'
                                    command = 'python'
                                    args    = @('C:/missing/post_tool_use.py')
                                }
                            )
                        }
                    )
                    SubagentStop = @(
                        @{
                            hooks = @(
                                @{
                                    type    = 'command'
                                    command = 'python'
                                    args    = @('C:/missing/subagent_stop.py')
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $false
            $result.MissingPaths | Should -Contain 'C:/missing/post_tool_use.py'
            $result.MissingPaths | Should -Contain 'C:/missing/subagent_stop.py'
        }

        It 'Uses FallbackDir when primary path is missing' {
            $fallbackDir = Join-Path $TempDir 'source-hooks'
            New-Item -ItemType Directory -Path $fallbackDir -Force | Out-Null
            Set-Content (Join-Path $fallbackDir 'hook_wrapper.cmd') '@echo off' -Encoding UTF8

            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    SessionStart = @(
                        @{
                            hooks = @(
                                @{
                                    type = 'command'
                                    command = '"C:/nonexistent/hooks/hook_wrapper.cmd" session_start'
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath -FallbackDir $fallbackDir
            $result.Valid | Should -Be $true
            $result.MissingPaths.Count | Should -Be 0
        }

        It 'Reports missing even with FallbackDir when both miss' {
            $fallbackDir = Join-Path $TempDir 'empty-fallback'
            New-Item -ItemType Directory -Path $fallbackDir -Force | Out-Null

            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    SessionStart = @(
                        @{
                            hooks = @(
                                @{
                                    type = 'command'
                                    command = '"C:/nonexistent/hooks/hook_wrapper.cmd" session_start'
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath -FallbackDir $fallbackDir
            $result.Valid | Should -Be $false
            $result.MissingPaths.Count | Should -Be 1
        }

        It 'Handles multiple hook types' {
            $settingsPath = Join-Path $TempDir 'settings.json'
            $settings = @{
                hooks = @{
                    SessionStart = @(
                        @{
                            hooks = @(
                                @{ type = 'command'; command = '"C:/missing/hook_wrapper.cmd" session_start' }
                            )
                        }
                    )
                    Stop = @(
                        @{
                            hooks = @(
                                @{ type = 'command'; command = '"C:/missing/hook_wrapper.cmd" stop' }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            $result.Valid | Should -Be $false
            # Same script path referenced twice, but reported for each occurrence
            $result.MissingPaths.Count | Should -BeGreaterThan 0
        }

        It 'Expands $HOME in commands' {
            $hooksDir = Join-Path $TempDir 'hooks'
            New-Item -ItemType Directory -Path $hooksDir -Force | Out-Null
            Set-Content (Join-Path $hooksDir 'hook_wrapper.cmd') '@echo off' -Encoding UTF8

            $settingsPath = Join-Path $TempDir 'settings.json'
            # Use $HOME which should expand to $env:USERPROFILE
            $settings = @{
                hooks = @{
                    SessionStart = @(
                        @{
                            hooks = @(
                                @{
                                    type = 'command'
                                    command = '"$HOME/.claude/hooks/hook_wrapper.cmd" session_start'
                                }
                            )
                        }
                    )
                }
            }
            $settings | ConvertTo-Json -Depth 10 | Set-Content $settingsPath -Encoding UTF8

            # The expanded path won't match our temp dir, so it should report missing
            # unless $HOME/.claude/hooks/hook_wrapper.cmd actually exists on disk
            $result = Test-HookCommandPaths -SettingsPath $settingsPath
            # Just verify it runs without error and returns valid structure
            $result.Keys -contains 'Valid' | Should -Be $true
            $result.Keys -contains 'MissingPaths' | Should -Be $true
        }
    }
}

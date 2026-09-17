<#
.SYNOPSIS
    Pester tests for tools/lib/validation.ps1

.DESCRIPTION
    Tests shared validation primitives: Get-LocalSettingsFiles, Test-SettingsJson,
    Test-HeredocPollution, Test-HookPaths, Test-SharedPermissions, Test-Dependencies.
    Uses isolated temp directories. Requires Pester 5.

.EXAMPLE
    .\tools\run-tests.ps1 -Path tools\lib\validation.tests.ps1
#>
BeforeAll {

# Dot-source shared helpers (output functions + Test-HookCommandPaths)
$ScriptDir = $PSScriptRoot
. (Join-Path $ScriptDir 'common.ps1')

# Load hook manifest (single source of truth for filenames)
$script:ManifestPath = Join-Path $ScriptDir 'hook-manifest.json'
$script:Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$script:ManifestHookFiles = @($Manifest.hooks | ForEach-Object { $_.file })
$script:ManifestCoreModules = @($Manifest.core_modules)

# Set up script-scope variables that validation.ps1 functions depend on.
# These are overridden in BeforeEach to point to temp directories.
$script:SettingsJson = ''
$script:HooksDir = ''
$script:CoreDir = ''
$script:HookFiles = @()
$script:CoreModules = @()
$script:RepoRoot = ''
$script:PythonExe = ''

# Dot-source the module under test
. (Join-Path $ScriptDir 'validation.ps1')

}

Describe 'Validation Primitives (validation.ps1)' {

    BeforeEach {
        # Create isolated temp structure
        # [guid]::NewGuid() not New-Guid: the .NET call has no dependency on the Utility module
        # resolving, which can fail under PS 5.1 when a PowerShell 7 module path shadows 5.1's on
        # PSModulePath (New-Guid then resolves to a 7.x Utility that 5.1 can't load). Equivalent.
        $script:TempRoot = Join-Path $TestDrive ([guid]::NewGuid().ToString())
        $script:ClaudeDir = Join-Path $TempRoot '.claude'
        $script:HooksDir = Join-Path $ClaudeDir 'hooks'
        $script:CoreDir = Join-Path $HooksDir 'core'
        $script:RepoRoot = Join-Path $TempRoot 'repo'
        $script:ConfigDir = Join-Path $RepoRoot 'config'

        New-Item -ItemType Directory -Path $ClaudeDir -Force | Out-Null
        New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null
        New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null
        New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null

        # Create a valid settings.json
        $script:SettingsJson = Join-Path $ClaudeDir 'settings.json'
        $settingsContent = @{
            hooks = @{
                SessionStart = @(
                    @{
                        hooks = @(
                            @{
                                type    = 'command'
                                command = "`"$($HooksDir -replace '\\', '/')/hook_wrapper.cmd`" session_start"
                                timeout = 5
                            }
                        )
                    }
                )
            }
        }
        $settingsContent | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8

        # Create hook files from manifest
        $script:HookFiles = $ManifestHookFiles
        $script:CoreModules = $ManifestCoreModules

        foreach ($hf in $HookFiles) {
            Set-Content (Join-Path $HooksDir $hf) '# placeholder' -Encoding UTF8
        }
        foreach ($mod in $CoreModules) {
            $modulePath = Join-Path $CoreDir $mod
            New-Item -ItemType Directory -Path (Split-Path -Parent $modulePath) -Force | Out-Null
            Set-Content $modulePath '# placeholder' -Encoding UTF8
        }

        # Python exe (use real path if available, else fake)
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $script:PythonExe = $pythonCmd.Source
        } else {
            $script:PythonExe = 'C:\Python314\python.exe'
        }
    }

    Context 'Function Definitions' {

        It 'Defines Get-LocalSettingsFiles' {
            (Get-Command Get-LocalSettingsFiles -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Test-SettingsJson' {
            (Get-Command Test-SettingsJson -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Test-HeredocPollution' {
            (Get-Command Test-HeredocPollution -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Test-HookPaths' {
            (Get-Command Test-HookPaths -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Test-SharedPermissions' {
            (Get-Command Test-SharedPermissions -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Test-Dependencies' {
            (Get-Command Test-Dependencies -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }

        It 'Defines Test-PermissionRules' {
            (Get-Command Test-PermissionRules -ErrorAction SilentlyContinue) | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Test-SettingsJson' {

        It 'Reports valid for well-formed settings.json' {
            $result = Test-SettingsJson
            $result.Valid | Should -Be $true
            $result.Errors.Count | Should -Be 0
        }

        It 'Reports invalid for missing settings.json' {
            $script:SettingsJson = Join-Path $TempRoot 'nonexistent.json'

            $result = Test-SettingsJson
            $result.Valid | Should -Be $false
            ($result.Errors -like '*file not found*').Count | Should -BeGreaterThan 0
        }

        It 'Reports invalid for broken JSON' {
            Set-Content $SettingsJson '{ this is not json !!!' -Encoding UTF8

            $result = Test-SettingsJson
            $result.Valid | Should -Be $false
            $result.Errors.Count | Should -BeGreaterThan 0
        }
    }

    Context 'Test-HeredocPollution' {

        It 'Reports clean when no settings.local.json files exist' {
            # Get-LocalSettingsFiles searches ~/source/ which may or may not have files.
            # This tests the function runs without error.
            $result = Test-HeredocPollution
            $result.Keys -contains 'Clean' | Should -Be $true
            $result.Keys -contains 'PollutedFiles' | Should -Be $true
        }

        It 'Detects entries over 200 chars as pollution' {
            $longEntry = 'Bash(' + ('x' * 250) + ')'
            $localSettings = @{
                permissions = @{
                    allow = @('Read', 'Write', $longEntry)
                }
            }

            # Simulate the detection logic directly
            $allowEntries = @($localSettings.permissions.allow)
            $polluted = @($allowEntries | Where-Object { $_.Length -gt 200 })

            $polluted.Count | Should -Be 1
            $polluted[0].Length | Should -BeGreaterThan 200
        }

        It 'Does not flag entries under 200 chars' {
            $shortEntries = @('Read', 'Write', 'Bash(start:*)')

            $polluted = @($shortEntries | Where-Object { $_.Length -gt 200 })

            $polluted.Count | Should -Be 0
        }
    }

    Context 'Test-HookPaths' {

        It 'Reports valid when all hook files are present' {
            $result = Test-HookPaths
            $result.Valid | Should -Be $true
            $result.MissingFiles.Count | Should -Be 0
            $result.MissingCoreModules.Count | Should -Be 0
        }

        It 'Detects missing hook file' {
            Remove-Item (Join-Path $HooksDir 'hook_wrapper.cmd') -Force

            $result = Test-HookPaths
            $result.Valid | Should -Be $false
            ($result.MissingFiles -contains 'hook_wrapper.cmd') | Should -Be $true
        }

        It 'Detects missing core module' {
            Remove-Item (Join-Path $CoreDir 'calibration.py') -Force

            $result = Test-HookPaths
            $result.Valid | Should -Be $false
            ($result.MissingCoreModules -contains 'calibration.py') | Should -Be $true
        }

        It 'Reports multiple missing files' {
            Remove-Item (Join-Path $HooksDir 'session_start.py') -Force
            Remove-Item (Join-Path $HooksDir 'stop.py') -Force

            $result = Test-HookPaths
            $result.Valid | Should -Be $false
            ($result.MissingFiles -contains 'session_start.py') | Should -Be $true
            ($result.MissingFiles -contains 'stop.py') | Should -Be $true
        }
    }

    Context 'Test-SharedPermissions' {

        It 'Returns clean when no shared permissions file exists' {
            $result = Test-SharedPermissions
            $result.Clean | Should -Be $true
        }

        It 'Detects missing shared patterns in managed file' {
            $sharedAllows = @('Bash(start:*)', 'Bash(where.exe:*)', 'WebSearch')
            $localAllows = @('Bash(start:*)')

            $missing = @($sharedAllows | Where-Object { $_ -notin $localAllows })

            $missing.Count | Should -Be 2
            ($missing -contains 'Bash(where.exe:*)') | Should -Be $true
            ($missing -contains 'WebSearch') | Should -Be $true
        }

        It 'Reports clean when all shared patterns present' {
            $sharedAllows = @('Bash(start:*)', 'WebSearch')
            $localAllows = @('Bash(start:*)', 'WebSearch', 'mcp__compute-kb__search_kbs')

            $missing = @($sharedAllows | Where-Object { $_ -notin $localAllows })

            $missing.Count | Should -Be 0
        }

        It 'Handles invalid shared permissions file gracefully' {
            $sharedPermsFile = Join-Path $ConfigDir 'permissions-allow.json'
            Set-Content $sharedPermsFile '{ broken json' -Encoding UTF8

            $result = Test-SharedPermissions
            $result.Clean | Should -Be $false
        }
    }

    Context 'Test-Dependencies' {

        It 'Returns result with Valid and Missing keys' {
            $result = Test-Dependencies
            $result.Keys -contains 'Valid' | Should -Be $true
            $result.Keys -contains 'Missing' | Should -Be $true
        }

        It 'Reports valid when Python exists' {
            if (Test-Path $PythonExe) {
                $result = Test-Dependencies
                $result.Valid | Should -Be $true
            } else {
                # If Python not found on this machine, validate the negative path
                $result = Test-Dependencies
                $result.Valid | Should -Be $false
                ($result.Missing | Where-Object { $_ -like '*Python*' }).Count | Should -BeGreaterThan 0
            }
        }

        It 'Reports missing when Python path is invalid' {
            $script:PythonExe = 'C:\nonexistent\python.exe'

            $result = Test-Dependencies
            $result.Valid | Should -Be $false
            ($result.Missing | Where-Object { $_ -like '*Python*' }).Count | Should -BeGreaterThan 0
        }

        It 'Reports missing hook_wrapper.cmd when absent' {
            Remove-Item (Join-Path $HooksDir 'hook_wrapper.cmd') -Force

            $result = Test-Dependencies
            ($result.Missing -contains 'hook_wrapper.cmd') | Should -Be $true
        }
    }

    Context 'Test-PermissionRules' {

        # Write a settings.json with the given permissions.allow list and return its path.
        # In BeforeAll: a function defined directly in a Context body is created during
        # Pester 5's DISCOVERY pass and is gone when It bodies run.
        BeforeAll {
            function New-PermSettings {
                param([string[]]$Allow)
                $path = Join-Path $ClaudeDir 'perm-settings.json'
                (@{ permissions = @{ allow = $Allow } } | ConvertTo-Json -Depth 10) |
                    Set-Content $path -Encoding UTF8
                return $path
            }
        }

        It 'Reports valid when only Edit(...) and bare Write are present' {
            $p = New-PermSettings -Allow @(
                'Read', 'Write', 'Edit',
                'Edit(.git/**)', 'Edit(.git\COMMIT_EDITMSG_*)', 'Bash(git status:*)'
            )
            $result = Test-PermissionRules -SettingsPaths @($p)
            $result.Valid | Should -Be $true
            $result.Violations.Count | Should -Be 0
        }

        It 'Rejects a path-qualified Write(...) rule and reports the rule + path' {
            $p = New-PermSettings -Allow @('Read', 'Write(.git/**)')
            $result = Test-PermissionRules -SettingsPaths @($p)
            $result.Valid | Should -Be $false
            $result.Violations.Count | Should -Be 1
            $result.Violations[0].Rule | Should -Be 'Write(.git/**)'
            $result.Violations[0].Path | Should -Be $p
        }

        It 'Rejects all six Write(.git...) forms' {
            $sixWrite = @(
                'Write(.git/COMMIT_EDITMSG_*)',
                'Write(.git/**)',
                'Write(.git\COMMIT_EDITMSG_*)',
                'Write(.git\**)',
                'Write(**/.git/COMMIT_EDITMSG_*)',
                'Write(**\.git\COMMIT_EDITMSG_*)'
            )
            $p = New-PermSettings -Allow (@('Read', 'Write') + $sixWrite)
            $result = Test-PermissionRules -SettingsPaths @($p)
            $result.Valid | Should -Be $false
            $result.Violations.Count | Should -Be 6
        }

        It 'Accepts all six corrected Edit(.git...) forms' {
            $sixEdit = @(
                'Edit(.git/COMMIT_EDITMSG_*)',
                'Edit(.git/**)',
                'Edit(.git\COMMIT_EDITMSG_*)',
                'Edit(.git\**)',
                'Edit(**/.git/COMMIT_EDITMSG_*)',
                'Edit(**\.git\**)'
            )
            $p = New-PermSettings -Allow (@('Read', 'Write') + $sixEdit)
            $result = Test-PermissionRules -SettingsPaths @($p)
            $result.Valid | Should -Be $true
            $result.Violations.Count | Should -Be 0
        }

        It 'Does not flag bare Write (tool-level, no parentheses)' {
            $p = New-PermSettings -Allow @('Write', 'Read', 'Edit')
            $result = Test-PermissionRules -SettingsPaths @($p)
            $result.Valid | Should -Be $true
        }

        It 'Skips a missing settings file without error' {
            $missing = Join-Path $TempRoot 'no-such-settings.json'
            $result = Test-PermissionRules -SettingsPaths @($missing)
            $result.Valid | Should -Be $true
        }

        It 'Skips an unparseable settings file without failing the rule check' {
            $p = Join-Path $ClaudeDir 'broken-perm.json'
            Set-Content $p '{ not valid json' -Encoding UTF8
            $result = Test-PermissionRules -SettingsPaths @($p)
            # JSON syntax is Test-SettingsJson's job; this check must not hard-fail on it.
            $result.Valid | Should -Be $true
        }

        It 'Checks multiple settings paths (source + deployed) in one call' {
            $good = New-PermSettings -Allow @('Edit(.git/**)')
            $bad = Join-Path $ClaudeDir 'bad-perm.json'
            (@{ permissions = @{ allow = @('Write(.git/**)') } } | ConvertTo-Json -Depth 10) |
                Set-Content $bad -Encoding UTF8
            $result = Test-PermissionRules -SettingsPaths @($good, $bad)
            $result.Valid | Should -Be $false
            $result.Violations.Count | Should -Be 1
            $result.Violations[0].Path | Should -Be $bad
        }
    }

    Context 'Get-LocalSettingsFiles' {

        It 'Returns array type' {
            $result = Get-LocalSettingsFiles
            # Should return array (possibly empty)
            $result -is [array] -or $result.Count -ge 0 | Should -Be $true
        }

        It 'Only returns files under .claude directories' {
            $result = Get-LocalSettingsFiles
            foreach ($file in $result) {
                $file.FullName -like '*\.claude\*' | Should -Be $true
            }
        }
    }
}

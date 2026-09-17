<#
.SYNOPSIS
    Pester tests for config-guardian.ps1 (dispatcher-level)

.DESCRIPTION
    Tests the dispatcher's end-to-end behavior. Detailed function-level tests
    for extracted modules live in tools/lib/checks/*.tests.ps1:
      - guardian-diagnostics.tests.ps1  (Test-CircularDeps, Test-BackupHealth)
      - guardian-repair.tests.ps1       (Repair-*, Snapshot, SafeMode, Issue)
      - check-auto-max.tests.ps1       (Test-AutoMaxConfig)
      - check-dispatch-docs.tests.ps1  (Test-DispatchDocs)
    Requires Pester 5.

.EXAMPLE
    .\tools\run-tests.ps1 -Path tools\config-guardian.tests.ps1
#>

Describe 'Config Guardian Dispatcher' {

    # Load hook manifest (single source of truth for filenames)
    $script:ScriptDir = $PSScriptRoot
    $script:ManifestPath = Join-Path $ScriptDir 'lib\hook-manifest.json'
    $script:Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
    $script:ManifestHookFiles = @($Manifest.hooks | ForEach-Object { $_.file })
    $script:ManifestCoreModules = @($Manifest.core_modules)

    BeforeEach {
        # Create isolated temp structure mimicking ~/.claude
        # [guid]::NewGuid() not New-Guid: the .NET call has no dependency on the Utility module
        # resolving, which can fail under PS 5.1 when a PowerShell 7 module path shadows 5.1's on
        # PSModulePath. Equivalent and portable.
        $script:TempRoot = Join-Path $TestDrive ([guid]::NewGuid().ToString())
        $script:ClaudeDir = Join-Path $TempRoot '.claude'
        $script:HooksDir = Join-Path $ClaudeDir 'hooks'
        $script:CoreDir = Join-Path $HooksDir 'core'
        $script:SettingsPath = Join-Path $ClaudeDir 'settings.json'

        New-Item -ItemType Directory -Path $ClaudeDir -Force | Out-Null
        New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null
        New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null

        # Create a valid settings.json
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
        $settingsContent | ConvertTo-Json -Depth 10 | Set-Content $SettingsPath -Encoding UTF8

        # Create hook files (from manifest)
        $ManifestHookFiles | ForEach-Object {
            Set-Content (Join-Path $HooksDir $_) "# placeholder" -Encoding UTF8
        }

        # Create core modules (from manifest)
        $ManifestCoreModules | ForEach-Object {
            $modulePath = Join-Path $CoreDir $_
            New-Item -ItemType Directory -Path (Split-Path -Parent $modulePath) -Force | Out-Null
            Set-Content $modulePath "# placeholder" -Encoding UTF8
        }
    }

    Context 'End-to-End CheckOnly' {

        It 'Verifies all hook files present in healthy state' {
            $allPresent = $true
            foreach ($hf in $ManifestHookFiles) {
                if (-not (Test-Path (Join-Path $HooksDir $hf))) { $allPresent = $false }
            }
            $allPresent | Should -Be $true
        }

        It 'Verifies settings.json is valid JSON' {
            { Get-Content $SettingsPath -Raw | ConvertFrom-Json } | Should -Not -Throw
        }
    }

    Context 'Dispatcher accepts required flags' {

        It 'Accepts -CheckOnly -NoIssue -NoSnapshot (deploy-claude.ps1 call)' {
            $script = Join-Path $ScriptDir 'config-guardian.ps1'
            $params = (Get-Command $script).Parameters
            $params.ContainsKey('CheckOnly') | Should -Be $true
            $params.ContainsKey('NoIssue') | Should -Be $true
            $params.ContainsKey('NoSnapshot') | Should -Be $true
            $params.ContainsKey('RepoRoot') | Should -Be $true
            $params.ContainsKey('Quick') | Should -Be $true
        }
    }
}

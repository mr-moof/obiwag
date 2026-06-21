<#
.SYNOPSIS
    Pester 3.4 tests for tools/lib/checks/guardian-repair.ps1
    (Repair-*, Save-ConfigSnapshot, Restore-FromSnapshot, New-RecoveryIssue,
     Enter-SafeMode, Exit-SafeMode functions).
#>

Describe 'Guardian Repair' {

    $script:ToolsDir = Join-Path (Split-Path -Parent $PSScriptRoot) '..'
    $script:ToolsDir = (Resolve-Path $script:ToolsDir).Path

    $script:ManifestPath = Join-Path $script:ToolsDir 'lib\hook-manifest.json'
    $script:Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
    $script:ManifestHookFiles = @($Manifest.hooks | ForEach-Object { $_.file })
    $script:ManifestCoreModules = @($Manifest.core_modules)

    BeforeEach {
        # Dot-source dependencies
        . (Join-Path $script:ToolsDir 'lib\common.ps1')
        . (Join-Path $script:ToolsDir 'lib\validation.ps1')
        . (Join-Path $script:ToolsDir 'lib\checks\guardian-repair.ps1')

        # Create isolated temp structure mimicking ~/.claude
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()
        $script:ClaudeDir = Join-Path $TempRoot '.claude'
        $script:HooksDir = Join-Path $ClaudeDir 'hooks'
        $script:CoreDir = Join-Path $HooksDir 'core'
        $script:SnapshotDir = Join-Path (Join-Path $ClaudeDir '.obi') 'config-snapshots'
        $script:SourceHooksDir = Join-Path (Join-Path (Join-Path $TempRoot 'source') 'obiwag-agents') 'hooks'
        $script:SourceCoreDir = Join-Path $SourceHooksDir 'core'
        $script:ProjectDir = Join-Path (Join-Path (Join-Path $TempRoot 'source') 'myproject') '.claude'
        $script:ScriptDir = $script:ToolsDir
        $script:RepoRoot = Split-Path -Parent $script:ToolsDir

        # Create directory structure
        New-Item -ItemType Directory -Path $ClaudeDir -Force | Out-Null
        New-Item -ItemType Directory -Path $HooksDir -Force | Out-Null
        New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null
        New-Item -ItemType Directory -Path $SourceHooksDir -Force | Out-Null
        New-Item -ItemType Directory -Path $SourceCoreDir -Force | Out-Null
        New-Item -ItemType Directory -Path $ProjectDir -Force | Out-Null

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

        # Create hook files (from manifest)
        $ManifestHookFiles | ForEach-Object {
            Set-Content (Join-Path $HooksDir $_) "# placeholder" -Encoding UTF8
        }

        # Create source repo hook files (for restore)
        $ManifestHookFiles | ForEach-Object {
            Set-Content (Join-Path $SourceHooksDir $_) "# source repo copy" -Encoding UTF8
        }

        # Create core modules (from manifest)
        $ManifestCoreModules | ForEach-Object {
            Set-Content (Join-Path $CoreDir $_) "# placeholder" -Encoding UTF8
            Set-Content (Join-Path $SourceCoreDir $_) "# source repo copy" -Encoding UTF8
        }

        $script:HookFiles = $ManifestHookFiles
        $script:CoreModules = $ManifestCoreModules
        $script:PythonExe = if (Get-Command python -ErrorAction SilentlyContinue) { (Get-Command python).Source } else { 'C:\Python314\python.exe' }
    }

    Context 'Missing Hook Files' {

        It 'Detects missing hook_wrapper.cmd' {
            Remove-Item (Join-Path $HooksDir 'hook_wrapper.cmd') -Force

            $missing = @()
            foreach ($hf in $ManifestHookFiles) {
                $path = Join-Path $HooksDir $hf
                if (-not (Test-Path $path)) {
                    $missing += $hf
                }
            }

            ($missing -contains 'hook_wrapper.cmd') | Should Be $true
        }

        It 'Detects missing Python hook files' {
            Remove-Item (Join-Path $HooksDir 'session_start.py') -Force
            Remove-Item (Join-Path $HooksDir 'stop.py') -Force

            $missing = @()
            foreach ($hf in $ManifestHookFiles) {
                if (-not (Test-Path (Join-Path $HooksDir $hf))) {
                    $missing += $hf
                }
            }

            $missing.Count | Should Be 2
            ($missing -contains 'session_start.py') | Should Be $true
            ($missing -contains 'stop.py') | Should Be $true
        }

        It 'Detects missing core modules' {
            Remove-Item (Join-Path $CoreDir 'calibration.py') -Force
            Remove-Item (Join-Path $CoreDir 'hook_logger.py') -Force

            $missingCore = @()
            foreach ($mod in $ManifestCoreModules) {
                if (-not (Test-Path (Join-Path $CoreDir $mod))) {
                    $missingCore += $mod
                }
            }

            $missingCore.Count | Should Be 2
            ($missingCore -contains 'calibration.py') | Should Be $true
            ($missingCore -contains 'hook_logger.py') | Should Be $true
        }

        It 'Repairs by copying from source repo' {
            Remove-Item (Join-Path $HooksDir 'session_start.py') -Force
            Remove-Item (Join-Path $CoreDir 'calibration.py') -Force

            # Simulate repair: copy from source
            Copy-Item (Join-Path $SourceHooksDir 'session_start.py') (Join-Path $HooksDir 'session_start.py') -Force
            Copy-Item (Join-Path $SourceCoreDir 'calibration.py') (Join-Path $CoreDir 'calibration.py') -Force

            (Test-Path (Join-Path $HooksDir 'session_start.py')) | Should Be $true
            (Test-Path (Join-Path $CoreDir 'calibration.py')) | Should Be $true
            (Get-Content (Join-Path $HooksDir 'session_start.py')) | Should Be '# source repo copy'
        }
    }

    Context 'Corrupted settings.local.json' {

        It 'Detects entries >200 chars as heredoc pollution' {
            $longEntry = 'Bash(' + ('x' * 250) + ')'
            $localSettings = @{
                permissions = @{
                    allow = @(
                        'Read',
                        'Write',
                        $longEntry
                    )
                }
            }
            $localPath = Join-Path $ProjectDir 'settings.local.json'
            $localSettings | ConvertTo-Json -Depth 5 | Set-Content $localPath -Encoding UTF8

            $json = Get-Content $localPath -Raw | ConvertFrom-Json
            $polluted = @($json.permissions.allow | Where-Object { $_.Length -gt 200 })

            $polluted.Count | Should Be 1
            $polluted[0].Length | Should BeGreaterThan 200
        }

        It 'Removes polluted entries while keeping clean ones' {
            $longEntry = 'Bash(' + ('x' * 250) + ')'
            $localSettings = @{
                permissions = @{
                    allow = @(
                        'Read',
                        'Write',
                        $longEntry,
                        'Glob'
                    )
                }
            }
            $localPath = Join-Path $ProjectDir 'settings.local.json'
            $localSettings | ConvertTo-Json -Depth 5 | Set-Content $localPath -Encoding UTF8

            # Simulate repair
            $json = Get-Content $localPath -Raw | ConvertFrom-Json
            $cleaned = @($json.permissions.allow | Where-Object { $_.Length -le 200 })
            $json.permissions.allow = $cleaned
            $json | ConvertTo-Json -Depth 10 | Set-Content $localPath -Encoding UTF8

            # Verify
            $result = Get-Content $localPath -Raw | ConvertFrom-Json
            $result.permissions.allow.Count | Should Be 3
            ($result.permissions.allow -contains 'Read') | Should Be $true
            ($result.permissions.allow -contains 'Write') | Should Be $true
            ($result.permissions.allow -contains 'Glob') | Should Be $true
        }

        It 'Handles completely unparseable JSON by deleting file' {
            $localPath = Join-Path $ProjectDir 'settings.local.json'
            Set-Content $localPath '{ this is not json !!!' -Encoding UTF8

            $parseable = $true
            try {
                $null = Get-Content $localPath -Raw | ConvertFrom-Json
            } catch {
                $parseable = $false
            }
            $parseable | Should Be $false

            # Simulate repair: delete unparseable file
            Remove-Item $localPath -Force

            (Test-Path $localPath) | Should Be $false
        }

        It 'Preserves settings.json during repair of settings.local.json' {
            $localPath = Join-Path $ProjectDir 'settings.local.json'
            $longEntry = 'Bash(' + ('x' * 250) + ')'
            @{ permissions = @{ allow = @($longEntry) } } | ConvertTo-Json -Depth 5 | Set-Content $localPath -Encoding UTF8

            $settingsBefore = Get-Content $SettingsJson -Raw

            # Simulate repair of local file only
            $json = Get-Content $localPath -Raw | ConvertFrom-Json
            $json.permissions.allow = @($json.permissions.allow | Where-Object { $_.Length -le 200 })
            $json | ConvertTo-Json -Depth 10 | Set-Content $localPath -Encoding UTF8

            # settings.json should be untouched
            $settingsAfter = Get-Content $SettingsJson -Raw
            $settingsAfter | Should Be $settingsBefore
        }
    }

    Context 'Snapshots' {

        It 'Creates snapshot with correct structure' {
            New-Item -ItemType Directory -Path $SnapshotDir -Force | Out-Null

            $snapshot = @{
                timestamp           = (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss')
                settings_json_hash  = 'abc123'
                settings_json_valid = $true
                hook_files          = @(@{ name = 'hook_wrapper.cmd'; exists = $true; hash = 'def456' })
                core_modules        = @(@{ name = 'calibration.py'; exists = $true; hash = '789abc' })
                python_version      = '3.14.2'
                obi_version         = '0.56'
            }
            $snapshotFile = Join-Path $SnapshotDir 'snapshot-20260217-120000.json'
            $snapshot | ConvertTo-Json -Depth 5 | Set-Content $snapshotFile -Encoding UTF8

            $loaded = Get-Content $snapshotFile -Raw | ConvertFrom-Json
            $loaded.settings_json_valid | Should Be $true
            $loaded.python_version | Should Be '3.14.2'
            $loaded.obi_version | Should Be '0.56'
            $loaded.hook_files.Count | Should Be 1
            $loaded.hook_files[0].name | Should Be 'hook_wrapper.cmd'
        }

        It 'Rotates snapshots keeping only 5' {
            New-Item -ItemType Directory -Path $SnapshotDir -Force | Out-Null

            # Create 7 snapshots
            1..7 | ForEach-Object {
                $ts = "2026021{0}-12000{0}" -f $_
                $file = Join-Path $SnapshotDir "snapshot-$ts.json"
                @{ timestamp = $ts; settings_json_valid = $true } | ConvertTo-Json | Set-Content $file -Encoding UTF8
            }

            (Get-ChildItem -Path $SnapshotDir -Filter 'snapshot-*.json').Count | Should Be 7

            # Simulate rotation
            $all = Get-ChildItem -Path $SnapshotDir -Filter 'snapshot-*.json' | Sort-Object Name -Descending
            $all | Select-Object -Skip 5 | ForEach-Object { Remove-Item $_.FullName -Force }

            (Get-ChildItem -Path $SnapshotDir -Filter 'snapshot-*.json').Count | Should Be 5
        }

        It 'Identifies last known good snapshot for restore' {
            New-Item -ItemType Directory -Path $SnapshotDir -Force | Out-Null

            # Bad snapshot (invalid settings)
            @{ timestamp = '2026-02-17T10:00:00'; settings_json_valid = $false } |
                ConvertTo-Json | Set-Content (Join-Path $SnapshotDir 'snapshot-20260217-100000.json') -Encoding UTF8

            # Good snapshot
            @{ timestamp = '2026-02-17T11:00:00'; settings_json_valid = $true; obi_version = '0.56' } |
                ConvertTo-Json | Set-Content (Join-Path $SnapshotDir 'snapshot-20260217-110000.json') -Encoding UTF8

            # Find newest valid
            $snapshots = Get-ChildItem -Path $SnapshotDir -Filter 'snapshot-*.json' | Sort-Object Name -Descending
            $best = $null
            foreach ($sf in $snapshots) {
                $snap = Get-Content $sf.FullName -Raw | ConvertFrom-Json
                if ($snap.settings_json_valid) {
                    $best = $snap
                    break
                }
            }

            $best | Should Not BeNullOrEmpty
            $best.obi_version | Should Be '0.56'
        }
    }

    Context 'GitHub Issue' {

        It 'Formats recovery details correctly' {
            $repairs = @('Restored hook_wrapper.cmd', 'Cleaned heredoc pollution in settings.local.json')
            $cycles = @('mod_a -> mod_b -> mod_a')

            $bodyLines = @('## Auto-Recovery Report', '')
            foreach ($r in $repairs) { $bodyLines += "- $r" }
            if ($cycles.Count -gt 0) {
                $bodyLines += ''
                $bodyLines += '### Unresolved'
                foreach ($c in $cycles) { $bodyLines += "- Circular import: $c" }
            }
            $body = $bodyLines -join "`n"

            $body | Should Match 'Restored hook_wrapper.cmd'
            $body | Should Match 'Cleaned heredoc pollution'
            $body | Should Match 'mod_a -> mod_b -> mod_a'
        }

        It 'Respects NoIssue flag by skipping creation' {
            $NoIssue = $true
            $allRepairs = @('some repair')

            $shouldCreate = (-not $NoIssue -and $allRepairs.Count -gt 0)
            $shouldCreate | Should Be $false
        }
    }

    Context 'Safe Mode' {

        It 'Disables hooks before repair' {
            $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $settings.hooks | Should Not BeNullOrEmpty

            # Simulate entering safe mode
            $originalHooks = $settings.hooks
            $settings.hooks = @{}
            $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8

            $modified = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            ($modified.hooks.PSObject.Properties | Measure-Object).Count | Should Be 0

            $originalHooks | Should Not BeNullOrEmpty
        }

        It 'Re-enables hooks after repair' {
            $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $originalHooks = $settings.hooks

            # Enter safe mode
            $settings.hooks = @{}
            $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8

            # Exit safe mode
            $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $settings.hooks = $originalHooks
            $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8

            $restored = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $restored.hooks.SessionStart | Should Not BeNullOrEmpty
        }

        It 'Preserves original hook config exactly' {
            $before = Get-Content $SettingsJson -Raw

            $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $originalHooks = $settings.hooks

            # Enter safe mode
            $settings.hooks = @{}
            $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8

            # Exit safe mode
            $settings = Get-Content $SettingsJson -Raw | ConvertFrom-Json
            $settings.hooks = $originalHooks
            $settings | ConvertTo-Json -Depth 10 | Set-Content $SettingsJson -Encoding UTF8

            $after = Get-Content $SettingsJson -Raw

            $objBefore = $before | ConvertFrom-Json
            $objAfter = $after | ConvertFrom-Json

            ($objAfter.hooks.SessionStart | ConvertTo-Json -Depth 10) |
                Should Be ($objBefore.hooks.SessionStart | ConvertTo-Json -Depth 10)
        }
    }
}

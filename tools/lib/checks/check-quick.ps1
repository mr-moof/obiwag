<#
.SYNOPSIS
    Quick structural/environment checks (formerly verify-setup.ps1).

.DESCRIPTION
    Dot-sourced by config-guardian.ps1 AFTER lib/common.ps1 and lib/validation.ps1.
    No script-scope variable assumptions beyond those already set by the dispatcher.

    Provides:
    - Test-QuickSetup  (VS-1..VS-12: env vars, CLI availability, settings parsing,
                        commands deployed, permissions, git access, peer harness)

    VS-13 (hook execution) is intentionally omitted -- it is a runtime check
    already covered by healthcheck.py HC-5.
#>

function Test-QuickSetup {
    <#
    .SYNOPSIS
        Run quick structural and environment verification checks.
    .OUTPUTS
        Hashtable with keys: Valid ([bool]), Errors ([string[]])
    #>
    $results = @{ Valid = $true; Errors = @() }

    # VS-1: CLAUDE_PROJECT_ROOT env var
    $projectRoot = $env:CLAUDE_PROJECT_ROOT
    if ($projectRoot) {
        if (Test-Path $projectRoot) {
            Write-Check "CLAUDE_PROJECT_ROOT is set: $projectRoot"
        } else {
            $results.Valid = $false
            $results.Errors += "CLAUDE_PROJECT_ROOT path does not exist: $projectRoot"
            Write-Problem "CLAUDE_PROJECT_ROOT path does not exist: $projectRoot"
        }
    } else {
        $results.Valid = $false
        $results.Errors += 'CLAUDE_PROJECT_ROOT is not set'
        Write-Problem 'CLAUDE_PROJECT_ROOT is not set'
    }

    # VS-2: Claude Code CLI available
    $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($claudeCmd) {
        Write-Check "Claude Code CLI available: $($claudeCmd.Source)"
    } else {
        $results.Valid = $false
        $results.Errors += 'Claude Code CLI not found in PATH'
        Write-Problem 'Claude Code CLI not found in PATH'
    }

    # VS-3: Python available
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        try {
            $pythonVersion = python --version 2>&1
            Write-Check "Python available: $pythonVersion"
        } catch {
            $results.Valid = $false
            $results.Errors += 'Python found but failed to run'
            Write-Problem 'Python found but failed to run'
        }
    } else {
        $results.Valid = $false
        $results.Errors += 'Python not found in PATH'
        Write-Problem 'Python not found in PATH'
    }

    # VS-4 + VS-5: settings.json exists + hooks section
    $settingsJson = Join-Path $env:USERPROFILE '.claude\settings.json'
    if (Test-Path $settingsJson) {
        Write-Check "settings.json exists"
        try {
            $settings = Get-Content $settingsJson -Raw | ConvertFrom-Json
            if ($settings.hooks) {
                Write-Check "hooks section found in settings.json"
            } else {
                $results.Valid = $false
                $results.Errors += 'No hooks key in settings.json'
                Write-Problem 'No hooks key in settings.json'
            }

            # VS-6: No $HOME variables in settings
            $content = Get-Content $settingsJson -Raw
            if ($content -match '\$HOME') {
                $results.Valid = $false
                $results.Errors += 'settings.json contains $HOME (Windows-incompatible)'
                Write-Problem 'settings.json contains $HOME (Windows-incompatible)'
            } else {
                Write-Check 'No $HOME variables in settings'
            }

            # VS-7: CLAUDE_PROJECT_ROOT usage valid
            if ($content -match '\$\{CLAUDE_PROJECT_ROOT\}' -and -not $projectRoot) {
                $results.Valid = $false
                $results.Errors += 'settings.json references CLAUDE_PROJECT_ROOT but variable is not set'
                Write-Problem 'settings.json references CLAUDE_PROJECT_ROOT but variable is not set'
            } else {
                Write-Check 'CLAUDE_PROJECT_ROOT usage valid'
            }
        } catch {
            $results.Valid = $false
            $results.Errors += "settings.json is invalid JSON: $($_.Exception.Message)"
            Write-Problem "settings.json is invalid JSON: $($_.Exception.Message)"
        }
    } else {
        $results.Valid = $false
        $results.Errors += "settings.json not found at $settingsJson"
        Write-Problem "settings.json not found at $settingsJson"
    }

    # VS-8: Commands deployed
    $commandsDir = Join-Path $env:USERPROFILE '.claude\commands'
    if (Test-Path $commandsDir) {
        $expectedCommands = @(
            'author.md', 'discovery.md', 'integrate.md',
            'learning.md', 'obi.md', 'obi-auto.md', 'obi-auto-max.md', 'obi-collect.md',
            'obi-memory-review.md', 'obi-swarm.md', 'obi-update.md', 'readme.md',
            'readme-review.md', 'release.md', 're-review.md', 'review.md',
            'simplify.md'
        )
        $foundCount = 0
        $missingCommands = @()
        foreach ($cmd in $expectedCommands) {
            if (Test-Path (Join-Path $commandsDir $cmd)) {
                $foundCount++
            } else {
                $missingCommands += $cmd
            }
        }
        if ($foundCount -eq $expectedCommands.Count) {
            Write-Check "Obi commands deployed: $foundCount/$($expectedCommands.Count)"
        } else {
            $results.Valid = $false
            $results.Errors += "Commands incomplete: $foundCount/$($expectedCommands.Count) (missing: $($missingCommands -join ', '))"
            Write-Problem "Commands incomplete: $foundCount/$($expectedCommands.Count)"
        }
    } else {
        $results.Valid = $false
        $results.Errors += "Commands directory not found: $commandsDir"
        Write-Problem "Commands directory not found: $commandsDir"
    }

    # VS-9: Permission count + categorization
    if (Test-Path $settingsJson) {
        try {
            $userJson = Get-Content $settingsJson -Raw | ConvertFrom-Json
            $userPerms = $userJson.permissions.allow
            $userCount = if ($userPerms) { $userPerms.Count } else { 0 }

            if ($userCount -ge 100) {
                Write-Check "User permissions: $userCount (comprehensive)"
            } elseif ($userCount -ge 20) {
                Write-Check "User permissions: $userCount (moderate)"
            } elseif ($userCount -gt 0) {
                Write-Problem "User permissions: $userCount (limited)"
            } else {
                $results.Valid = $false
                $results.Errors += 'No user permissions defined'
                Write-Problem 'No user permissions defined'
            }
        } catch {
            # Already caught in VS-4
        }
    }

    # VS-10: Project-local permission scope. Claude merges permission arrays across
    # scopes; a shorter project allow list does not replace the user allow list.
    # Deny rules retain precedence and are reported when they exactly overlap a
    # user allow rule. Project settings are never grounds for destructive cleanup.
    $projectSettings = if ($projectRoot) {
        Join-Path $projectRoot '.claude\settings.local.json'
    } else {
        Join-Path (Get-Location) '.claude\settings.local.json'
    }
    if (Test-Path $projectSettings) {
        try {
            $projJson = Get-Content $projectSettings -Raw | ConvertFrom-Json
            $projPerms = @($projJson.permissions.allow | Where-Object { $null -ne $_ })
            $projDenies = @($projJson.permissions.deny | Where-Object { $null -ne $_ })
            Write-Check "Project settings: $($projPerms.Count) allow, $($projDenies.Count) deny (arrays merge across scopes)"
            if (Test-Path $settingsJson) {
                $userJson2 = Get-Content $settingsJson -Raw | ConvertFrom-Json
                $userAllows = @($userJson2.permissions.allow | ForEach-Object { [string]$_ })
                $denyConflicts = @($projDenies | Where-Object { $userAllows -contains [string]$_ })
                if ($denyConflicts.Count -gt 0) {
                    Write-Problem "Project deny rules take precedence over matching user allows: $($denyConflicts[0..([Math]::Min(2, $denyConflicts.Count - 1))] -join ', ')"
                }
            }
        } catch {
            Write-Problem "Project settings: failed to parse $projectSettings"
        }
    }

    # VS-11: Git repository accessible
    if ($projectRoot -and (Test-Path $projectRoot)) {
        try {
            Push-Location $projectRoot
            git status 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $branch = git branch --show-current 2>&1
                Write-Check "Git repository accessible (branch: $branch)"
            } else {
                $results.Valid = $false
                $results.Errors += 'Git repository not accessible'
                Write-Problem 'Git repository not accessible'
            }
        } finally {
            Pop-Location
        }
    }

    # VS-12: canonical peer-review harness is complete and legacy entry points are absent.
    $peerFiles = @(
        'peer-review.ps1',
        'peer-review.py',
        'peer_review\__init__.py',
        'peer_review\adapters.py',
        'peer_review\broker.py',
        'peer_review\cli.py',
        'peer_review\io_utils.py',
        'peer_review\packet.py',
        'peer_review\process_control.py',
        'peer_review\runner.py',
        'peer_review\validation.py',
        'schemas\peer-review-request.schema.json',
        'schemas\peer-review-scope-manifest.schema.json',
        'schemas\peer-review-result.schema.json',
        'schemas\peer-review-status.schema.json'
    )
    $missingPeerFiles = @($peerFiles | Where-Object {
        -not (Test-Path -LiteralPath (Join-Path $ScriptDir $_) -PathType Leaf)
    })
    if ($missingPeerFiles.Count -gt 0) {
        $results.Valid = $false
        $results.Errors += "Peer harness incomplete (missing: $($missingPeerFiles -join ', '))"
        Write-Problem "Peer harness incomplete: $($missingPeerFiles.Count) file(s) missing"
    } else {
        Write-Check "Peer harness complete: $($peerFiles.Count)/$($peerFiles.Count)"
    }

    $legacyPeerPaths = @(
        'codex-run.ps1',
        'codex-plan-prep.ps1',
        'schemas\codex-plan-critique.schema.json'
    )
    $activeLegacyPaths = @($legacyPeerPaths | Where-Object {
        Test-Path -LiteralPath (Join-Path $ScriptDir $_)
    })
    if ($activeLegacyPaths.Count -gt 0) {
        $results.Valid = $false
        $results.Errors += "Retired peer-review paths remain active: $($activeLegacyPaths -join ', ')"
        Write-Problem "Retired peer-review paths remain active: $($activeLegacyPaths -join ', ')"
    }

    $stalePeerSkill = Join-Path $env:USERPROFILE '.agents\skills\codex-adversarial-review'
    if (Test-Path -LiteralPath $stalePeerSkill) {
        $results.Valid = $false
        $results.Errors += "Retired unmanaged peer skill remains active: $stalePeerSkill"
        Write-Problem "Retired unmanaged peer skill remains active: $stalePeerSkill"
    } else {
        Write-Check 'Retired unmanaged peer skill absent'
    }

    return $results
}

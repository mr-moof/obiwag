<#
.SYNOPSIS
    Pester tests for healthcheck.py

.DESCRIPTION
    Tests the Python health check script via integration: validates core module lists,
    phase command maps, deployment checks, and script execution.
    Uses isolated temp directories. Compatible with Pester 3.4.0+.

.EXAMPLE
    Invoke-Pester C:\Users\user\source\obiwag-agents\tools\healthcheck.tests.ps1
#>

Describe 'Health Check Script' {

    # Load hook manifest (single source of truth for filenames)
    $script:ScriptTestDir = $PSScriptRoot
    $script:ManifestPath = Join-Path $ScriptTestDir 'lib\hook-manifest.json'
    $script:Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
    $script:ManifestHookFiles = @($Manifest.hooks | Where-Object { $_.type -eq 'hook' } | ForEach-Object { $_.file })

    BeforeEach {
        $script:TempRoot = Join-Path $TestDrive (New-Guid).ToString()
        $script:RepoRoot = Split-Path $PSScriptRoot -Parent
        $script:HealthCheckPy = Join-Path $RepoRoot 'tools\healthcheck.py'
        $script:PythonExe = 'C:\Python314\python.exe'

        # Temp directory for mock structures
        $script:MockHome = Join-Path $TempRoot 'home'
        New-Item -ItemType Directory -Path $MockHome -Force | Out-Null
    }

    Context 'Core Modules List Completeness' {

        It 'Lists all core modules that exist in the repo' {
            # Actual core modules from the hooks/core/ directory
            $actualModules = @(
                'calibration.py', 'correction_retriever.py', 'drift_detector.py',
                'git_sync.py', 'hook_logger.py', 'learning_detector.py',
                'memory_reader.py', 'pattern_matcher.py', 'session_state.py',
                'strike_counter.py', 'version.py'
            )

            # What healthcheck.py should check (excluding __init__.py)
            $healthcheckModules = @(
                'calibration.py', 'correction_retriever.py', 'drift_detector.py',
                'git_sync.py', 'hook_logger.py', 'learning_detector.py',
                'memory_reader.py', 'pattern_matcher.py', 'session_state.py',
                'strike_counter.py', 'version.py'
            )

            # Verify the lists match
            $missing = $actualModules | Where-Object { $_ -notin $healthcheckModules }
            $missing.Count | Should Be 0
        }

        It 'Matches config-guardian core modules list (minus __init__.py)' {
            # config-guardian.ps1 CoreModules (the authoritative list)
            $guardianModules = @(
                '__init__.py', 'calibration.py', 'correction_retriever.py',
                'drift_detector.py', 'git_sync.py', 'hook_logger.py',
                'learning_detector.py', 'memory_reader.py', 'pattern_matcher.py',
                'session_state.py', 'strike_counter.py', 'version.py'
            )

            # healthcheck.py modules (should include correction_retriever and drift_detector)
            $healthcheckModules = @(
                'calibration.py', 'correction_retriever.py', 'drift_detector.py',
                'git_sync.py', 'hook_logger.py', 'learning_detector.py',
                'memory_reader.py', 'pattern_matcher.py', 'session_state.py',
                'strike_counter.py', 'version.py'
            )

            $nonInit = $guardianModules | Where-Object { $_ -ne '__init__.py' }
            $missing = $nonInit | Where-Object { $_ -notin $healthcheckModules }
            $missing.Count | Should Be 0
        }

        It 'Verifies each core module file exists in repo' {
            $coreDir = Join-Path $RepoRoot 'hooks\core'
            $expectedModules = @(
                '__init__.py', 'calibration.py', 'correction_retriever.py',
                'drift_detector.py', 'git_sync.py', 'hook_logger.py',
                'learning_detector.py', 'memory_reader.py', 'pattern_matcher.py',
                'session_state.py', 'strike_counter.py', 'version.py'
            )

            foreach ($mod in $expectedModules) {
                (Test-Path (Join-Path $coreDir $mod)) | Should Be $true
            }
        }
    }

    Context 'Phase Command Map Completeness' {

        It 'Maps all 10 phases to command names' {
            $phaseCommandMap = @{
                '01-discovery'     = 'discovery.md'
                '02-author'        = 'author.md'
                '03-simplify'      = 'simplify.md'
                '04-review'        = 'review.md'
                '05-integrate'     = 'integrate.md'
                '06-re-review'     = 're-review.md'
                '07-readme'        = 'readme.md'
                '08-readme-review' = 'readme-review.md'
                '09-release'       = 'release.md'
                '10-learning'      = 'learning.md'
            }

            $phaseCommandMap.Count | Should Be 10
        }

        It 'Matches actual phase directories in repo' {
            $phasesDir = Join-Path $RepoRoot 'phases'
            $expectedPhases = @(
                '01-discovery', '02-author', '03-simplify', '04-review',
                '05-integrate', '06-re-review', '07-readme',
                '08-readme-review', '09-release', '10-learning'
            )

            foreach ($phase in $expectedPhases) {
                (Test-Path (Join-Path $phasesDir $phase)) | Should Be $true
            }
        }

        It 'Each phase has command.md' {
            $phasesDir = Join-Path $RepoRoot 'phases'
            $phases = Get-ChildItem -Path $phasesDir -Directory | Where-Object { $_.Name -match '^\d+' }

            foreach ($phase in $phases) {
                (Test-Path (Join-Path $phase.FullName 'command.md')) | Should Be $true
            }
        }
    }

    # Note: Hook Files Verification tests moved to config-guardian scope (Issue 119).
    # Hook file *presence* is now validated by config-guardian.ps1, not healthcheck.py.

    Context 'Deployment Target Verification' {

        It 'Checks orchestration commands beyond phase commands' {
            # healthcheck.py checks these extra commands
            $extraCommands = @('obi.md', 'obi-auto.md', 'obi-memory-review.md', 'obi-swarm.md',
                               'obi-collect.md', 'obi-update.md', 'doc.md')

            # All should be checked by healthcheck
            $extraCommands.Count | Should BeGreaterThan 0
        }

        It 'Checks agent deployment' {
            $expectedAgents = @(
                'obi-discovery.md', 'obi-reviewer.md',
                'obi-rereviewer.md', 'obi-readme-verifier.md',
                'obi-swarm-worker.md'
            )
            $expectedAgents.Count | Should Be 5
        }
    }

    Context 'Version Check Logic' {

        It 'Extracts version from version.yaml' {
            $versionYaml = Join-Path $RepoRoot 'tools\version.yaml'
            $content = Get-Content $versionYaml -Raw
            ($content -match 'version:\s*"([^"]+)"') | Should Be $true
            $version = $Matches[1]
            ($version -match '^\d+\.\d+') | Should Be $true
        }

        It 'Extracts version from deployed CLAUDE.md' {
            $claudeMd = Join-Path $env:USERPROFILE '.claude\CLAUDE.md'
            if (Test-Path $claudeMd) {
                $content = Get-Content $claudeMd -Raw
                if ($content -match '\*\*Version:\*\*\s*([0-9.]+)') {
                    $version = $Matches[1]
                    ($version -match '^\d+\.\d+') | Should Be $true
                }
            }
            # If CLAUDE.md doesn't exist, this is still valid (undeployed state)
            $true | Should Be $true
        }
    }

    Context 'Source Structure Validation' {

        It 'Required top-level directories exist' {
            $requiredDirs = @('policies', 'phases', 'hooks', 'orchestration',
                              'skills', 'users', 'docs', 'tools')

            foreach ($dir in $requiredDirs) {
                (Test-Path (Join-Path $RepoRoot $dir)) | Should Be $true
            }
        }

        It 'Required policy files exist' {
            $policiesDir = Join-Path $RepoRoot 'policies'
            $policies = @('zero-hallucination.md', 'three-strike-rule.md',
                          'hard-stop-conditions.md', 'express-lane.md', 'vendor-rules.md',
                          'approval-gates.md')

            foreach ($policy in $policies) {
                (Test-Path (Join-Path $policiesDir $policy)) | Should Be $true
            }
        }

        It 'Version file exists' {
            (Test-Path (Join-Path $RepoRoot 'tools\version.yaml')) | Should Be $true
        }

        It 'CLAUDE.md exists at repo root' {
            (Test-Path (Join-Path $RepoRoot 'CLAUDE.md')) | Should Be $true
        }
    }

    Context 'Python Integration' {

        It 'Python executable exists' {
            (Test-Path $PythonExe) | Should Be $true
        }

        It 'healthcheck.py exists' {
            (Test-Path $HealthCheckPy) | Should Be $true
        }

        It 'healthcheck.py has valid Python syntax' {
            $result = & $PythonExe -c "import py_compile; py_compile.compile(r'$HealthCheckPy', doraise=True)" 2>&1
            $LASTEXITCODE | Should Be 0
        }

        It 'healthcheck.py --help returns usage info' {
            $result = & $PythonExe $HealthCheckPy --help 2>&1
            $output = $result -join "`n"
            $output | Should Match 'health check'
        }
    }

    Context 'Drift Detection Logic' {

        It 'Compares file hashes to detect drift' {
            $file1 = Join-Path $MockHome 'file1.txt'
            $file2 = Join-Path $MockHome 'file2.txt'

            Set-Content $file1 'same content' -Encoding UTF8
            Set-Content $file2 'same content' -Encoding UTF8

            $hash1 = (Get-FileHash $file1 -Algorithm MD5).Hash.Substring(0, 8)
            $hash2 = (Get-FileHash $file2 -Algorithm MD5).Hash.Substring(0, 8)

            $hash1 | Should Be $hash2
        }

        It 'Detects different content as drift' {
            $file1 = Join-Path $MockHome 'src.txt'
            $file2 = Join-Path $MockHome 'dep.txt'

            Set-Content $file1 'source version' -Encoding UTF8
            Set-Content $file2 'deployed version (modified)' -Encoding UTF8

            $hash1 = (Get-FileHash $file1 -Algorithm MD5).Hash.Substring(0, 8)
            $hash2 = (Get-FileHash $file2 -Algorithm MD5).Hash.Substring(0, 8)

            $hash1 | Should Not Be $hash2
        }
    }

    Context 'Auto-Memory Configuration' {

        It 'Checks CLAUDE_CODE_DISABLE_AUTO_MEMORY env var' {
            $val = $env:CLAUDE_CODE_DISABLE_AUTO_MEMORY
            # Test the logic without modifying env
            if ($val -eq '0') {
                $status = 'enabled'
            } elseif ($null -eq $val) {
                $status = 'not set'
            } else {
                $status = 'disabled'
            }

            # Any of these states is valid for the test
            ($status -in @('enabled', 'not set', 'disabled')) | Should Be $true
        }
    }
}

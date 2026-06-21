<#
.SYNOPSIS
    Pester 3.4 tests for tools/run-grep-gates.ps1.

.DESCRIPTION
    Tests use $TestDrive as the repo root for an isolated, hermetic scan.
    Real Select-String is exercised; no mocking.
#>

$ScriptPath = Join-Path $PSScriptRoot 'run-grep-gates.ps1'

Describe 'run-grep-gates' {

    BeforeEach {
        # Pester 3.4 shares $TestDrive across Its in a Describe; wipe $RepoRoot
        # explicitly so files from a prior test do not pollute the next scan.
        $script:RepoRoot = Join-Path $TestDrive 'fakerepo'
        if (Test-Path $RepoRoot) {
            Remove-Item -LiteralPath $RepoRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $RepoRoot -Force | Out-Null
        $script:PlanPath = Join-Path $TestDrive 'plan.md'
    }

    Context 'No verification block' {

        It 'returns clean when plan lacks verification: block' {
            Set-Content -Path $PlanPath -Value '# Just a plan, no verification' -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot
            ($output -join "`n") -match 'no verification' | Should Be $true
        }
    }

    Context 'Phase has no matching entry' {

        It 'returns clean when no entry matches the phase' {
            $body = @"
verification:
  grep:
    - after_phase: 7
      forbidden_patterns:
        - "TODO"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot
            ($output -join "`n") -match 'no entries with after_phase=5' | Should Be $true
        }
    }

    Context 'Forbidden pattern detection' {

        It 'exits with FAIL output when forbidden pattern appears' {
            $body = @"
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "TODO\\(prereq\\)"
      fail_message: "Placeholder still present after Integrate"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8

            # Create a file with the forbidden pattern
            $bad = Join-Path $RepoRoot 'src.ps1'
            Set-Content -Path $bad -Value 'TODO(prereq) finish this' -Encoding UTF8

            $output = & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot
            $combined = $output -join "`n"
            $combined -match 'GREP GATE FAIL' | Should Be $true
            $combined -match 'Placeholder still present' | Should Be $true
        }

        It 'returns clean when forbidden pattern is absent' {
            $body = @"
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "TODO\\(prereq\\)"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8

            $clean = Join-Path $RepoRoot 'src.ps1'
            Set-Content -Path $clean -Value 'function Foo {}' -Encoding UTF8

            $output = & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot
            ($output -join "`n") -match 'clean' | Should Be $true
        }
    }

    Context 'allow_files exemption' {

        It 'skips files matching allow_files regex (plain pattern)' {
            $body = @"
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "TODO\\(prereq\\)"
      allow_files:
        - "docs/templates/.*"
      fail_message: "Placeholder still present"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8

            $tplDir = Join-Path $RepoRoot 'docs\templates'
            New-Item -ItemType Directory -Path $tplDir -Force | Out-Null
            Set-Content -Path (Join-Path $tplDir 'sample.md') -Value 'TODO(prereq) here is fine' -Encoding UTF8

            $output = & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot
            ($output -join "`n") -match 'clean' | Should Be $true
        }

        It 'unescapes YAML backslashes in allow_files (Round 2 regression)' {
            # Regression: round-2 review found that allow_files entries with
            # YAML-escaped regex (e.g. `phases/.*/command\\.md$`) were stored
            # raw, so they failed to match real paths like `phases/04-review/command.md`.
            $body = @"
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "FORBIDDEN_LEAK"
      allow_files:
        - "phases/.*/command\\.md$"
      fail_message: "Should not surface for command.md files"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8

            # Plant a match in a path that matches the unescaped regex
            $phaseDir = Join-Path $RepoRoot 'phases\04-review'
            New-Item -ItemType Directory -Path $phaseDir -Force | Out-Null
            Set-Content -Path (Join-Path $phaseDir 'command.md') -Value 'has FORBIDDEN_LEAK in body' -Encoding UTF8

            $output = & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot
            # If the unescape worked, this file is exempt and the gate is clean.
            ($output -join "`n") -match 'clean' | Should Be $true
        }
    }

    Context 'JSON failure artifact' {

        It 'writes grep-gate-<phase>-<UTC>.json on failure' {
            $body = @"
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "FORBIDDEN_TOKEN_XYZ"
      fail_message: "Token must not appear"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $bad = Join-Path $RepoRoot 'leak.txt'
            Set-Content -Path $bad -Value 'this contains FORBIDDEN_TOKEN_XYZ here' -Encoding UTF8

            & $ScriptPath -PlanPath $PlanPath -Phase 5 -RepoRoot $RepoRoot 2>&1 | Out-Null

            $artifactDir = Join-Path $RepoRoot '.obi\runtime'
            (Test-Path $artifactDir) | Should Be $true
            $artifacts = Get-ChildItem $artifactDir -Filter 'grep-gate-5-*.json'
            @($artifacts).Count -gt 0 | Should Be $true

            $payload = Get-Content $artifacts[0].FullName -Raw | ConvertFrom-Json
            $payload.phase | Should Be 5
            @($payload.matches).Count -gt 0 | Should Be $true
            $payload.fail_message | Should Be 'Token must not appear'
            @($payload.proposed_allow_files).Count -gt 0 | Should Be $true
        }
    }

    Context 'Error handling' {

        It 'exits non-zero when plan file missing' {
            $missing = Join-Path $TestDrive 'no-such.md'
            $err = & $ScriptPath -PlanPath $missing -Phase 5 -RepoRoot $RepoRoot 2>&1
            ($err -join "`n") -match 'Plan file not found' | Should Be $true
        }
    }
}

Describe 'run-grep-gates -VersionDrift' {
    # The drift gate checks the two framework-version banner files that
    # bump-version.ps1 writes: README.md (**v<ver>**) and
    # docs/hooks-architecture.md (**Version:** <ver>). Pass = every target
    # banner equals version.yaml; fail = any is stale or missing. Scope is the
    # bump targets only, so doc-level **Version:** headers elsewhere are out of
    # scope by design.

    BeforeEach {
        # Hermetic fake repo per test (see plan-mode tests above for rationale).
        $script:RepoRoot = Join-Path $TestDrive 'driftrepo'
        if (Test-Path $RepoRoot) {
            Remove-Item -LiteralPath $RepoRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'tools') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'docs') -Force | Out-Null

        $script:CurrentVersion = '0.69.39'

        # Source of truth.
        Set-Content -Path (Join-Path $RepoRoot 'tools\version.yaml') -Value @"
# Obi Wag Version Information
version: "$CurrentVersion"
edition: ""
last_updated: "2026-06-12"
"@ -Encoding UTF8

        # The two bump-target banners, all at the current version (clean).
        $script:ReadmeMd  = Join-Path $RepoRoot 'README.md'
        $script:HooksMd   = Join-Path $RepoRoot 'docs\hooks-architecture.md'

        Set-Content -Path $ReadmeMd -Value @"
# ObiWag

**v$CurrentVersion**
"@ -Encoding UTF8

        Set-Content -Path $HooksMd -Value @"
# Hooks

> **Last Updated:** 2026-06-12 | **Version:** $CurrentVersion
"@ -Encoding UTF8
    }

    Context 'Clean tree' {

        It 'passes when every bump-target banner matches version.yaml' {
            $output = & $ScriptPath -VersionDrift -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 0
            ($output -join "`n") -match 'VERSION DRIFT GATE: clean' | Should Be $true
        }

        It 'does NOT police document-level **Version:** headers outside the bump targets' {
            # Pervasive in the real repo: policy/doc headers carry their own doc
            # version (e.g. 1.0) and future-proposal versions (e.g. 0.70.0).
            # These are not bump targets, so the gate must ignore them.
            Set-Content -Path (Join-Path $RepoRoot 'docs\some-policy.md') -Value @"
> **Version:** 1.0 | **Last Updated:** 2026-02-02
"@ -Encoding UTF8
            Set-Content -Path (Join-Path $RepoRoot 'docs\future-proposal.md') -Value @"
> **Status:** Future | **Version:** 0.70.0 | **Date:** 2026-03-22
"@ -Encoding UTF8
            $output = & $ScriptPath -VersionDrift -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 0
            ($output -join "`n") -match 'clean' | Should Be $true
        }
    }

    Context 'Stale-version fixture' {

        It 'fails when a bump-target banner carries a stale version' {
            # Deliberate drift: hooks-architecture.md left at a previous release
            # (simulates a bump that did not fully propagate).
            Set-Content -Path $HooksMd -Value @"
# Hooks

> **Last Updated:** 2026-05-01 | **Version:** 0.69.30
"@ -Encoding UTF8
            $output = & $ScriptPath -VersionDrift -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 1
            $combined = $output -join "`n"
            $combined -match 'VERSION DRIFT GATE FAIL' | Should Be $true
            $combined -match 'hooks-architecture\.md' | Should Be $true
            $combined -match "found '0\.69\.30'" | Should Be $true
        }

        It 'fails on a stale **vX** README banner too' {
            Set-Content -Path $ReadmeMd -Value @"
# ObiWag

**v0.69.28**
"@ -Encoding UTF8
            $output = & $ScriptPath -VersionDrift -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 1
            ($output -join "`n") -match 'VERSION DRIFT GATE FAIL' | Should Be $true
        }

        It 'fails when a bump-target file is missing entirely' {
            Remove-Item -LiteralPath $HooksMd -Force
            $output = & $ScriptPath -VersionDrift -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 1
            ($output -join "`n") -match 'MISSING FILE|NO BANNER' | Should Be $true
        }
    }

    Context 'Error handling' {

        It 'exits 2 when version.yaml is missing' {
            Remove-Item -LiteralPath (Join-Path $RepoRoot 'tools\version.yaml') -Force
            & $ScriptPath -VersionDrift -RepoRoot $RepoRoot 2>&1 | Out-Null
            $LASTEXITCODE | Should Be 2
        }
    }
}

Describe 'run-grep-gates -BarePass' {
    # The bare-pass gate (OPT-05 #178) fails if any `except Exception:` is
    # immediately followed by `pass` under hooks/, outside the logging-infra
    # files (hook_logger.py, hook_error_handler.py) and tests/.

    BeforeEach {
        $script:RepoRoot = Join-Path $TestDrive 'bprepo'
        if (Test-Path $RepoRoot) {
            Remove-Item -LiteralPath $RepoRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'hooks\core') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $RepoRoot 'hooks\tests') -Force | Out-Null

        # Clean production file: broad except routes through log_swallowed.
        Set-Content -Path (Join-Path $RepoRoot 'hooks\session_start.py') -Value @"
def f():
    try:
        risky()
    except Exception as exc:
        log_swallowed("f", exc)
"@ -Encoding UTF8
    }

    Context 'Clean tree' {

        It 'passes when no bare except-pass exists under hooks/' {
            $output = & $ScriptPath -BarePass -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 0
            ($output -join "`n") -match 'BARE-PASS GATE: clean' | Should Be $true
        }

        It 'ignores narrow typed except-pass (idiomatic expected-failure)' {
            Set-Content -Path (Join-Path $RepoRoot 'hooks\core\narrow.py') -Value @"
def g():
    try:
        load()
    except OSError:
        pass
"@ -Encoding UTF8
            $output = & $ScriptPath -BarePass -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 0
        }

        It 'exempts logging-infra files (hook_logger.py)' {
            Set-Content -Path (Join-Path $RepoRoot 'hooks\core\hook_logger.py') -Value @"
def _rotate():
    try:
        rotate()
    except Exception:
        pass
"@ -Encoding UTF8
            $output = & $ScriptPath -BarePass -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 0
        }

        It 'ignores tests/ fixtures' {
            Set-Content -Path (Join-Path $RepoRoot 'hooks\tests\test_x.py') -Value @"
def test():
    try:
        thing()
    except Exception:
        pass
"@ -Encoding UTF8
            $output = & $ScriptPath -BarePass -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 0
        }
    }

    Context 'Violation present' {

        It 'fails on a bare except Exception: pass in a production hook file' {
            Set-Content -Path (Join-Path $RepoRoot 'hooks\core\bad.py') -Value @"
def h():
    try:
        risky()
    except Exception:
        pass
"@ -Encoding UTF8
            $output = & $ScriptPath -BarePass -RepoRoot $RepoRoot
            $LASTEXITCODE | Should Be 1
            $combined = $output -join "`n"
            $combined -match 'BARE-PASS GATE FAIL' | Should Be $true
            $combined -match 'hooks/core/bad\.py' | Should Be $true
        }
    }

    Context 'Error handling' {

        It 'exits 2 when hooks/ is missing' {
            Remove-Item -LiteralPath (Join-Path $RepoRoot 'hooks') -Recurse -Force
            & $ScriptPath -BarePass -RepoRoot $RepoRoot 2>&1 | Out-Null
            $LASTEXITCODE | Should Be 2
        }
    }
}

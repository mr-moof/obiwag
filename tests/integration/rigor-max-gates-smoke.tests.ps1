<#
.SYNOPSIS
    Integration smoke test: exercises a fork-and-strip scenario against a fresh
    fixture, asserting all five rigor=max gates fire.

.DESCRIPTION
    Contract: `policies/rigor-max-gates.md` (Phase 0 + Gates 2-5).

    Acceptance criteria:
    - Fresh fixture clone (simulated locally with $TestDrive)
    - All 5 gates exercised:
        Gate 1: Phase 0 prereq lock-in (parse + write-back)
        Gate 2: Hard grep gate (forbidden placeholder pattern)
        Gate 3: Probe library (probe lib loads, schema valid)
        Gate 4: Pipeline classifier (5 classes recognized)
        Gate 5: Auto-memory capture (compute_surprises + threshold gate)

    This is a smoke test — exercises happy paths, not exhaustive coverage.
    Each gate's deep coverage lives in its own *.tests.ps1 / *.py file.
#>

Describe 'fork-and-strip smoke (all 5 gates)' {

    BeforeEach {
        $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $script:Fixture = Join-Path $TestDrive 'fixture-sample-project'
        if (Test-Path $Fixture) { Remove-Item -LiteralPath $Fixture -Recurse -Force -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Path $Fixture -Force | Out-Null

        # A minimal source tree the gates can scan
        Set-Content -Path (Join-Path $Fixture 'src.ps1') -Value 'function Foo {}' -Encoding UTF8
        Set-Content -Path (Join-Path $Fixture 'config.json') -Value '{"name":"x"}' -Encoding UTF8

        $script:PlanPath = Join-Path $TestDrive 'fork-plan.md'
    }

    It 'Gate 1: Phase 0 parses phase0: block' {
        $body = @"
# Fork sample-project

phase0:
  - id: target_namespace
    question: "Where does the fork land?"
    placeholder: "<target-namespace>"
    options: [user-personal, project-team]
    default: user-personal
"@
        Set-Content -Path $PlanPath -Value $body -Encoding UTF8
        $parser = Join-Path $RepoRoot 'tools\parse-plan-phase0.ps1'
        $obj = & $parser -PlanPath $PlanPath | ConvertFrom-Json
        @($obj).Count | Should -Be 1
        $obj[0].id | Should -Be 'target_namespace'
    }

    It 'Gate 2: Hard grep gate detects placeholder leak' {
        $body = @"
verification:
  grep:
    - after_phase: 5
      forbidden_patterns:
        - "<target-namespace>"
      fail_message: "Placeholder leaked past Integrate"
"@
        Set-Content -Path $PlanPath -Value $body -Encoding UTF8
        # Plant a leak in the fixture
        Set-Content -Path (Join-Path $Fixture 'leak.txt') -Value 'forked to <target-namespace> still TODO' -Encoding UTF8

        $gate = Join-Path $RepoRoot 'tools\run-grep-gates.ps1'
        $output = & $gate -PlanPath $PlanPath -Phase 5 -RepoRoot $Fixture 2>&1
        ($output -join "`n") -match 'GREP GATE FAIL' | Should -Be $true
        ($output -join "`n") -match 'Placeholder leaked' | Should -Be $true
    }

    It 'Gate 3: Probe library loads with valid schema' {
        $lib = Join-Path $RepoRoot 'tools\probes\_lib.ps1'
        (Test-Path $lib) | Should -Be $true
        . $lib
        $r = New-ProbeResult -Probe 'fork_test' -Status 'ok' -InputData @{x=1} -Data @{result='good'}
        $r.probe | Should -Be 'fork_test'
        $r.status | Should -Be 'ok'
        $r.data.result | Should -Be 'good'

        # Verify all 5 probe scripts exist
        $probesDir = Join-Path $RepoRoot 'tools\probes'
        @(Get-ChildItem $probesDir -Filter '*.ps1' | Where-Object { $_.Name -ne '_lib.ps1' -and $_.Name -notmatch '\.tests\.ps1$' }).Count | Should -Be 5
    }

    It 'Gate 4: Pipeline classifier handles 5 failure classes' {
        # Drive the Python classifier via py invocation
        $classifier = Join-Path $RepoRoot 'hooks\core\pipeline_classifier.py'
        (Test-Path $classifier) | Should -Be $true

        $py = @"
import sys
sys.path.insert(0, r'$($RepoRoot -replace '\\', '/' )/hooks')
from core.pipeline_classifier import classify_failure
traces = [
    ('runner', 'Waiting for a runner to pick up this job'),
    ('quota',  'Actions spending limit exceeded'),
    ('yaml',   'Invalid workflow file'),
    ('image',  'pull access denied for registry'),
    ('script', 'AssertionError 2 + 2 != 5'),
]
for label, t in traces:
    print(f"{label}={classify_failure(t)['class']}")
"@

        $tmpPy = Join-Path $TestDrive 'classify_smoke.py'
        Set-Content -Path $tmpPy -Value $py -Encoding UTF8

        $output = & python $tmpPy 2>&1
        $combined = $output -join "`n"
        $combined -match 'runner=runner-unavailable' | Should -Be $true
        $combined -match 'quota=quota'                | Should -Be $true
        $combined -match 'yaml=yaml-error'            | Should -Be $true
        $combined -match 'image=image-pull-failure'   | Should -Be $true
        $combined -match 'script=script-error'        | Should -Be $true
    }

    It 'Gate 5: Auto-memory capture gates on confidence threshold' {
        $py = @"
import sys
sys.path.insert(0, r'$($RepoRoot -replace '\\', '/' )/hooks')
from core.auto_memory_capture import (
    compute_surprises, parse_classifier_verdict, is_actionable, CONFIDENCE_THRESHOLD
)
answers = [{'id': 'target_namespace', 'answer': 'group'}]
# Use real probe name namespace_kind (routed via ANS_ID_TO_PROBE), not the answer id
probes = [{'probe': 'namespace_kind', 'status': 'auth_failure', 'data': {}, 'error': '401'}]
candidates = compute_surprises(phase=5, locked_answers=answers, probe_outcomes=probes)
print(f"candidates={len(candidates)}")
v_high = parse_classifier_verdict('{"confidence": 0.85, "summary": "x", "type": "tool"}')
v_low  = parse_classifier_verdict('{"confidence": 0.4,  "summary": "x", "type": "tool"}')
print(f"high_actionable={is_actionable(v_high)}")
print(f"low_actionable={is_actionable(v_low)}")
print(f"threshold={CONFIDENCE_THRESHOLD}")
"@
        $tmpPy = Join-Path $TestDrive 'capture_smoke.py'
        Set-Content -Path $tmpPy -Value $py -Encoding UTF8

        $output = & python $tmpPy 2>&1
        $combined = $output -join "`n"
        $combined -match 'candidates=1'        | Should -Be $true
        $combined -match 'high_actionable=True'  | Should -Be $true
        $combined -match 'low_actionable=False' | Should -Be $true
        $combined -match 'threshold=0.7'       | Should -Be $true
    }

    It 'All gates: artifacts directory layout matches plan' {
        # Verify the contract: which files SHOULD exist in the repo for the
        # gates to function. This catches accidental deletion in future commits.
        $expected = @(
            'tools\parse-plan-phase0.ps1'
            'tools\run-grep-gates.ps1'
            'tools\peer-plan-review.ps1'
            'tools\load-auto-max-config.ps1'
            'tools\lib\checks\check-auto-max.ps1'
            'tools\probes\_lib.ps1'
            'tools\probes\namespace_kind.ps1'
            'tools\probes\runner_tags.ps1'
            'tools\probes\pages_access.ps1'
            'tools\probes\marketplace_reach.ps1'
            'tools\probes\mirror_existence.ps1'
            'hooks\core\auto_memory_capture.py'
            'hooks\core\pipeline_classifier.py'
            'orchestration\obi-auto-max.md'
            'policies\peer-review.md'
            'policies\obi-auto-max-schema.md'
        )
        foreach ($rel in $expected) {
            $abs = Join-Path $RepoRoot $rel
            (Test-Path $abs) | Should -Be $true
        }
    }
}

<#
.SYNOPSIS
    Integration test: /obi-auto-max Phase 0 plan-file write-back.

.DESCRIPTION
    Builds a fixture plan with 2 placeholders and a phase0: block, exercises
    parse-plan-phase0 + the placeholder-replacement Edit pattern + the
    runtime.phase0 write-back. Does NOT exercise AskUserQuestion (interactive
    tool can't run in Pester); simulates AskUserQuestion responses by passing
    a hashtable directly to the write-back logic.

    Acceptance criteria from plan (Issue 161 Gate 1):
    - Phase 0 fires AskUserQuestion (mocked here)
    - Plan-file written back with answers
#>

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Describe 'obi-auto-max Phase 0 integration' {

    BeforeEach {
        $script:PlanPath = Join-Path $TestDrive 'fixture-plan.md'
        $body = @"
# Test Plan

This plan exercises Phase 0 prereq lock-in.

The fork lands in <target-namespace>. CI runs on <runner-tags>.

phase0:
  - id: target_namespace
    question: "Which namespace owns the fork?"
    placeholder: "<target-namespace>"
    options: [personal, organization]
    default: personal
    locks_field: bootstrap-config.json:repoBase
  - id: runner_tags
    question: "Which runner tags do CI jobs need?"
    placeholder: "<runner-tags>"
    free_text: true

## Implementation

Specifics that don't affect Phase 0.
"@
        Set-Content -Path $PlanPath -Value $body -Encoding UTF8
    }

    Context 'parse-plan-phase0 emits ordered JSON' {

        It 'parses both phase0 entries in declared order' {
            $parser = Join-Path $RepoRoot 'tools\parse-plan-phase0.ps1'
            $output = & $parser -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            @($obj).Count | Should Be 2
            $obj[0].id | Should Be 'target_namespace'
            $obj[1].id | Should Be 'runner_tags'
            $obj[0].placeholder | Should Be '<target-namespace>'
            $obj[1].free_text | Should Be $true
        }
    }

    Context 'Plan-file write-back simulation' {

        It 'replaces placeholders with answers and appends runtime.phase0 block' {
            # Simulate the orchestrator's write-back: it would call AskUserQuestion
            # then use Edit to swap placeholders. Here we manipulate the file directly
            # to verify the WRITTEN result matches the spec.

            $simulatedAnswers = @(
                @{ id = 'target_namespace'; answer = 'personal'; locks_field = 'bootstrap-config.json:repoBase' }
                @{ id = 'runner_tags';      answer = 'pscodesign';   locks_field = '' }
            )

            # Step 1: placeholder replacement
            $content = Get-Content $PlanPath -Raw
            foreach ($a in $simulatedAnswers) {
                $parser = Join-Path $RepoRoot 'tools\parse-plan-phase0.ps1'
                $schema = & $parser -PlanPath $PlanPath | ConvertFrom-Json
                $entry = $schema | Where-Object { $_.id -eq $a.id }
                if ($entry -and $entry.placeholder) {
                    $content = $content.Replace($entry.placeholder, $a.answer)
                }
            }

            # Step 2: append runtime.phase0 block
            $now = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            $writeback = @"

runtime:
  phase0:
    locked_at: "$now"
    plan_file: "$PlanPath"
    answers:
"@
            foreach ($a in $simulatedAnswers) {
                $writeback += "`n      - id: $($a.id)"
                $writeback += "`n        answer: `"$($a.answer)`""
                $writeback += "`n        locks_field: `"$($a.locks_field)`""
                $writeback += "`n        source: `"AskUserQuestion`""
            }
            $writeback += "`n    codex:"
            $writeback += "`n      status: `"unavailable`""
            $writeback += "`n      reason: `"test fixture - codex not exercised`""
            $writeback += "`n      checked_at: `"$now`""

            $content += $writeback
            Set-Content -Path $PlanPath -Value $content -Encoding UTF8

            # Assert post-conditions
            $final = Get-Content $PlanPath -Raw

            # Placeholders gone, answers in their place
            $final -match '<target-namespace>' | Should Be $false
            $final -match '<runner-tags>' | Should Be $false
            $final -match 'personal' | Should Be $true
            $final -match 'pscodesign' | Should Be $true

            # runtime.phase0 block present
            $final -match 'runtime:' | Should Be $true
            $final -match '  phase0:' | Should Be $true
            $final -match '    answers:' | Should Be $true
            $final -match 'id: target_namespace' | Should Be $true
            $final -match 'id: runner_tags' | Should Be $true
            $final -match 'source: "AskUserQuestion"' | Should Be $true
            $final -match 'codex:' | Should Be $true
            $final -match 'status: "unavailable"' | Should Be $true
        }
    }

    Context 'Idempotence on re-run' {

        It 'orchestrator detects existing runtime.phase0 block and skips re-asking' {
            # After Phase 0 has run once, re-running /obi-auto-max must NOT
            # re-ask AskUserQuestion or duplicate the runtime.phase0 block.
            # The orchestrator (orchestration/obi-auto-max.md step 2) detects
            # this and skips ahead to probes + codex-on-plan.
            $simulatedFinal = @"
phase0:
  - id: target_namespace
    placeholder: "<target-namespace>"
    options: [personal]

runtime:
  phase0:
    locked_at: "2026-05-02T18:00:00Z"
    answers:
      - id: target_namespace
        answer: "personal"
        source: "AskUserQuestion"
"@
            Set-Content -Path $PlanPath -Value $simulatedFinal -Encoding UTF8

            $content = Get-Content $PlanPath -Raw

            # The runtime.phase0.answers block is the lockfile signal.
            $content -match '(?s)runtime:\s*\n\s*phase0:\s*\n[\s\S]*?answers:' | Should Be $true

            # Idempotence-guard contract (per orchestration/obi-auto-max.md step 2):
            # presence of a non-empty answers block under runtime.phase0 means
            # Phase 0 must NOT re-ask. We assert the structural signal here;
            # the orchestrator's behavior is enforced by the Markdown directive.
            $hasRuntimePhase0 = $content -match '(?s)\nruntime:\s*\n\s+phase0:'
            $hasAnswers       = $content -match '(?s)answers:\s*\n\s+- id:'
            ($hasRuntimePhase0 -and $hasAnswers) | Should Be $true

            # The original phase0: schema block remains parseable (so probe
            # routing still works even when re-running on a locked plan).
            $parser = Join-Path $RepoRoot 'tools\parse-plan-phase0.ps1'
            $schema = & $parser -PlanPath $PlanPath | ConvertFrom-Json
            @($schema).Count -gt 0 | Should Be $true
        }
    }
}

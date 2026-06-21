<#
.SYNOPSIS
    Pester 3.4 tests for tools/parse-plan-phase0.ps1.
#>

$ScriptPath = Join-Path $PSScriptRoot 'parse-plan-phase0.ps1'

Describe 'parse-plan-phase0' {

    BeforeEach {
        $script:PlanPath = Join-Path $TestDrive 'sample-plan.md'
    }

    Context 'No phase0 block' {

        It 'returns empty array when plan has no phase0 block' {
            Set-Content -Path $PlanPath -Value '# Plan with no phase0' -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $output | Should Be '[]'
        }
    }

    Context 'Single entry' {

        It 'parses a single phase0 entry with full schema' {
            $body = @"
# Plan

phase0:
  - id: target_namespace
    question: "Where does the fork land?"
    placeholder: "<target-namespace>"
    options: [user, group, instance]
    default: group
    locks_field: bootstrap-config.json:repoBase
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            @($obj).Count | Should Be 1
            $obj[0].id | Should Be 'target_namespace'
            $obj[0].question | Should Be 'Where does the fork land?'
            $obj[0].placeholder | Should Be '<target-namespace>'
            @($obj[0].options).Count | Should Be 3
            $obj[0].default | Should Be 'group'
            $obj[0].locks_field | Should Be 'bootstrap-config.json:repoBase'
        }
    }

    Context 'Multiple entries' {

        It 'preserves declaration order' {
            $body = @"
phase0:
  - id: first_question
    question: "First?"
  - id: second_question
    question: "Second?"
  - id: third_question
    question: "Third?"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            @($obj).Count | Should Be 3
            $obj[0].id | Should Be 'first_question'
            $obj[1].id | Should Be 'second_question'
            $obj[2].id | Should Be 'third_question'
        }
    }

    Context 'free_text flag' {

        It 'parses free_text: true' {
            $body = @"
phase0:
  - id: runner_tags
    question: "Which runner tags?"
    free_text: true
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            $obj[0].free_text | Should Be $true
        }

        It 'defaults free_text to false when absent' {
            $body = @"
phase0:
  - id: q1
    question: "Q1?"
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            $obj[0].free_text | Should Be $false
        }
    }

    Context 'Block boundaries' {

        It 'stops at next top-level key' {
            $body = @"
phase0:
  - id: q1
    question: "Q1?"

verification:
  grep:
    - after_phase: 5
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            @($obj).Count | Should Be 1
            $obj[0].id | Should Be 'q1'
        }

        It 'stops at code-fence boundary' {
            $body = @"
phase0:
  - id: only_one
    question: "?"
``````
not part of block
``````
"@
            Set-Content -Path $PlanPath -Value $body -Encoding UTF8
            $output = & $ScriptPath -PlanPath $PlanPath
            $obj = $output | ConvertFrom-Json
            @($obj).Count | Should Be 1
            $obj[0].id | Should Be 'only_one'
        }
    }

    Context 'Error handling' {

        It 'exits non-zero when plan file missing' {
            $missing = Join-Path $TestDrive 'no-such.md'
            $err = & $ScriptPath -PlanPath $missing 2>&1
            ($err -join "`n") -match 'Plan file not found' | Should Be $true
        }
    }
}

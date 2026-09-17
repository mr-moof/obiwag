<# Validate the efficiency policy without changing deployment or phase state. #>
function Get-EfficiencyPolicyError {
    param($Table)
    $results = @{ Valid = $true; Errors = @() }
        if ($table.efficiency.schema_version -ne 1 -or $table.efficiency.controlled_prompt_bytes -le 0) {
            $results.Valid = $false
            $results.Errors += 'efficiency policy missing or invalid controlled prompt budget'
        }
        foreach ($tier in @('routine', 'strong')) {
            foreach ($platform in @('claude', 'codex')) {
                $route = $table.efficiency.routing.tiers.$tier.$platform
                if (-not $route.model -or -not $route.effort) {
                    $results.Valid = $false
                    $results.Errors += "efficiency policy lacks $tier/$platform model and effort"
                }
            }
        }
        foreach ($section in @('narrative', 'references', 'excerpts', 'read_batches')) {
            if ($table.efficiency.handoff_budgets.sections.$section -le 0) {
                $results.Valid = $false
                $results.Errors += "efficiency handoff budget missing section $section"
            }
        }
        if ($table.efficiency.handoff_budgets.total_bytes -le 0) {
            $results.Valid = $false
            $results.Errors += 'efficiency handoff total budget is invalid'
        }
    return $results.Errors
}

function Read-ExpandedOrchestrationContract {
    param([string]$Path, [string]$RepoRoot)
    $text = [IO.File]::ReadAllText($Path)
    if ($text.Contains('docs/workflow/dispatch-failure.md')) {
        $failurePath = Join-Path $RepoRoot 'docs/workflow/dispatch-failure.md'
        if (-not (Test-Path -LiteralPath $failurePath -PathType Leaf)) {
            throw 'lazy dispatch failure contract is missing'
        }
        $text += "`n" + [IO.File]::ReadAllText($failurePath)
    }
    return $text
}

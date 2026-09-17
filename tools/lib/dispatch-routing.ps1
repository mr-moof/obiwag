<# Canonical effort routing and bounded dispatch-input preparation. #>

function Resolve-DispatchRouting {
    <#
    .SYNOPSIS Resolve the effective Claude model and effort for one dispatch.
    .DESCRIPTION Discovery and Author consume the canonical routine/strong tier mapping from
    phase-table.json. Their fail-closed default is strong. All other phases retain their persona
    frontmatter values, even if a caller supplied a routing tier.
    #>
    param(
        [Parameter(Mandatory)][int]$Phase,
        [string]$RoutingTier,
        [Parameter(Mandatory)][string]$PhaseTablePath,
        [string]$PersonaModel,
        [string]$PersonaEffort
    )
    if ($Phase -notin @(1, 2)) {
        return [ordered]@{
            tier = $null; model = $PersonaModel; effort = $PersonaEffort; source = 'persona'
        }
    }
    $tier = if ($RoutingTier) { $RoutingTier } else { 'strong' }
    if ($tier -notin @('routine', 'strong')) {
        throw "routing tier '$tier' is not routine or strong"
    }
    if (-not (Test-Path -LiteralPath $PhaseTablePath -PathType Leaf)) {
        throw "routing policy source not found: $PhaseTablePath"
    }
    try { $table = Get-Content -LiteralPath $PhaseTablePath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "routing policy source is invalid JSON: $($_.Exception.Message)" }
    $provider = $table.efficiency.routing.tiers.$tier.claude
    if (-not $provider -or -not [string]$provider.model -or -not [string]$provider.effort) {
        throw "routing policy has no complete Claude mapping for tier '$tier'"
    }
    return [ordered]@{
        tier = $tier
        model = [string]$provider.model
        effort = [string]$provider.effort
        source = 'phase-table-efficiency-routing'
    }
}

function Initialize-DispatchInput {
    param([string]$Persona, [string]$PersonaPath, [string]$PromptFile,
          [string]$ScriptDir, [int]$Phase, [string]$RoutingTier,
          [string]$PhaseTablePath, $PhasePolicy, [string]$CheckpointPath,
          [string]$BodyFile)
    if (-not $PersonaPath) {
        $candidates = @(
            (Join-Path $HOME ".claude\agents\$Persona.md"),
            (Join-Path (Split-Path -Parent $ScriptDir) "platforms\claude-code\agents\$Persona.md")
        )
        $PersonaPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $PersonaPath -or -not (Test-Path $PersonaPath)) {
        throw "persona '$Persona' not found (looked in ~/.claude/agents and platforms/claude-code/agents)"
    }
    if (-not (Test-Path $PromptFile)) { throw "prompt file not found: $PromptFile" }
    $parts = Get-PersonaParts -Raw (Get-Content $PersonaPath -Raw -Encoding UTF8)
    $routing = Resolve-DispatchRouting -Phase $Phase -RoutingTier $RoutingTier `
        -PhaseTablePath $PhaseTablePath -PersonaModel $parts.Model -PersonaEffort $parts.Effort
    $actualRoutingTier = [string]$routing.tier
    $actualModel = [string]$routing.model
    $actualEffort = [string]$routing.effort
    $budgetInstruction = Get-DispatchBudgetInstruction -Policy $phasePolicy `
        -CheckpointPath $CheckpointPath
    $workerBody = $parts.Body.TrimEnd() + "`n`n" + $budgetInstruction + "`n"
    Set-Content -LiteralPath $BodyFile -Value $workerBody -Encoding UTF8
    $controlledInputBytes = [Text.Encoding]::UTF8.GetByteCount($workerBody) +
        [Text.Encoding]::UTF8.GetByteCount((Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8))
    $inputPolicy = Get-Content -LiteralPath $PhaseTablePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($inputPolicy.efficiency.controlled_prompt_bytes -and
        $controlledInputBytes -gt [long]$inputPolicy.efficiency.controlled_prompt_bytes) {
        throw "controlled prompt exceeds efficiency budget; reduce excerpts or split scope before dispatch"
    }
    return @{ Parts=$parts; Tier=$actualRoutingTier; Model=$actualModel;
              Effort=$actualEffort; ControlledInputBytes=$controlledInputBytes }
}

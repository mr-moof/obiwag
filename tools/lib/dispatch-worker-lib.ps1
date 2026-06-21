<#
.SYNOPSIS
    Pure (side-effect-free) helpers for tools/dispatch-worker.ps1 (OPT-23 headless dispatch).

.DESCRIPTION
    Factored out so the persona-parsing, arg-building, and result-parsing logic is unit-testable
    without spawning a real `claude` worker. dispatch-worker.ps1 dot-sources this and owns the
    Start-Job / timeout / kill mechanics.
#>

function Get-PersonaParts {
    <#
    .SYNOPSIS Parse an agent persona .md into @{ Tools; Model; Body }.
    .DESCRIPTION Splits YAML frontmatter (between leading --- fences) from the body and extracts the
    `tools:` and `model:` keys. Returns the whole text as Body when there is no frontmatter.
    #>
    param([Parameter(Mandatory)][string]$Raw)

    $tools = $null; $model = $null; $body = $Raw
    if ($Raw -match '(?s)^\xEF?\xBB?\xBF?---\r?\n(.*?)\r?\n---\r?\n(.*)$') {
        $front = $Matches[1]; $body = $Matches[2]
        if ($front -match '(?m)^\s*tools:\s*(.+?)\s*$') { $tools = $Matches[1].Trim() }
        if ($front -match '(?m)^\s*model:\s*(.+?)\s*$') { $model = $Matches[1].Trim() }
    }
    return [ordered]@{ Tools = $tools; Model = $model; Body = $body }
}

function Build-ClaudeArgs {
    <#
    .SYNOPSIS Build the `claude` argument array for a headless worker.
    .DESCRIPTION Uses --tools (RESTRICTS the toolset, unlike --allowedTools which only auto-approves),
    --append-system-prompt for the persona body, --output-format json, and --max-turns. Adds --model
    only when the persona declares one.
    #>
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string]$Body,
        [string]$Tools,
        [string]$Model,
        [int]$MaxTurns = 80
    )
    $a = @('-p', $Prompt, '--output-format', 'json', '--max-turns', $MaxTurns,
           '--append-system-prompt', $Body)
    if ($Tools) { $a += @('--tools') + ($Tools -split '\s*,\s*' | Where-Object { $_ }) }
    if ($Model) { $a += @('--model', $Model) }
    return $a
}

function Read-WorkerResult {
    <#
    .SYNOPSIS Parse a claude --output-format json worker output file into @{ Result; IsError; SignalFound }.
    .DESCRIPTION On timeout or unparseable/missing output, returns IsError=$true. When ExpectSignal is
    given, SignalFound is true iff the worker's result text contains it.
    #>
    param([string]$OutFile, [bool]$TimedOut = $false, [string]$ExpectSignal)

    $result = ''; $isError = $true; $signalFound = $false
    if (-not $TimedOut -and $OutFile -and (Test-Path $OutFile)) {
        try {
            $j = Get-Content $OutFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $result = [string]$j.result
            $isError = [bool]$j.is_error
        } catch {
            $result = '<unparseable worker output>'; $isError = $true
        }
    }
    if ($ExpectSignal -and $result -and $result.Contains($ExpectSignal)) { $signalFound = $true }
    return [ordered]@{ Result = $result; IsError = $isError; SignalFound = $signalFound }
}

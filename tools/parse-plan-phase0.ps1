<#
.SYNOPSIS
    Parse the phase0: block from a plan markdown file and emit ordered JSON.

.DESCRIPTION
    Reads a plan file at $PlanPath and locates a fenced or inline `phase0:`
    YAML block. Returns a JSON array of question objects on stdout, in
    declared order, ready for the orchestrator to fire AskUserQuestion.

    Schema per entry (see policies/obi-auto-max-schema.md):
        id, question, placeholder?, options?, default?, locks_field?, free_text?

    Parser strategy: regex-based, matching the bump-version.ps1 pattern.
    Does NOT introduce a YAML library. Plan authors must use the simplified
    flat-block format documented in obi-auto-max-schema.md.

.PARAMETER PlanPath
    Absolute path to the plan markdown file.

.OUTPUT
    On stdout: a single JSON array string. Empty array `[]` if no phase0:
    block is found.

.EXAMPLE
    .\tools\parse-plan-phase0.ps1 -PlanPath "$HOME\.claude\plans\my-plan.md"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$PlanPath
)

$ErrorActionPreference = 'Continue'

if (-not (Test-Path -LiteralPath $PlanPath)) {
    Write-Error "Plan file not found: $PlanPath" -ErrorAction Continue
    exit 2
}

$content = Get-Content -LiteralPath $PlanPath -Raw

# Locate the phase0: block. Tolerant of leading code-fence (```yaml).
# The block runs until a blank line + a non-indented line at column 0
# that isn't a list item, OR another top-level ":" key, OR end of file.
$startIdx = -1
$lines = $content -split "`r?`n"
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^phase0:\s*$') {
        $startIdx = $i + 1
        break
    }
}

if ($startIdx -lt 0) {
    Write-Output '[]'
    exit 0
}

# Collect indented lines (the block body) until dedent or another top-level key
$bodyLines = New-Object System.Collections.ArrayList
for ($i = $startIdx; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    if ($line -match '^\s*$') {
        # Blank line: peek next; if next is non-indented non-list, end block
        $j = $i + 1
        while ($j -lt $lines.Count -and $lines[$j] -match '^\s*$') { $j++ }
        if ($j -ge $lines.Count) { break }
        if ($lines[$j] -match '^[A-Za-z]' -or $lines[$j] -match '^```') { break }
        $null = $bodyLines.Add($line)
        continue
    }
    if ($line -match '^[A-Za-z]') { break }
    if ($line -match '^```') { break }
    $null = $bodyLines.Add($line)
}

# Each entry begins with `  - id: <name>`. Group lines into entries.
$entries = New-Object System.Collections.ArrayList
$current = $null
foreach ($l in $bodyLines) {
    if ($l -match '^\s*-\s*id:\s*(\S+)\s*$') {
        if ($current) { $null = $entries.Add($current) }
        $current = @{
            id = $Matches[1]
            question = ''
            placeholder = ''
            options = @()
            default = ''
            locks_field = ''
            free_text = $false
        }
    } elseif ($null -ne $current) {
        if ($l -match '^\s*question:\s*"?(.+?)"?\s*$') {
            $current.question = $Matches[1].Trim('"')
        } elseif ($l -match '^\s*placeholder:\s*"?(.+?)"?\s*$') {
            $current.placeholder = $Matches[1].Trim('"')
        } elseif ($l -match '^\s*options:\s*\[(.+?)\]\s*$') {
            $optsRaw = $Matches[1]
            $current.options = @($optsRaw -split ',' | ForEach-Object { $_.Trim().Trim('"') } | Where-Object { $_ })
        } elseif ($l -match '^\s*default:\s*"?(.+?)"?\s*$') {
            $current.default = $Matches[1].Trim('"')
        } elseif ($l -match '^\s*locks_field:\s*"?(.+?)"?\s*$') {
            $current.locks_field = $Matches[1].Trim('"')
        } elseif ($l -match '^\s*free_text:\s*(true|false)\s*$') {
            $current.free_text = ($Matches[1] -eq 'true')
        }
    }
}
if ($current) { $null = $entries.Add($current) }

# Emit JSON. PS 5.1 ConvertTo-Json on a single-element array drops the
# array brackets and emits just the inner object. Detect and wrap.
if ($entries.Count -eq 0) {
    Write-Output '[]'
    exit 0
}

$stableArr = @()
foreach ($e in $entries) {
    $stableArr += [PSCustomObject]@{
        id          = $e.id
        question    = $e.question
        placeholder = $e.placeholder
        options     = @($e.options)
        default     = $e.default
        locks_field = $e.locks_field
        free_text   = [bool]$e.free_text
    }
}

$json = $stableArr | ConvertTo-Json -Depth 6 -Compress
if ($json -isnot [string]) { $json = [string]$json }
if (-not $json.StartsWith('[')) {
    $json = '[' + $json + ']'
}
Write-Output $json

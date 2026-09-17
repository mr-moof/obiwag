<#
.SYNOPSIS
    Load /obi-auto-max configuration from .obi/auto-max.yaml with baked-in defaults.

.DESCRIPTION
    Returns a flat hashtable keyed by dotted path. Schema:
        phase0.required:                bool   (default true)
        phase0.peer_review:              bool   (default true)
        grep_gates.fail_fast:            bool   (default true)
        probes.parallel:                 bool   (default false)
        pipeline.max_iterations:         int    (default 3)
        auto_memory.enabled:             bool   (default true)
        auto_memory.confidence_threshold: float (default 0.7)

    Per-repo overrides loaded from .obi/auto-max.yaml relative to -RepoRoot
    (or the inferred root if -RepoRoot is omitted). Regex parser, no YAML lib
    (per bump-version.ps1:55-60 pattern).

.PARAMETER RepoRoot
    Repo root for locating .obi/auto-max.yaml. Defaults to the script's
    parent's parent.

.OUTPUT
    Hashtable. Use `Get-AutoMaxConfig -Key 'pipeline.max_iterations' -Default 3`
    helper for safe lookup with default fallback.

.EXAMPLE
    $cfg = .\tools\load-auto-max-config.ps1
    $cfg['pipeline.max_iterations']

.EXAMPLE
    . .\tools\load-auto-max-config.ps1 -DotSource
    Get-AutoMaxConfig -Key 'auto_memory.enabled' -Default $true
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [switch]$DotSource
)

$ErrorActionPreference = 'Continue'

# Resolve RepoRoot inside the body — PS 5.1 may leave $PSScriptRoot empty
# during param-default evaluation under `powershell.exe -File`.
if (-not $RepoRoot) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

# Baked-in defaults
$script:AutoMaxDefaults = @{
    'phase0.required'                 = $true
    'phase0.peer_review'              = $true
    'grep_gates.fail_fast'            = $true
    'probes.parallel'                 = $false
    'pipeline.max_iterations'         = 3
    'auto_memory.enabled'             = $true
    'auto_memory.confidence_threshold' = 0.7
}

# Map dotted paths to (block, key) pairs for the regex parser
$script:AutoMaxKeyMap = @{
    'phase0.required'                  = @('phase0', 'required')
    'phase0.peer_review'               = @('phase0', 'peer_review')
    'grep_gates.fail_fast'             = @('grep_gates', 'fail_fast')
    'probes.parallel'                  = @('probes', 'parallel')
    'pipeline.max_iterations'          = @('pipeline', 'max_iterations')
    'auto_memory.enabled'              = @('auto_memory', 'enabled')
    'auto_memory.confidence_threshold' = @('auto_memory', 'confidence_threshold')
}

function Read-AutoMaxOverrides {
    param([Parameter(Mandatory)] [string]$ConfigPath)

    $overrides = @{}
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return $overrides
    }

    $content = Get-Content -LiteralPath $ConfigPath -Raw
    $lines = $content -split "`r?`n"

    $currentBlock = ''
    foreach ($line in $lines) {
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*):\s*$') {
            $currentBlock = $Matches[1]
            continue
        }
        if ($line -match '^\s+([A-Za-z_][A-Za-z0-9_]*):\s*(.+?)\s*$') {
            $key = $Matches[1]
            $rawValue = $Matches[2].Trim().Trim('"')

            # Convert types
            $value = $null
            if ($rawValue -match '^(true|false)$') {
                $value = ($rawValue -eq 'true')
            } elseif ($rawValue -match '^-?\d+$') {
                $value = [int]$rawValue
            } elseif ($rawValue -match '^-?\d+\.\d+$') {
                $value = [double]$rawValue
            } else {
                $value = $rawValue
            }

            $overrides["$currentBlock.$key"] = $value
        }
    }

    return $overrides
}

function Get-AutoMaxConfig {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string]$Key,
        $Default = $null
    )

    if ($script:AutoMaxLoaded.ContainsKey($Key)) {
        return $script:AutoMaxLoaded[$Key]
    }
    if ($null -ne $Default) {
        return $Default
    }
    if ($script:AutoMaxDefaults.ContainsKey($Key)) {
        return $script:AutoMaxDefaults[$Key]
    }
    return $null
}

# --- Main: build merged config ---
$ConfigPath = Join-Path $RepoRoot '.obi\auto-max.yaml'
$overrides = Read-AutoMaxOverrides -ConfigPath $ConfigPath

$merged = @{}
foreach ($k in $script:AutoMaxDefaults.Keys) {
    if ($overrides.ContainsKey($k)) {
        $merged[$k] = $overrides[$k]
    } else {
        $merged[$k] = $script:AutoMaxDefaults[$k]
    }
}
# Preserve unknown override keys for forward-compat surfaces
foreach ($k in $overrides.Keys) {
    if (-not $merged.ContainsKey($k)) {
        $merged[$k] = $overrides[$k]
    }
}

$script:AutoMaxLoaded = $merged

if ($DotSource) {
    # When dot-sourced, leave Get-AutoMaxConfig in caller scope and return
    return
}

$merged

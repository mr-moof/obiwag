<#
.SYNOPSIS
    Validate .obi/auto-max.yaml schema, types, and ranges.

.DESCRIPTION
    Dot-sourced by config-guardian.ps1 AFTER lib/common.ps1 and lib/validation.ps1.
    Assumes the caller has defined $ScriptDir and $RepoRoot.

    Provides:
    - Test-AutoMaxConfig  (AM-2..AM-5: loader call, type validation, range checks,
                           malformed-line check)
#>

function Test-AutoMaxConfig {
    <#
    .SYNOPSIS
        Validate .obi/auto-max.yaml: loader call, schema types, ranges, syntax.
    .PARAMETER CheckOnly
        Suppresses success output (CI-friendly). Errors still print.
    #>
    param([switch]$CheckOnly)

    $results = @{ Valid = $true; Errors = @() }

    $ConfigPath = Join-Path $RepoRoot '.obi\auto-max.yaml'

    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        if (-not $CheckOnly) {
            Write-Check "$ConfigPath does not exist; defaults will be used."
        }
        return $results
    }

    # Expected schema (key -> expected PowerShell type name)
    $schema = @{
        'phase0.required'                 = 'Boolean'
        'phase0.codex_review'             = 'Boolean'
        'grep_gates.fail_fast'            = 'Boolean'
        'probes.parallel'                 = 'Boolean'
        'pipeline.max_iterations'         = 'Int32'
        'auto_memory.enabled'             = 'Boolean'
        'auto_memory.confidence_threshold' = 'Double'
    }

    # Load via the loader
    $LoaderPath = Join-Path $ScriptDir 'load-auto-max-config.ps1'
    if (-not (Test-Path -LiteralPath $LoaderPath)) {
        $results.Valid = $false
        $results.Errors += "Loader not found: $LoaderPath"
        Write-Problem "auto-max loader not found: $LoaderPath"
        return $results
    }

    $cfg = & $LoaderPath -RepoRoot $RepoRoot

    foreach ($key in $schema.Keys) {
        $expected = $schema[$key]
        if (-not $cfg.ContainsKey($key)) {
            $results.Valid = $false
            $results.Errors += "Missing key: $key"
            continue
        }
        $actual = $cfg[$key].GetType().Name
        if ($actual -ne $expected) {
            $results.Valid = $false
            $results.Errors += "Wrong type for $key`: expected $expected, got $actual (value: $($cfg[$key]))"
        }
    }

    # Range checks
    if ($cfg.ContainsKey('pipeline.max_iterations')) {
        $iter = $cfg['pipeline.max_iterations']
        if ($iter -lt 1 -or $iter -gt 10) {
            $results.Valid = $false
            $results.Errors += "pipeline.max_iterations out of range [1..10]: $iter"
        }
    }
    if ($cfg.ContainsKey('auto_memory.confidence_threshold')) {
        $thr = $cfg['auto_memory.confidence_threshold']
        if ($thr -lt 0 -or $thr -gt 1) {
            $results.Valid = $false
            $results.Errors += "auto_memory.confidence_threshold out of range [0..1]: $thr"
        }
    }

    # Syntactic check: any lines that look like a key but didn't parse?
    $content = Get-Content -LiteralPath $ConfigPath -Raw
    $lines = $content -split "`r?`n"
    foreach ($line in $lines) {
        if ($line -match '^\s*#') { continue }       # comment
        if ($line -match '^\s*$') { continue }        # blank
        if ($line -match '^[A-Za-z_][A-Za-z0-9_]*:\s*$') { continue }  # block header
        if ($line -match '^\s+[A-Za-z_][A-Za-z0-9_]*:\s+\S') { continue } # key: value
        $results.Valid = $false
        $results.Errors += "Malformed line: $line"
    }

    if ($results.Errors.Count -gt 0) {
        Write-Problem "$ConfigPath has $($results.Errors.Count) validation error(s):"
        foreach ($e in $results.Errors) { Write-Problem "  - $e" }
    } elseif (-not $CheckOnly) {
        Write-Check "$ConfigPath is valid."
    }

    return $results
}

<#
.SYNOPSIS
    Run hard grep gates from a plan's verification.grep block (Gate 2), or the
    standalone version-drift gate (-VersionDrift).

.DESCRIPTION
    Default (plan) mode: reads the plan file, locates verification.grep entries
    whose `after_phase` matches -Phase, and scans the repo for forbidden patterns
    while respecting `allow_files` regex exemptions.

    Version-drift mode (-VersionDrift): no plan required. Reads the current
    version from tools/version.yaml, then checks the framework-version BANNER
    sites that tools/bump-version.ps1 is responsible for keeping in sync:
        - README.md                                          (**v<ver>**)
        - docs/hooks-architecture.md                         (**Version:** <ver>)
    The gate fails if a target's framework-version banner is MISSING or carries
    a version that DISAGREES with version.yaml — i.e. a bump that didn't fully
    propagate (the "stale version left behind after a bump" landmine the retired
    "version in 4 places" invariant tried to police by hand).

    Scope is intentionally limited to these bump-target files. The repo uses the
    `> **Version:** X` shape pervasively for *document-level* versions (e.g.
    `**Version:** 1.0` in policy/doc headers, future-proposal versions like
    `0.70.0`), so a repo-wide banner scan would be all false positives. The real
    drift surface is exactly the set of files bump-version.ps1 writes; this gate
    asserts those landed. version.yaml itself is the source of truth, CHANGELOG.md
    legitimately lists every past version, docs/archive/ holds frozen snapshots,
    and the deploy.ps1 "pre-0.69.38 behavior" comment is historical prose — none
    are in scope. Keep this target list in sync with the banner $targets in
    tools/bump-version.ps1.

    Exit codes:
        0 - clean (no forbidden matches / all bump-target banners match)
        1 - one or more forbidden patterns matched, or a target banner is stale
            / missing
        2 - plan file missing or schema invalid (plan mode), or version.yaml
            unreadable (version-drift mode)

    On exit 1 (plan mode), writes JSON to .obi/runtime/grep-gate-<phase>-<UTC>.json
    with: {phase, matches: [{file, line, text}], fail_message, proposed_allow_files}

.PARAMETER PlanPath
    Path to the plan markdown file containing the verification.grep block.
    Required in plan mode; ignored with -VersionDrift.

.PARAMETER Phase
    Integer phase number (1-10). Only entries whose after_phase matches
    are evaluated. Required in plan mode; ignored with -VersionDrift.

.PARAMETER RepoRoot
    Root directory to scan. Defaults to the git top-level of the CURRENT WORKING
    DIRECTORY, falling back to the repo root inferred from $PSScriptRoot when the
    cwd is not a git work tree.

    The cwd-first default matters: this script is invoked from the deployed
    $env:OBI_HOME copy (the configured shared runtime), so
    inferring the root from $PSScriptRoot resolved to obi-tools and silently
    scanned the WRONG repo — every gate reported "clean" for any other project.

.PARAMETER VersionDrift
    Run the standalone version-drift gate instead of the plan-driven grep gate.
    Does not require -PlanPath / -Phase.

.PARAMETER BarePass
    Run the standalone bare-pass gate (OPT-05 #178): fail if any
    `except Exception:` is immediately followed by `pass` (no logging) anywhere
    under hooks/, outside the logging-infra files that must never recurse.
    Does not require -PlanPath / -Phase.

.NOTES
    Implementation: PowerShell Select-String only, for portability (rg may be
    missing on test boxes). Allow-files post-filtered via -notmatch.
    Scripts cannot call Claude's Grep tool; this uses real process/file APIs.
#>
[CmdletBinding(DefaultParameterSetName = 'Plan')]
param(
    [Parameter(Mandatory, ParameterSetName = 'Plan')] [string]$PlanPath,
    [Parameter(Mandatory, ParameterSetName = 'Plan')] [int]$Phase,
    [Parameter(Mandatory, ParameterSetName = 'VersionDrift')] [switch]$VersionDrift,
    [Parameter(Mandatory, ParameterSetName = 'BarePass')] [switch]$BarePass,
    [string]$RepoRoot = ''
)

$ErrorActionPreference = 'Continue'

if (-not $RepoRoot) {
    # Prefer the git top-level of the cwd: callers run the DEPLOYED copy under
    # $env:OBI_HOME, so $PSScriptRoot points at obi-tools, not the repo under
    # test. Falling back to $PSScriptRoot keeps the old behavior for a caller
    # that runs this from inside its own repo with no git available.
    $gitTop = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitTop) {
        $RepoRoot = ($gitTop | Select-Object -First 1).Trim() -replace '/', '\'
    } else {
        $RepoRoot = Split-Path -Parent $PSScriptRoot
    }
}

# ---------------------------------------------------------------------------
# Version-drift gate (-VersionDrift): standalone, plan-free. See .DESCRIPTION.
# ---------------------------------------------------------------------------
if ($VersionDrift) {
    if (-not (Test-Path -LiteralPath $RepoRoot)) {
        Write-Error "Repo root does not exist: $RepoRoot" -ErrorAction Continue
        exit 2
    }

    $versionYaml = Join-Path $RepoRoot 'tools\version.yaml'
    if (-not (Test-Path -LiteralPath $versionYaml)) {
        Write-Error "version.yaml not found: $versionYaml" -ErrorAction Continue
        exit 2
    }
    $yaml = Get-Content -LiteralPath $versionYaml -Raw
    if ($yaml -notmatch 'version:\s*"([^"]+)"') {
        Write-Error "Could not read current version from $versionYaml" -ErrorAction Continue
        exit 2
    }
    $current = $Matches[1]

    # The framework-version banner sites bump-version.ps1 is responsible for.
    # Each entry: File (repo-relative, backslash), Banner (regex capturing the
    # version in group 1), Label. Keep in lockstep with the banner $targets in
    # tools/bump-version.ps1 — if a banner target is added/removed there, mirror
    # it here. The Banner regex captures *any* semver in the banner slot so a
    # stale value is detected (not just the current one); equality vs. $current
    # decides pass/fail. A semver here is \d+\.\d+(\.\d+)? to also catch a banner
    # accidentally left two-segment.
    $bannerTargets = @(
        @{ File = 'README.md';                                          Banner = '\*\*v(\d+\.\d+(?:\.\d+)?)\*\*';             Label = 'README.md (**v<ver>**)' }
        @{ File = 'docs\hooks-architecture.md';                         Banner = '\*\*Version:\*\*\s*(\d+\.\d+(?:\.\d+)?)';   Label = 'hooks-architecture.md (**Version:** <ver>)' }
    )

    $problems = @()
    foreach ($t in $bannerTargets) {
        $path = Join-Path $RepoRoot $t.File
        if (-not (Test-Path -LiteralPath $path)) {
            $problems += "MISSING FILE: $($t.Label) - expected at $($t.File)"
            continue
        }
        $content = Get-Content -LiteralPath $path -Raw
        $m = [regex]::Match($content, $t.Banner)
        if (-not $m.Success) {
            $problems += "NO BANNER: $($t.Label) - no framework-version banner found in $($t.File)"
            continue
        }
        $found = $m.Groups[1].Value
        if ($found -ne $current) {
            $problems += "STALE: $($t.Label) - found '$found' in $($t.File), expected '$current'"
        }
    }

    if ($problems.Count -eq 0) {
        Write-Output "VERSION DRIFT GATE: clean (all $($bannerTargets.Count) bump-target banners match version.yaml = $current)"
        exit 0
    }

    Write-Output "VERSION DRIFT GATE FAIL: $($problems.Count) bump-target banner issue(s) vs version.yaml ($current):"
    foreach ($p in $problems) {
        Write-Output "  $p"
    }
    exit 1
}

# ---------------------------------------------------------------------------
# Bare-pass gate (-BarePass): standalone, plan-free. OPT-05 (#178).
# Fails if any `except Exception:` is immediately followed by `pass` (no log,
# no comment) anywhere under hooks/ -- the silent-dead-subsystem pattern that
# core.hook_logger.log_swallowed() replaces. Infra files that must never
# recurse through the logger (hook_logger.py, hook_error_handler.py) are
# exempt, as is tests/ (fixtures legitimately swallow).
# ---------------------------------------------------------------------------
if ($BarePass) {
    if (-not (Test-Path -LiteralPath $RepoRoot)) {
        Write-Error "Repo root does not exist: $RepoRoot" -ErrorAction Continue
        exit 2
    }
    $hooksDir = Join-Path $RepoRoot 'hooks'
    if (-not (Test-Path -LiteralPath $hooksDir)) {
        Write-Error "hooks/ not found under $RepoRoot" -ErrorAction Continue
        exit 2
    }

    # Logging infra is exempt: log_swallowed() itself appends + rotates, so a
    # bare-except there is the floor that prevents logging from ever recursing.
    $exemptLeaf = @('hook_logger.py', 'hook_error_handler.py')

    $pyFiles = Get-ChildItem -LiteralPath $hooksDir -Recurse -File -Filter '*.py' -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -notmatch '[\\/]tests[\\/]' -and
            $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
            $exemptLeaf -notcontains $_.Name
        }

    $violations = @()
    foreach ($f in $pyFiles) {
        $fl = Get-Content -LiteralPath $f.FullName
        for ($k = 0; $k -lt ($fl.Count - 1); $k++) {
            if ($fl[$k] -match '^\s*except Exception:\s*$' -and $fl[$k + 1] -match '^\s*pass\s*$') {
                $rel = ($f.FullName.Substring($RepoRoot.Length).TrimStart('\', '/')) -replace '\\', '/'
                $violations += ('{0}:{1}' -f $rel, ($k + 1))
            }
        }
    }

    if ($violations.Count -eq 0) {
        Write-Output "BARE-PASS GATE: clean (no bare 'except Exception: pass' in hooks/ outside logging infra)"
        exit 0
    }

    Write-Output ("BARE-PASS GATE FAIL: {0} bare 'except Exception: pass' handler(s) in hooks/ - route through core.hook_logger.log_swallowed():" -f $violations.Count)
    foreach ($v in $violations) { Write-Output "  $v" }
    exit 1
}

if (-not (Test-Path -LiteralPath $PlanPath)) {
    Write-Error "Plan file not found: $PlanPath" -ErrorAction Continue
    exit 2
}

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    Write-Error "Repo root does not exist: $RepoRoot" -ErrorAction Continue
    exit 2
}

# --- Parse verification.grep entries for $Phase ---
$content = Get-Content -LiteralPath $PlanPath -Raw
$lines = $content -split "`r?`n"

# Find verification: block start
$verifIdx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^verification:\s*$') { $verifIdx = $i + 1; break }
}
if ($verifIdx -lt 0) {
    Write-Output "GREP GATE [phase $Phase]: no verification: block in plan; nothing to check"
    exit 0
}

# Find grep: under verification:
$gepIdx = -1
for ($i = $verifIdx; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^[A-Za-z]') { break }     # left the verification: subtree
    if ($lines[$i] -match '^\s+grep:\s*$') { $gepIdx = $i + 1; break }
}
if ($gepIdx -lt 0) {
    Write-Output "GREP GATE [phase $Phase]: no verification.grep in plan"
    exit 0
}

# Walk grep entries. Each starts with `    - after_phase: N`.
$entries = New-Object System.Collections.ArrayList
$current = $null
for ($i = $gepIdx; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    if ($line -match '^[A-Za-z]') { break }   # left subtree
    if ($line -match '^\s+- after_phase:\s*(\d+)\s*$') {
        if ($null -ne $current) { $null = $entries.Add($current) }
        $current = @{
            after_phase        = [int]$Matches[1]
            forbidden_patterns = @()
            allow_files        = @()
            fail_message       = "Forbidden pattern matched after phase $($Matches[1])"
        }
    } elseif ($null -ne $current) {
        if ($line -match '^\s+forbidden_patterns:\s*$') {
            # collect following list items
            for ($j = $i + 1; $j -lt $lines.Count; $j++) {
                if ($lines[$j] -match '^\s+- "(.+)"\s*$') {
                    # YAML double-quoted strings escape backslashes; collapse \\ -> \
                    $current.forbidden_patterns += $Matches[1].Replace('\\', '\')
                } else { break }
            }
        } elseif ($line -match '^\s+allow_files:\s*$') {
            for ($j = $i + 1; $j -lt $lines.Count; $j++) {
                if ($lines[$j] -match '^\s+- "(.+)"\s*$') {
                    # Same YAML unescape as forbidden_patterns: collapse \\ -> \
                    $current.allow_files += $Matches[1].Replace('\\', '\')
                } else { break }
            }
        } elseif ($line -match '^\s+fail_message:\s*"(.+)"\s*$') {
            $current.fail_message = $Matches[1]
        }
    }
}
if ($null -ne $current) { $null = $entries.Add($current) }

# Filter to entries matching $Phase
$applicable = @($entries | Where-Object { $_.after_phase -eq $Phase })
if ($applicable.Count -eq 0) {
    Write-Output "GREP GATE [phase $Phase]: no entries with after_phase=$Phase; clean"
    exit 0
}

# --- Scan ---
$allMatches = @()
$failMessage = ''
foreach ($entry in $applicable) {
    $failMessage = $entry.fail_message
    foreach ($pattern in $entry.forbidden_patterns) {
        # Use Select-String for portability; rg may be missing on test boxes.
        # Select-String has no -Recurse; enumerate files first via Get-ChildItem.
        # Skip .git/ and other VCS dirs to avoid scanning binaries.
        # CRITICAL: skip .obi/ — this script writes failure artifacts to
        # .obi/runtime/grep-gate-*.json that contain the matched text and
        # the forbidden pattern itself. Including .obi/ would self-poison
        # subsequent runs.
        $files = Get-ChildItem -LiteralPath $RepoRoot -Recurse -File -Force `
            -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName -notmatch '[\\/]\.git[\\/]' -and
                $_.FullName -notmatch '[\\/]\.venv[\\/]' -and
                $_.FullName -notmatch '[\\/]\.obi[\\/]' -and
                $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
                $_.FullName -notmatch '[\\/]node_modules[\\/]'
            }

        if (-not $files) { continue }

        $hits = $files | Select-String -Pattern $pattern -AllMatches -ErrorAction SilentlyContinue
        if (-not $hits) { continue }
        foreach ($hit in $hits) {
            $relPath = $hit.Path.Substring($RepoRoot.Length).TrimStart('\','/')
            $relPath = $relPath -replace '\\', '/'

            # Apply allow_files exemptions
            $exempt = $false
            foreach ($allowRegex in $entry.allow_files) {
                if ($relPath -match $allowRegex) { $exempt = $true; break }
            }
            if ($exempt) { continue }

            $allMatches += [PSCustomObject]@{
                file = $relPath
                line = $hit.LineNumber
                text = $hit.Line.Trim()
                pattern = $pattern
            }
        }
    }
}

if ($allMatches.Count -eq 0) {
    Write-Output "GREP GATE [phase $Phase]: clean ($($applicable.Count) entries scanned)"
    exit 0
}

# --- Failure: write JSON + emit fail message ---
$runtimeDir = Join-Path $RepoRoot '.obi\runtime'
if (-not (Test-Path $runtimeDir)) {
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
}
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$jsonPath = Join-Path $runtimeDir "grep-gate-$Phase-$stamp.json"

# Suggest paths to consider for allow_files (top 5 unique files)
$proposedAllowFiles = @($allMatches | Select-Object -ExpandProperty file -Unique | Select-Object -First 5)

$payload = [PSCustomObject]@{
    phase                = $Phase
    matches              = @($allMatches)
    fail_message         = $failMessage
    proposed_allow_files = $proposedAllowFiles
    generated_at         = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Path $jsonPath -Encoding UTF8

Write-Output "GREP GATE FAIL [phase $Phase]: $failMessage ($($allMatches.Count) match(es))"
Write-Output "Details: $jsonPath"
exit 1

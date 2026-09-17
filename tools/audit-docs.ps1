<#
.SYNOPSIS
    Audits contract-doc integrity: dangling path references and agent/phase-table drift.

.DESCRIPTION
    Two sweeps that each caught real defects during the 0.69.72 audit, promoted from
    throwaway scripts into a repeatable gate so the next drift is caught mechanically
    instead of by a human reading 343 files.

    SWEEP A - dangling path references. Extracts every backticked repo-relative path
    from tracked and untracked working-tree markdown and checks it resolves. Known-benign classes are excluded by
    design, NOT by guessing:

      * docs/policies/*  - policies/ deploys to docs/policies/ (deploy.ps1 "Source
        layout"), so these resolve in the deployed tree the runtime agent reads and
        legitimately dangle in source.
      * commands/*, agents/* - deploy TARGETS. Prose about the deployed layout is correct.
      * .obi/**, output/** and graphify-out/** - runtime-generated, absent until a run creates them.
      * paths containing $ or starting with ~ - env/template placeholders.
      * phases/0N-name/... - the documented placeholder form in README.
      * config/gotchas.md - belongs to another project (cited as an example).

    Suppressing these matters: of 42 non-resolving paths found in 0.69.72, only 8 were
    real. Reporting all 42 would have trained the reader to ignore the gate, and
    "fixing" them would have broken working references.

    SWEEP B - agent vs phase-table drift. For each phases/phase-table.json entry:
    the agent file exists; its frontmatter `model` matches display.model; the
    primary_signal string appears verbatim in the agent body. Caught 5 agents pinning a
    model the table reported as unset, drift introduced one version earlier.

.PARAMETER RepoRoot
    Repo to audit. Defaults to the git top-level of the cwd, falling back to this
    script's parent. (Never $PSScriptRoot alone: this script is invoked from the
    deployed $env:OBI_HOME copy, where that would resolve to obi-tools.)

.PARAMETER Quiet
    Print only the summary lines and any findings.

.OUTPUTS
    Exit 0 clean, 1 findings, 2 script/input error.

.EXAMPLE
    .\tools\audit-docs.ps1

.EXAMPLE
    powershell -NoProfile -File $env:OBI_HOME\tools\audit-docs.ps1 -RepoRoot C:\src\obiwag-agents
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [switch]$Quiet
)

$ErrorActionPreference = 'Continue'

if (-not $RepoRoot) {
    $gitTop = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitTop) {
        $RepoRoot = ($gitTop | Select-Object -First 1).Trim() -replace '/', '\'
    } else {
        $RepoRoot = Split-Path -Parent $PSScriptRoot
    }
}
if (-not (Test-Path -LiteralPath $RepoRoot)) {
    Write-Error "Repo root does not exist: $RepoRoot"
    exit 2
}

Push-Location $RepoRoot
try {
    $tracked = @(& git ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) { Write-Error "git ls-files failed in $RepoRoot"; exit 2 }
} finally { Pop-Location }

$findings = @()

# ---------------------------------------------------------------------------
# Sweep A - dangling path references
# ---------------------------------------------------------------------------
$mdFiles = @($tracked | Where-Object {
    $_ -like '*.md' -and
    $_ -notlike 'graphify-out/*' -and
    $_ -notlike '.obi/*' -and
    $_ -ne 'CHANGELOG.md'
})

# Benign-by-design prefixes/patterns. See .DESCRIPTION for why each is here.
$benign = @(
    '^docs/policies/',        # policies/ -> docs/policies/ at deploy
    '^commands/',             # deploy target
    '^agents/',               # deploy target
    '^\.obi/',                # runtime-generated
    '^\.codex/hooks\.json$',  # generated project hook configuration
    '^output/',               # runtime-generated
    '^graphify-out/',         # runtime-generated (graphify update .), gitignored
    '^config/gotchas\.md$',   # another project's file, cited as an example
    '^phases/0N-'             # documented placeholder form
)

$pathRx = [regex]'`([A-Za-z0-9_./~$-]+\.(?:md|ps1|py|json|yaml|yml|jsonl|svg|psm1|psd1))`'
$missing = @{}
$totalRefs = 0

foreach ($rel in $mdFiles) {
    $full = Join-Path $RepoRoot $rel
    if (-not (Test-Path -LiteralPath $full)) { continue }
    $lineNo = 0
    foreach ($line in [System.IO.File]::ReadAllLines($full)) {
        $lineNo++
        foreach ($m in $pathRx.Matches($line)) {
            $ref = $m.Groups[1].Value
            $totalRefs++
            if ($ref -match '[$]' -or $ref.StartsWith('~') -or $ref.StartsWith('/')) { continue }
            if ($ref -notmatch '/') { continue }          # bare filename, not a path claim
            $isBenign = $false
            foreach ($b in $benign) { if ($ref -match $b) { $isBenign = $true; break } }
            if ($isBenign) { continue }

            $asIs     = Join-Path $RepoRoot $ref
            $relToDoc = Join-Path (Join-Path $RepoRoot (Split-Path -Parent $rel)) $ref
            if ((Test-Path -LiteralPath $asIs) -or (Test-Path -LiteralPath $relToDoc)) { continue }

            if (-not $missing.ContainsKey($ref)) { $missing[$ref] = @() }
            $missing[$ref] += "${rel}:${lineNo}"
        }
    }
}

if (-not $Quiet) { Write-Host "Sweep A: $totalRefs path refs in $($mdFiles.Count) working-tree markdown files" }
foreach ($ref in ($missing.Keys | Sort-Object)) {
    $sites = $missing[$ref] -join '; '
    $findings += "dangling ref: $ref  <- $sites"
}

# ---------------------------------------------------------------------------
# Sweep B - agent vs phase-table drift
# ---------------------------------------------------------------------------
$tablePath = Join-Path $RepoRoot 'phases\phase-table.json'
if (-not (Test-Path -LiteralPath $tablePath)) {
    $findings += 'phase-table.json not found; cannot run Sweep B'
} else {
    try {
        $table = Get-Content -LiteralPath $tablePath -Raw | ConvertFrom-Json
    } catch {
        $findings += "phase-table.json is not valid JSON: $($_.Exception.Message)"
        $table = $null
    }

    if ($table) {
        if (-not $Quiet) { Write-Host "Sweep B: $($table.phases.Count) phase-table entries" }
        foreach ($p in $table.phases) {
            $agentRel  = "platforms\claude-code\agents\$($p.agent).md"
            $agentPath = Join-Path $RepoRoot $agentRel
            if (-not (Test-Path -LiteralPath $agentPath)) {
                $findings += "phase $($p.n) ($($p.name)): agent file missing: $agentRel"
                continue
            }
            $body = [System.IO.File]::ReadAllText($agentPath)

            # model: frontmatter vs display.model (compare base, ignoring an [1m] suffix)
            $fmModel = ''
            $mm = [regex]::Match($body, '(?m)^model:\s*(.+?)\s*$')
            if ($mm.Success) { $fmModel = $mm.Groups[1].Value.Split('[')[0] }
            $tblModel = if ($p.display.model) { [string]$p.display.model } else { '' }
            if ($fmModel -ne $tblModel) {
                $findings += "phase $($p.n) ($($p.agent)): model drift - table display.model='$tblModel' but frontmatter='$fmModel'"
            }

            # primary signal must appear verbatim in the agent body
            $sig = $p.primary_signal.value
            if ($sig -and -not $body.Contains($sig)) {
                $findings += "phase $($p.n) ($($p.agent)): primary_signal '$sig' not present in agent body"
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Sweep D - peer finding schema enum vs the catch-log taxonomy
# ---------------------------------------------------------------------------
# The whole value of the peer-result schema is that an accepted finding maps 1:1 onto a
# log-codex-catch.ps1 call with no re-interpretation. That depends on two hand-maintained
# lists agreeing: the schema's `category` enum and the taxonomy table in
# policies/peer-review.md. Nothing linked them, so they would drift silently and the
# mapping would quietly stop being 1:1.
$schemaPath = Join-Path $RepoRoot 'tools\schemas\peer-review-result.schema.json'
$policyPath = Join-Path $RepoRoot 'policies\peer-review.md'
if ((Test-Path -LiteralPath $schemaPath) -and (Test-Path -LiteralPath $policyPath)) {
    try {
        $sch = Get-Content -LiteralPath $schemaPath -Raw | ConvertFrom-Json
        $enum = @($sch.properties.findings.items.properties.category.enum)
    } catch {
        $enum = @()
        $findings += "peer-review schema is not valid JSON: $schemaPath"
    }
    if ($enum.Count) {
        # Taxonomy rows look like: | `plan-gap` | Plan omits a required step or dependency |
        $policyText = [System.IO.File]::ReadAllText($policyPath)
        $taxonomySection = [regex]::Match(
            $policyText,
            '(?ms)^### Accepted-finding taxonomy\s*$.*?(?=^### |^## |\z)'
        )
        if (-not $taxonomySection.Success) {
            $findings += 'peer-review.md is missing the Accepted-finding taxonomy section'
        } else {
            $taxonomy = @([regex]::Matches($taxonomySection.Value, '(?m)^\|\s*`([a-z-]+)`\s*\|') |
                ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
            $missingInPolicy = @($enum   | Where-Object { $_ -notin $taxonomy })
            $missingInSchema = @($taxonomy | Where-Object { $_ -notin $enum })
            if ($missingInPolicy.Count) {
                $findings += "peer category enum has values absent from the peer-review.md taxonomy table: $($missingInPolicy -join ', ')"
            }
            if ($missingInSchema.Count) {
                $findings += "peer-review.md taxonomy has categories absent from the schema enum: $($missingInSchema -join ', ')"
            }
        }
        if (-not $Quiet) { Write-Host "Sweep D: $($enum.Count) peer categories cross-checked against the catch-log taxonomy" }
    }
}

# ---------------------------------------------------------------------------
# Sweep C - UTF-8 BOM ahead of YAML frontmatter
# ---------------------------------------------------------------------------
# A BOM before the opening `---` stops the frontmatter from parsing, so the command
# or agent silently loses its `description`/`model`/`tools`. Observed for real: a
# PowerShell 5.1 `Set-Content -Encoding UTF8` rewrite of orchestration/obi-auto.md
# added a BOM and the deployed command's description became the literal "---".
# Only files that actually carry frontmatter matter; a BOM elsewhere is harmless.
$bomChecked = 0
foreach ($rel in @($tracked | Where-Object { $_ -like '*.md' })) {
    $full = Join-Path $RepoRoot $rel
    if (-not (Test-Path -LiteralPath $full)) { continue }
    $bytes = [System.IO.File]::ReadAllBytes($full)
    if ($bytes.Length -lt 6) { continue }
    $hasBom = ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if (-not $hasBom) { continue }
    # frontmatter iff the first characters after the BOM are '---'
    if ($bytes[3] -eq 0x2D -and $bytes[4] -eq 0x2D -and $bytes[5] -eq 0x2D) {
        $findings += "BOM before YAML frontmatter (breaks description/model parsing): $rel"
    }
    $bomChecked++
}
if (-not $Quiet) { Write-Host "Sweep C: $bomChecked BOM-bearing markdown file(s) checked for frontmatter" }

# ---------------------------------------------------------------------------
if ($findings.Count -eq 0) {
    Write-Host "AUDIT DOCS: clean ($totalRefs refs checked, no dangling refs, no agent/table drift, no BOM-before-frontmatter)"
    exit 0
}
Write-Host "AUDIT DOCS FAIL: $($findings.Count) finding(s)"
foreach ($f in $findings) { Write-Host "  - $f" }
exit 1

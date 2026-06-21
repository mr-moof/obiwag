<#
.SYNOPSIS
    Run codex.cmd against a plan file outside any git repo, via ephemeral git init.

.DESCRIPTION
    Plan files at ~/.claude/plans/<task>.md are outside any git repo, so a bare
    `codex exec` aborts with "not a git repository". This helper:

    1. Creates $env:TEMP\codex-plan-<sha12>\
    2. Copies the plan file in (preserving filename)
    3. git init + git commit -m snap (ephemeral; isolated from user's git config)
    4. Invokes `codex --cd <tmp> @CodexArgs`, passing all args after `--`
       verbatim
    5. Cleans up the tempdir on exit (try/finally), even when codex fails

    All output (stdout/stderr) streams to the user's terminal. There is no
    .obi/plan-codex-*.md artifact.

    Implementation note: this script uses `codex --cd <tmp>` per `codex exec
    --help` showing `-C, --cd <DIR>`. As a future simplification, codex 0.128+
    also supports `--skip-git-repo-check`, which would let us skip the ephemeral
    init entirely. Keeping ephemeral init for now per /obi-auto-max plan.

.PARAMETER PlanPath
    Absolute path to the plan markdown file.

.PARAMETER CodexArgs
    Arguments forwarded verbatim to codex.cmd. Pass as an array, e.g.
    `-CodexArgs @('--profile','review','exec','--json','-')`. Defaults to
    Recipe-2 standard `--profile review exec -`.

    Note: PowerShell scripts cannot reliably consume `--` as a stop-parsing
    separator when invoked via `powershell.exe -File`, so the array form is
    the supported invocation. Wrap in your own helper if you want a `--`
    pass-through CLI.

.EXAMPLE
    .\tools\codex-plan-prep.ps1 -PlanPath "$HOME\.claude\plans\my-plan.md"

.EXAMPLE
    .\tools\codex-plan-prep.ps1 -PlanPath "$HOME\.claude\plans\my-plan.md" `
        -CodexArgs @('--profile','review','exec','--json','-')

.NOTES
    Requires PowerShell 5.1+, git, and codex.cmd on PATH.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$PlanPath,
    [string[]]$CodexArgs = @('--profile','review','exec','-')
)

# Note: keep EAP=Continue for the bulk of execution because PS 5.1 wraps
# native-command stderr (git, codex) as ErrorRecord under EAP=Stop and
# crashes even on successful invocations. We exit explicitly on failure.
$ErrorActionPreference = 'Continue'

function Write-PrepError {
    param([string]$Message)
    # Use Write-Error so PowerShell's 2>&1 stream redirection can capture
    # the message in tests. EAP is Continue at script level so this won't throw.
    Write-Error "codex-plan-prep: $Message" -ErrorAction Continue
}

function Remove-TempDirSafe {
    param([Parameter(Mandatory)] [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    # git init creates .git\objects\* with read-only attributes that defeat
    # naive Remove-Item -Force on Windows. Strip read-only first.
    try {
        Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
            ForEach-Object {
                if ($_.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
                    try { $_.Attributes = $_.Attributes -band -bnot [System.IO.FileAttributes]::ReadOnly } catch {}
                }
            }
    } catch {}
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $PlanPath)) {
    Write-PrepError "Plan file not found: $PlanPath"
    exit 2
}

$codex = Get-Command -Name codex -ErrorAction SilentlyContinue
if (-not $codex) {
    Write-PrepError 'codex not found on PATH. See policies/codex-usage.md for setup.'
    exit 3
}

$git = Get-Command -Name git -ErrorAction SilentlyContinue
if (-not $git) {
    Write-PrepError 'git not found on PATH. Required for ephemeral repo init.'
    exit 4
}

# Stable but uniqueness-bearing tempdir name from a hash of the plan path
$sha = [System.Security.Cryptography.SHA256]::Create()
$bytes = [System.Text.Encoding]::UTF8.GetBytes($PlanPath + (Get-Date).Ticks)
$hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').Substring(0, 12).ToLower()
$tmp = Join-Path $env:TEMP "codex-plan-$hash"

try {
    Remove-TempDirSafe -Path $tmp
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null

    $planLeaf = Split-Path $PlanPath -Leaf
    Copy-Item -LiteralPath $PlanPath -Destination (Join-Path $tmp $planLeaf) -Force

    # Initialize ephemeral repo. Suppress git init's verbose output; let codex own the terminal.
    & git -C $tmp init --quiet 2>$null | Out-Null
    & git -C $tmp -c user.email='plan@local' -c user.name='codex-plan-prep' add -- $planLeaf 2>$null | Out-Null
    & git -C $tmp -c user.email='plan@local' -c user.name='codex-plan-prep' commit --quiet -m 'snap' 2>$null | Out-Null

    # Forward args verbatim. -C is injected first so codex picks the ephemeral cwd.
    $finalArgs = @('-C', $tmp) + $CodexArgs

    # If CodexArgs ends with `-` (stdin sentinel), feed the plan body plus a
    # review instruction to codex's stdin. Without this the trailing `-` causes
    # codex to wait on EOF immediately, running on an empty prompt.
    $reviewsStdin = ($CodexArgs.Count -gt 0 -and $CodexArgs[-1] -eq '-')
    if ($reviewsStdin) {
        $reviewPrompt = @"
You are reviewing the plan file below for an upcoming /obi-auto-max run.

Look for:
- Underspecified or contradictory directives
- Missing prereq probes that should be locked-in via Phase 0 questions
- Hallucinated APIs (vendor or technology)
- Test gaps, race conditions, error-path gaps
- File-boundary breaches (>400 line target, >600 hard limit)
- Plan-vs-reality drift in line numbers or referenced files

Be specific: cite file:line. Output structured findings.

---PLAN---

"@
        $planBody = Get-Content -LiteralPath (Join-Path $tmp $planLeaf) -Raw
        $combined = $reviewPrompt + $planBody
        $combined | & codex @finalArgs
    } else {
        & codex @finalArgs
    }
    $codexExit = $LASTEXITCODE
    if ($null -eq $codexExit) { $codexExit = 0 }
} finally {
    Remove-TempDirSafe -Path $tmp
}

exit $codexExit

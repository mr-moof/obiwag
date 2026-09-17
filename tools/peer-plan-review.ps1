<#
.SYNOPSIS
    Submit an external plan file through the canonical peer-review harness.

.DESCRIPTION
    Creates a short-lived semantic request that attaches exactly the selected
    plan, then delegates to peer-review.ps1. Provider CLI flags are intentionally
    unavailable here. Output is the peer harness's compact JSON artifact pointer.

.PARAMETER Platform
    Known primary runtime forwarded semantically to peer-review.ps1. Use `codex` from Codex and
    `claude` from Claude Code when Provider is `auto`.

.PARAMETER Authorization
    Forward `approved` only after the user explicitly approves a scope that the
    canonical harness classified as approval-required. Pass its exact
    ApprovalScopeSha256 with the approval.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PlanPath,

    [string]$RepoRoot,

    [ValidateSet('auto', 'codex', 'claude')]
    [string]$Provider = 'auto',

    [ValidateSet('codex', 'claude', 'claude-code')]
    [string]$Platform,

    [ValidateSet('auto', 'approved')]
    [string]$Authorization = 'auto',

    [string]$ApprovalScopeSha256,

    [ValidateSet('preflight', 'run', 'start')]
    [string]$Operation = 'start',

    [int]$TimeoutSec = 0
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PlanPath -PathType Leaf)) {
    [Console]::Error.WriteLine("peer-plan-review: plan file not found: $PlanPath")
    exit 2
}
if ($Operation -ne 'preflight') {
    if ($TimeoutSec -eq 0) {
        $TimeoutSec = if ($Operation -eq 'start') { 1800 } else { 240 }
    }
    $maximum = if ($Operation -eq 'start') { 3600 } else { 240 }
    if ($TimeoutSec -lt 1 -or $TimeoutSec -gt $maximum) {
        [Console]::Error.WriteLine("peer-plan-review: TimeoutSec must be between 1 and $maximum for '$Operation'.")
        exit 2
    }
}
if (-not $RepoRoot) {
    $oldEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $gitTop = & git rev-parse --show-toplevel 2>$null
    } finally {
        $ErrorActionPreference = $oldEap
    }
    if ($LASTEXITCODE -ne 0 -or -not $gitTop) {
        [Console]::Error.WriteLine('peer-plan-review: RepoRoot is required outside a git repository.')
        exit 2
    }
    $RepoRoot = ([string]($gitTop | Select-Object -First 1)).Trim()
}

$resolvedPlan = (Resolve-Path -LiteralPath $PlanPath).Path
$requestFile = Join-Path ([System.IO.Path]::GetTempPath()) ("obi-peer-plan-{0}.json" -f [guid]::NewGuid().ToString('N'))
$request = [ordered]@{
    schema_version = 1
    objective = 'Adversarially review the attached implementation plan against this repository. Find contradictions, invented APIs, missing prerequisites, unsafe sequencing, and verification gaps.'
    acceptance_criteria = @(
        'Every finding cites an exact attachment or repository snapshot line.',
        'Unsupported concerns are omitted rather than guessed.',
        'The review converges within the harness deadline.'
    )
    focus = @('plan feasibility', 'repository alignment', 'tests and rollback', 'scope discipline')
    include_paths = @()
    evidence = @()
    attachments = @(@{ label = 'plan'; path = $resolvedPlan })
}

try {
    $request | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $requestFile -Encoding UTF8
    $peerReview = Join-Path $PSScriptRoot 'peer-review.ps1'
    if (-not (Test-Path -LiteralPath $peerReview)) {
        [Console]::Error.WriteLine("peer-plan-review: canonical harness not found: $peerReview")
        exit 2
    }
    $peerArguments = @{
        Provider = $Provider
        RepoRoot = $RepoRoot
        RequestFile = $requestFile
        Authorization = $Authorization
    }
    if ($Operation -ne 'preflight') { $peerArguments.TimeoutSec = $TimeoutSec }
    if ($ApprovalScopeSha256) { $peerArguments.ApprovalScopeSha256 = $ApprovalScopeSha256 }
    if ($Platform) { $peerArguments.Platform = $Platform }
    & $peerReview $Operation @peerArguments
    $peerExit = $LASTEXITCODE
    if ($null -eq $peerExit) { $peerExit = 0 }
    exit $peerExit
} finally {
    Remove-Item -LiteralPath $requestFile -Force -ErrorAction SilentlyContinue
}

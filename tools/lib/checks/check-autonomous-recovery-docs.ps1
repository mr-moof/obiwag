function Get-AutonomousRecoveryDocError {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$ObiAutoNormalized
    )

    $errors = @()
    function Get-MissingMarker {
        param([string]$Text, [string[]]$Markers)
        return @($Markers | Where-Object {
            $Text.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -lt 0
        })
    }

    $takeoverMarkers = @(
        'two verified checkpoint failures', 'same assigned session', 'kill_verified',
        'progress_count', 'never dispatch a third worker',
        'Standing autonomous authority begins the primary takeover automatically',
        'primary-synthesis-recovery', 'settled evidence only', 'no new source research',
        'primary-author-takeover', 'honest attribution', 'reconciles source and test'
    )
    if (@(Get-MissingMarker -Text $ObiAutoNormalized -Markers $takeoverMarkers).Count -gt 0) {
        $errors += 'obi-auto must constrain authorized primary takeover after two verified checkpoint failures, including settled evidence only'
    }

    $recoveryMarkers = @(
        'status-updates-<run_id>.jsonl', 'task-base-$runId.txt', 'autonomous_recovery.py',
        'native_completion_stalled', 'peer_unavailable', 'prior_run_safe_resumable',
        'prior_run_terminal_or_corrupt', 'phase_blocker_fixable', 'check_failure_fixable',
        'reversible_default_selected', 'user_abort', 'hard_stop',
        'Never use worker/orchestrator failure itself as a stop reason'
    )
    if (@(Get-MissingMarker -Text $ObiAutoNormalized -Markers $recoveryMarkers).Count -gt 0) {
        $errors += 'obi-auto must preserve the append-only autonomous-recovery event and continuation contract'
    }

    $permissionStopPatterns = @(
        'Primary takeover requires\s+explicit the user authorization',
        'AskUserQuestion:\s*resume-existing-run',
        'NEEDS USER INPUT.{0,80}explicit the user authorization',
        'inline_fallback_eligible:\s*false.{0,500}NEEDS USER INPUT'
    )
    if (@($permissionStopPatterns | Where-Object { $ObiAutoNormalized -match $_ }).Count -gt 0) {
        $errors += 'obi-auto contains a permission-only stop for an in-scope recovery action'
    }

    $inlineRecipesPath = Join-Path $RepoRoot 'orchestration\inline-fallback-recipes.md'
    if (-not (Test-Path -LiteralPath $inlineRecipesPath -PathType Leaf)) {
        $errors += 'orchestration/inline-fallback-recipes.md not found for autonomous recovery validation'
    } else {
        $inlineText = [regex]::Replace(
            [IO.File]::ReadAllText($inlineRecipesPath), '\s+', ' ')
        $inlineMarkers = @(
            'task-base-<run_id>.txt', 'phase_blocker_fixable', 'hard_stop',
            'Never emit `NEEDS USER INPUT` merely to authorize continued review'
        )
        if (@(Get-MissingMarker -Text $inlineText -Markers $inlineMarkers).Count -gt 0 -or
            $inlineText -match 'halt.{0,100}NEEDS USER INPUT') {
            $errors += 'inline fallback recipes must recover in-scope blockers without a permission-only halt'
        }
    }

    $obiAutoMaxPath = Join-Path $RepoRoot 'orchestration\obi-auto-max.md'
    if (-not (Test-Path -LiteralPath $obiAutoMaxPath -PathType Leaf)) {
        $errors += 'orchestration/obi-auto-max.md not found for autonomous recovery validation'
    } else {
        $maxText = [regex]::Replace([IO.File]::ReadAllText($obiAutoMaxPath), '\s+', ' ')
        $maxMarkers = @(
            'orchestration/obi-auto.md', 'rigor: max', 'standing autonomous-recovery authority',
            'append-only decision ledger',
            'do not reintroduce permission-only continuation questions'
        )
        if (@(Get-MissingMarker -Text $maxText -Markers $maxMarkers).Count -gt 0 -or
            $maxText -match 'NEEDS USER INPUT|AskUserQuestion') {
            $errors += 'obi-auto-max must inherit the regular autonomous recovery contract without permission-only stops'
        }
    }

    $threeStrikePath = Join-Path $RepoRoot 'policies\three-strike-rule.md'
    if (-not (Test-Path -LiteralPath $threeStrikePath -PathType Leaf)) {
        $errors += 'policies/three-strike-rule.md not found for autonomous recovery validation'
    } else {
        $threeStrikeText = [regex]::Replace(
            [IO.File]::ReadAllText($threeStrikePath), '\s+', ' ')
        $threeStrikeMarkers = @(
            'STOP THAT APPROACH', '`phase_blocker_fixable` or `check_failure_fixable`',
            'Retry exhaustion alone is not a terminal boundary',
            'Autonomous mode: recovery decision appended'
        )
        if (@(Get-MissingMarker -Text $threeStrikeText -Markers $threeStrikeMarkers).Count -gt 0 -or
            $threeStrikeText -match 'Waiting for the user direction') {
            $errors += 'three-strike policy must stop the failed approach without stopping recoverable autonomous work'
        }
    }

    return $errors
}

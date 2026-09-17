$script:ToolsDir = Join-Path (Split-Path -Parent $PSScriptRoot) '..'
$script:ToolsDir = (Resolve-Path $script:ToolsDir).Path
$script:Renderer = Join-Path $script:ToolsDir 'render-phase-table.ps1'

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# Minimal 2-phase contract (schema 7, with lanes + display objects). Lane phase refs
# (0/1/3) must all be valid: 0 = max prereq, 1 + 3 exist in the phases array.
$MinimalJson = @'
{
  "schema_version": 7,
  "efficiency": {
    "schema_version": 1, "controlled_prompt_bytes": 98304,
    "routing": {"tiers": {
      "routine": {"claude": {"model": "sonnet", "effort": "medium"}, "codex": {"model": "gpt-5.6-terra", "effort": "medium"}},
      "strong": {"claude": {"model": "fable[1m]", "effort": "xhigh"}, "codex": {"model": "gpt-5.6-sol", "effort": "xhigh"}}
    }},
    "handoff_budgets": {"total_bytes": 65536, "sections": {"narrative": 8192, "references": 16384, "excerpts": 32768, "read_batches": 8192}}
  },
  "delegation_policy_defaults": {
    "claude": {"nominal_work_sec": 270, "idle_sec": 90, "productive_extension_sec": 0, "finalization_sec": 0, "absolute_sec": 270, "recent_progress_sec": 15, "cleanup_margin_sec": 30},
    "codex": {"synthesis_due_sec": 270, "grace_sec": 60, "recent_progress_sec": 60, "wait_window_max_sec": 60, "resume_window_count": 2, "resume_window_sec": 60, "terminal_states": ["completed", "native_completion_stalled"], "initial_classifications": ["completed", "productive_budget_exhausted", "interrupted_after_grace"], "synthesis_instruction": "No more tools. Return the phase artifact now from current evidence; list missing sources instead of researching further."}
  },
  "lanes": {
    "trivial":  {"phases": [3], "signal": "TRIVIAL LANE: {N}", "description": "t"},
    "express":  {"phases": [1, 3], "signal": "EXPRESS LANE: {N}", "description": "e"},
    "standard": {"phases": [1, 3], "signal": null, "description": "s"},
    "max":      {"phases": [0, 1, 3], "signal": null, "description": "m", "gates": [3]}
  },
  "phases": [
    {"n": 1, "name": "Discovery", "agent": "obi-discovery", "delegated": true, "default_strategy": "dispatch", "recipe": null, "inline_fallback_eligible": false, "primary_signal": {"value": "DISCOVERY COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": false, "delegation_policy": {"claude": {"nominal_work_sec": 390, "idle_sec": 90, "productive_extension_sec": 90, "finalization_sec": 60, "absolute_sec": 540, "recent_progress_sec": 15, "cleanup_margin_sec": 30}, "codex": {"synthesis_due_sec": 270, "grace_sec": 60, "recent_progress_sec": 60, "wait_window_max_sec": 60, "resume_window_count": 2, "resume_window_sec": 60, "terminal_states": ["completed", "native_completion_stalled"], "initial_classifications": ["completed", "productive_budget_exhausted", "interrupted_after_grace"], "synthesis_instruction": "No more tools. Return the phase artifact now from current evidence; list missing sources instead of researching further.", "research": {"targeted_local_call_limit": 12, "official_source_batch_call_limit": 4, "source_open_stop_sec": 180}}}, "display": {"command": "/discovery", "model": "opus", "strategy_note": null, "recipe_note": null, "fallback_note": "synthesis", "signal_display": "`DISCOVERY COMPLETE`"}},
    {"n": 3, "name": "Simplify", "agent": "obi-simplify", "delegated": true, "default_strategy": "inline", "recipe": "S", "inline_fallback_eligible": true, "primary_signal": {"value": "SIMPLIFY COMPLETE", "match_mode": "literal"}, "special_signals": [], "verdict_in_body": false, "display": {"command": "/simplify", "model": "opus", "strategy_note": "#164", "recipe_note": null, "fallback_note": null, "signal_display": "`SIMPLIFY COMPLETE`"}}
  ]
}
'@

function New-FixtureRepo {
    param([string]$Slug)
    $repo = Join-Path $env:TEMP "hcdd-test-$Slug-$(Get-Random)"
    New-Item -ItemType Directory -Force -Path "$repo\phases" | Out-Null
    return $repo
}

function Set-SyncedFixture {
    param([string]$Repo, [string]$JsonBody = $MinimalJson)
    Set-Content -Path "$Repo\phases\phase-table.json" -Value $JsonBody -Encoding UTF8
    $readme = @"
# Phase Table

<!-- obi:phase-table-start -->
<!-- obi:phase-table-end -->

## Delegation policy
"@
    Set-Content -Path "$Repo\phases\README.md" -Value $readme -Encoding UTF8
    & $script:Renderer -RepoRoot $Repo | Out-Null

    # Contract docs carrying the catch-log wiring so DD-3 passes by construction.
    # phase=plan lives in policies\rigor-max-gates.md as of 0.69.73 (the rigor=max block
    # moved out of obi-auto.md so a plain /obi-auto run doesn't load it).
    New-Item -ItemType Directory -Force -Path "$Repo\policies" | Out-Null
    New-Item -ItemType Directory -Force -Path "$Repo\phases\05-integrate" | Out-Null
    New-Item -ItemType Directory -Force -Path "$Repo\phases\09-release" | Out-Null
    New-Item -ItemType Directory -Force -Path "$Repo\orchestration" | Out-Null
    New-Item -ItemType Directory -Force -Path "$Repo\docs" | Out-Null
    New-Item -ItemType Directory -Force -Path "$Repo\platforms\claude-code\agents" | Out-Null
    Set-Content -Path "$Repo\policies\rigor-max-gates.md" `
        -Value '7. Run codex-on-plan; & "$env:OBI_HOME\tools\log-codex-catch.ps1" -Phase plan -Category plan-gap' -Encoding UTF8
    Set-Content -Path "$Repo\policies\three-strike-rule.md" `
        -Value 'STOP THAT APPROACH. Append `phase_blocker_fixable` or `check_failure_fixable`. Retry exhaustion alone is not a terminal boundary. Autonomous mode: recovery decision appended.' -Encoding UTF8
    Set-Content -Path "$Repo\phases\05-integrate\command.md" `
        -Value '& $env:OBI_HOME\tools\log-codex-catch.ps1 -Phase review -Category other' -Encoding UTF8
    $emDash = [char]0x2014
    $dropSelfCorrection = "If you reach the step-1 read after a sentinel without having emitted the DETECTED line, you skipped the contract $emDash emit it now."
    $dropContract = @(
        "DROP DETECTED: <tool/phase> $emDash verifying with <check>, then <retry once | proceed | halt>"
        'DROP RESOLVED: <already-applied | retried-ok | escalating>'
        $dropSelfCorrection
        'Stable contract: grep-stable on `^DROP (DETECTED|RESOLVED):`.'
        ''
        '```text'
        "DROP DETECTED: Agent/Review $emDash verifying with .obi/state/dispatch-state.json, then retry once"
        'VERIFY: no completed Review artifact; retry budget available'
        'RETRY: same Agent call once; Review returns REVIEW COMPLETE: PASS'
        'DROP RESOLVED: retried-ok'
        '```'
    ) -join "`r`n"
    $obiAutoContract = 'Read phase-table.json and branch on inline_fallback_eligible: false. At fresh-run startup and before successful Learning archival, invoke archive-orphan-state.ps1 -Mode Archive; accept only complete or no_eligible_orphans, preserve active and live state, and require its move-only manifest. Record task-base-$runId.txt. After two verified checkpoint failures in the same assigned session with kill_verified true and progress_count greater than zero, never dispatch a third worker. Standing autonomous authority begins the primary takeover automatically. Every recovery invokes autonomous_recovery.py and appends status-updates-<run_id>.jsonl. Supported events are native_completion_stalled, peer_unavailable, prior_run_safe_resumable, prior_run_terminal_or_corrupt, phase_blocker_fixable, check_failure_fixable, reversible_default_selected, user_abort, and hard_stop. Never use worker/orchestrator failure itself as a stop reason. Discovery primary-synthesis-recovery uses settled evidence only and performs no new source research. Author primary-author-takeover keeps honest attribution and reconciles source and tests. On successful Learning require every lifecycle tracker closed; archive-run-state.ps1 returns blocked_open_trackers for an exact Status: OPEN line and must never rewrite the tracker. Then invoke $OBI_HOME/tools/archive-run-state.ps1 with the active RunId, require status is complete, and leave the remaining state in place on failure.'
    Set-Content -Path "$Repo\orchestration\obi-auto.md" -Value ($obiAutoContract + "`r`n" + $dropContract) -Encoding UTF8
    Set-Content -Path "$Repo\orchestration\obi-auto-max.md" `
        -Value 'Read orchestration/obi-auto.md with rigor: max, including standing autonomous-recovery authority and append-only decision ledger; do not reintroduce permission-only continuation questions.' -Encoding UTF8
    Set-Content -Path "$Repo\docs\operation-timeouts.md" -Value $dropContract -Encoding UTF8
    $recipes = @'
Use task-base-<run_id>.txt. Append phase_blocker_fixable or hard_stop. Never emit `NEEDS USER INPUT` merely to authorize continued review.

Invoke-ScriptAnalyzer once per selected path with -Severity Error -ErrorAction Stop.
Run python -m ruff check <path> once per selected path for sorted unique existing changed Python paths,
and fail on a missing module, invocation error, or nonzero exit. Never lint the repository root.
For filtered Pester, executed count = PassedCount + FailedCount + SkippedCount; TotalCount includes
NotRunCount, so assert the expected executed count. README parsing uses bash -n -c and resolves CLAUDE_CODE_GIT_BASH_PATH, and rejects System32\bash.exe.

## Recipe R — Re-review (inline)

Run the smallest focused
selection. A Pester-only selection uses timeout: 600000; a non-Pester selection uses
timeout: 300000. Use full-suite
sharding only when isolation is unsafe.

## Recipe M — README Review (inline)

Parse examples.

## Recipe G — Release Gate (inline)

Phase 9 is the sole unconditional full-suite owner. Audit TARGET= output until the shards are
complete and disjoint. Run tools/run-tests.ps1 -PowerShellOnly -ShardCount 3 -ShardIndex 1
-ListTargets, then each shard without listing and tools/run-tests.ps1 -PythonOnly. Do not also run
the monolithic command. Each Pester shard uses timeout: 600000; the Python component uses timeout: 300000.
On timeout terminate and verify the exact timed-out process tree.
Replace the TODO: describe this release. stub in CHANGELOG.md. Release history does not belong in
`tools/version.yaml`.
Version scan uses Get-Item -LiteralPath $sourcePath -ErrorAction Stop for leaf files and
Get-ChildItem -LiteralPath $sourcePath -Recurse -File -ErrorAction Stop for directories.
Any enumeration error or remaining hit fails the recipe.
'@
    Set-Content -Path "$Repo\orchestration\inline-fallback-recipes.md" -Value $recipes -Encoding UTF8
    Set-Content -Path "$Repo\phases\09-release\command.md" -Encoding UTF8 -Value @'
Read orchestration/inline-fallback-recipes.md Recipe G and policies/zero-hallucination.md,
policies/hard-stop-conditions.md, and policies/vendor-rules.md. Stage by explicit file list; never
use blanket staging. Claude uses docs/policies/zero-hallucination.md,
docs/policies/hard-stop-conditions.md, and docs/policies/vendor-rules.md after deployment.
'@
    Set-Content -Path "$Repo\platforms\claude-code\agents\obi-release-gate.md" -Encoding UTF8 -Value @'
Read Recipe G and run its complete/disjoint suite plus the full local `tools/deploy.ps1`. Replace
the CHANGELOG.md stub; release prose never goes in tools/version.yaml. Stage by explicit file list.
Preserve scratch state. Project policy must name required files; do not invent generic .nuspec or
.github/workflows/ requirements.
'@
}

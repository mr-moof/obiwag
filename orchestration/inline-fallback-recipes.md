---
description: Inline fallback recipes (S/R/M/G) for dispatch-failure recovery and codified inline paths. Read by /obi-auto (both rigor levels) when an Agent call returns the dispatch sentinel.
---

# Inline Fallback Recipes

When a subagent dispatch fails (the `Agent` tool returns `[Tool result missing due to internal
error]` or an empty body, see Phase-Output Validation in `obi-auto.md`), the orchestrator falls
back to executing the corresponding recipe inline. Recipes also codify the inline path for phases
that don't dispatch but benefit from a reproducible procedure (Recipe G — Release Gate).

All recipes produce **two** outputs:
1. The bare completion signal (`SIMPLIFY COMPLETE`, `RE-REVIEW COMPLETE`, `README REVIEW COMPLETE`,
   `RELEASE GATE PASSED` / `RELEASE GATE FAILED: <reason>`).
2. A report artifact under `.obi/reviews/` or `.obi/reports/` whose first line is `Verdict: PASS` or
   `Verdict: NEEDS FIXES` (for Re-review and README Review only — Release Gate uses signal verdict;
   Simplify reports are advisory).

Recipes declare expected git state up front so the orchestrator can recover without guessing task
ownership. At run startup, `obi-auto` records the starting commit in
`.obi/state/task-base-<run_id>.txt`. Validate that value as a commit before using it. A missing
phase artifact or unexpected but task-owned Git state is `phase_blocker_fixable`: append the
decision, reconstruct the phase or diff from the task base, and continue. If task ownership cannot
be proven without including unrelated user changes, append `hard_stop` with that exact
data-integrity evidence. Never emit `NEEDS USER INPUT` merely to authorize continued review,
simplification, or verification, and never stage an unknown path.

## Shared probe — test-command detection

Used by Recipes R and G. Probe in order, take first hit:

1. `Test-Path tools/run-tests.ps1` → `powershell -NoProfile -File tools/run-tests.ps1`.
2. `Test-Path tools, *.Tests.ps1` → the repository's documented Pester 5 invocation (never a bare
   `Invoke-Pester` when multiple major versions are installed).
3. `Test-Path pyproject.toml` with `[tool.pytest*]` content OR `Test-Path pytest.ini` → `pytest`.
4. `Test-Path package.json` with a `test` script in scripts block → `npm test`.
5. `Test-Path go.mod` → `go test ./...`.
6. No discoverable command → emit blocking gap (recipe-specific handling; never silent pass).

For focused Author/Review/Re-review validation, map every changed behavior to the repository's
adjacent or named test file/case convention, then pass the smallest focused selection of explicit
paths/cases to the detected runner (for example, `tools/run-tests.ps1 -Path
a.tests.ps1,b.tests.ps1`). Use full-suite sharding only when isolation is unsafe or unavailable;
Recipe G is the sole unconditional full-suite owner. Record the selection and counts; zero
executed tests is a failure. For a filtered Pester run, `TotalCount` includes discovered
`NotRunCount`; compute executed count as `PassedCount + FailedCount + SkippedCount` (equivalently
`TotalCount - NotRunCount`) and assert the expected executed count explicitly.

PowerShell lint is also fail-closed: call `Invoke-ScriptAnalyzer` once per selected path with
`-Severity Error -ErrorAction Stop`, catch invocation errors, combine the returned error findings,
and fail when their count is nonzero. Lower-severity findings are advisory unless repository policy
promotes them. `-Path` is scalar; never pass an object array or infer success from process exit 0
after a binding error.

Python lint is task-scoped and fail-closed: derive the sorted, unique existing changed Python paths
from the selected diff, exclude generated/runtime state such as `.obi/` and `graphify-out/`, and run
`python -m ruff check <path>` once per selected path. Fail on a missing Ruff module, invocation
error, or nonzero exit. Never lint the repository root merely because one Python file changed;
pre-existing debt and unrelated untracked scratch are outside the task gate.

## Recipe RV — Review (inline)

Goal: emit `REVIEW COMPLETE: PASS` or `REVIEW COMPLETE: FAIL [N] issues` without dispatching
obi-reviewer. Produce report artifact `.obi/reviews/<run_id>-review.md`.

Expected git state: the Author's committed, staged, or tracked work is visible relative to the
validated run task base. Task-owned untracked paths must be named in the reconciled Author report;
an unowned path is the data-integrity hard stop above.

0. **Run the supervised peer pass** per `policies/peer-review.md`. Create a semantic branch-diff
   request; use foreground `run` only for a narrow, low-complexity pass and foreground `start` for
   a broad or semantically complex pass even when its file list is short. Retain
   every accepted RunId and use only bounded `status`/`wait`/`result`/`cancel` operations until
   consumed or explicitly cancelled. Consume only status, validated result, and summary artifacts.
   Never use shell background mode, tail raw files, retry, or pass raw provider flags. Reproduce
   accepted findings during steps 3-7; otherwise record `[Peer: unavailable]` and continue once.
1. **Detect diff source**: read and validate `.obi/state/task-base-<run_id>.txt`, then use
   `git diff <task-base>` and `git diff --name-only <task-base>` so committed, staged, and tracked
   unstaged task changes remain one cumulative review unit. Add only reconciled task-owned
   untracked paths from the Author report to the peer/review file list. If the task base is missing
   or is not a commit, append `hard_stop`; do not guess a recent commit.
2. **Read** `.obi/discovery-report.md` if present, for spec context and reference-module pointers
   that constrain what "correct" looks like for this change.
3. **Anti-hallucination sweep** — for every API call, function, vendor SDK method, environment
   variable, or file path referenced in the diff:
   - grep the wider codebase for evidence the symbol exists (`Get-ChildItem -Recurse | Select-String`).
   - If a symbol is referenced but not defined anywhere and is not a stdlib/well-known builtin,
     classify as a **Critical** finding ("invented API: `<symbol>`").
   - Trust nothing the AUTHOR claims about external libraries that you cannot confirm against
     vendored source, lockfiles, or imported modules.
4. **Spec compliance** — cross-check every change against `.obi/discovery-report.md`'s "Pattern
   to follow" and "Reference module" sections. Diffs that introduce new patterns when the
   discovery report named an existing one are **Major**.
5. **Test coverage** — for each new public function / new branch in existing code:
   - Confirm a corresponding test exists in the diff (or in a referenced fixture).
   - If no test landed AND the function has non-trivial logic (more than passthrough): classify
     as **Major** finding ("untested: `<symbol>`").
   - Pure-data scripts (one-shot backfills, migrations) may legitimately ship without unit tests;
     downgrade to **Minor** with note "one-shot script — verified via dry-run output".
6. **Security & secrets** — grep the diff for: hardcoded credentials, `password=`, `apikey=`,
   bearer tokens, private IPs in non-config files. Each hit is **Critical**.
7. **Run focused validation independently** — map every changed behavior to explicit test
   files/cases using the focused-selection rule above. Run the full command only when isolation is
   unsafe or unavailable. A command error or zero-test selection is a **Major** finding. Record the
   exact command and counts; do not reuse Author's result.
8. **Classify all findings** into Critical / Major / Minor / Nit. Count each tier separately.
9. **Write report** `.obi/reviews/<run_id>-review.md` with:
   - Header: target branch + commit sha or staged-diff marker.
   - Per-finding entry: `[tier] <category>: <detail>` with file:line refs.
   - Final line: `PASS` if zero Critical AND zero Major. `FAIL` otherwise.
10. **Emit**:
   - `REVIEW COMPLETE: PASS` if zero Critical AND zero Major findings.
   - `REVIEW COMPLETE: FAIL <N> issues` where `<N>` = Critical + Major count.
   The Integrate phase reads the report artifact for the detailed triage table.

Notes:
- Recipe RV and the dispatched reviewer use the same peer harness. Foreground `run` has a
  240-second cap; durable `start` has a 3600-second cap plus heartbeat/activity recovery. Neither
  path retries a terminal run.

## Recipe S — Simplify (inline)

Goal: emit `SIMPLIFY COMPLETE` (or `SIMPLIFY SKIPPED`) without dispatching obi-simplify. Produce
advisory report `.obi/reports/<run_id>-simplify.md`.

Expected git state: task-owned changes are visible relative to the validated run task base, with
any task-owned untracked path reconciled in the Author report. Unknown paths trigger the shared
data-integrity hard stop, not a continuation prompt.

1. **Detect diff source** — read and validate `.obi/state/task-base-<run_id>.txt`, then use the
   cumulative `git diff <task-base>` plus the reconciled task-owned untracked paths. This avoids
   selecting the wrong prior commit when Author leaves staged or tracked unstaged work.
2. **Scan diff** for: unused vars, dead branches, redundant comments restating code, debug print
   statements, trailing whitespace, quote-style drift (PowerShell: prefer single).
3. **Edit in place** for each finding. After each edit run the project's lint (detect by file types
   in diff): use the shared fail-closed per-path rules above for PowerShell and Python, or
   `golangci-lint run` for Go.
4. **Write report** `.obi/reports/<run_id>-simplify.md` summarizing scan findings + actions taken
   (or "no cleanup warranted"). Write this even on SKIPPED — downstream phases may inspect.
5. **Commit policy**:
   - If all task changes were already committed before step 1 AND cleanup landed: amend the Author
     commit (`git commit --amend --no-edit`). If the parent commit was already pushed (`git rev-parse '@{u}'` succeeds AND
     `git log '@{u}..HEAD'` is empty), DO NOT amend — create a separate `style: simplify post-author
     cleanup` commit. NOTE: `@{u}` MUST be single-quoted in PowerShell; the bare token is parsed as
     a hashtable literal (`@{...}`) and fails.
   - If any task change was staged or tracked-unstaged before Simplify, leave cleanup in the same
     worktree for Integrate/Release Gate to commit downstream. Stage only reconciled task-owned
     files, and do not commit in this case.
6. **Emit** `SIMPLIFY COMPLETE` if lint passes AND any cleanup landed; `SIMPLIFY SKIPPED` if no
   cleanup warranted.

## Recipe R — Re-review (inline)

Goal: emit `RE-REVIEW COMPLETE` (with `Verdict: PASS` or `Verdict: NEEDS FIXES` in the artifact
body) without dispatching obi-rereviewer. Produce report artifact
`.obi/reviews/<run_id>-rereview.md`.

Expected git state: Integrate has reconciled all accepted review feedback. HEAD may contain an
integration commit or the changes may remain task-owned in the worktree. If
`.obi/integration-report.md` is missing, append `phase_blocker_fixable`, reconstruct Integrate from
the review report and verified task diff, then re-enter Re-review. If reconstruction cannot prove
ownership or accepted-finding disposition, append the shared data-integrity `hard_stop`.

1. **Determine task base**: read `.obi/state/task-base-<run_id>.txt` and validate it with
   `git cat-file -e <task-base>^{commit}`. Store it as `$TaskBase`. A missing or invalid value is a
   data-integrity `hard_stop`; never guess `origin/main`, a recent commit, or `HEAD~10`.
2. **Read** `.obi/reviews/` for the latest review report (sort by filename, take last).
3. **Read** `.obi/integration-report.md` for the integrator's triage.
4. **Verify must-fix items** — for each: grep the changed files (`git diff --name-only
   $TaskBase..HEAD`) for evidence the fix landed. Confirm test coverage if review asked for it.
5. **Detect test command** (shared probe). If none discoverable: write artifact with
   `Verdict: NEEDS FIXES` and item "no discoverable test command — blocked on test infrastructure";
   emit `RE-REVIEW COMPLETE` and let orchestrator loop. Do NOT silently pass.
6. **Run the smallest focused selection** that covers every integrated must-fix item. Give a
   recognized Pester-only selection the **10-minute Bash timeout** (`timeout: 600000`); keep every
   non-Pester selection at the **5-minute Bash timeout** (`timeout: 300000`). Use full-suite sharding
   only when isolation is unsafe or unavailable; for obiwag-agents use the complete and disjoint
   Recipe G shard procedure, never the no-argument runner in one foreground call. A
   repository without safe focus, bounded shards, or an appropriate supervisor cannot pass: write
   `Verdict: NEEDS FIXES` and identify the missing bounded test path. On timeout, terminate and
   verify the exact timed-out process tree, write `Verdict: NEEDS FIXES`, and emit
   `RE-REVIEW COMPLETE`; do not retry blindly or silently stall.
7. **Write report** `.obi/reviews/<run_id>-rereview.md` with:
   - First line: `Verdict: PASS` or `Verdict: NEEDS FIXES`
   - Per-must-fix-item: address-status (addressed / partial / unaddressed) + evidence (file:line refs)
   - Test command + exit code
8. **Emit** `RE-REVIEW COMPLETE`. Orchestrator parses the Verdict line and loops to Phase 5 on
   NEEDS FIXES.

## Recipe M — README Review (inline)

Goal: emit `README REVIEW COMPLETE` (with verdict in artifact body) without dispatching
obi-readme-verifier. Produce report artifact `.obi/reviews/<run_id>-readme-review.md`.

Expected git state: README phase has either committed README.md updates OR emitted README SKIPPED.

1. **Read README.md** (top-to-bottom, not selective).
2. **Verify claims** — for each claim in README that names a file, function, command, or flag: grep
   the codebase for evidence. Each claim must resolve.
3. **Validate examples — PARSE ONLY, do NOT execute.** Language detection from fence info:
   - `js`/`javascript` → `node --check <tempfile>` (parse-only, no execution)
   - `ts`/`typescript` → `tsc --noEmit --target ES2020 --module ESNext <tempfile>` ONLY when tsc is
     on PATH; else soft-skip with a warning (do NOT use `node --check` for TS — it false-fails
     valid TS).
   - `python`/`py` → `python -c "import ast; ast.parse(open('<tempfile>').read())"` (parse-only via
     `ast.parse`; safer than `compile`).
   - `powershell`/`pwsh`/`ps1` → DO NOT use `powershell -Command "& { . '<tempfile>' }"` or any
     `Invoke-Expression` form — that's execution, not parsing. Use the PowerShell parser API
     directly. Both `tokens` and `errors` MUST be assigned to real variables before being passed
     by reference; `[ref]$null` does NOT bind correctly:
     ```powershell
     $tokens = $null
     $errors = $null
     [System.Management.Automation.Language.Parser]::ParseFile($tempfile, [ref]$tokens, [ref]$errors) | Out-Null
     if ($errors -and $errors.Count -gt 0) { <fail with $errors[0].Message> }
     ```
   - `bash`/`sh` → on Windows, resolve Git for Windows from
     `CLAUDE_CODE_GIT_BASH_PATH` or the installed `git.exe` sibling `bin\bash.exe`; never use
     ambient `System32\bash.exe` (WSL). Soft-skip with a warning if Git Bash is unavailable. Pass
     the complete source as one process argument to `bash -n -c <source>`. `-n` parses without
     executing; `-c` avoids Windows/WSL temp-path translation. Do not compose a shell command
     string or interpolate the source.
4. **First-time-reader scan** when README diff exceeds 30 lines (a heuristic pass by the same
   reviewer, not a context-free reader): re-read README as a first-time reader would and flag
   sections with undefined acronyms, forward references, or missing prereqs. Soft-warn in
   artifact; does NOT fail the recipe.
5. **Write report** `.obi/reviews/<run_id>-readme-review.md` with:
   - First line: `Verdict: PASS` or `Verdict: NEEDS FIXES`
   - Per-claim status + per-example parse result + first-time-reader warnings (if any)
6. **Emit** `README REVIEW COMPLETE`. Orchestrator parses Verdict; NEEDS FIXES loops to Phase 7.

## Recipe G — Release Gate (inline)

Goal: emit `RELEASE GATE PASSED` (or `RELEASE GATE FAILED: [reason]`). Produce report artifact
`.obi/reports/<run_id>-release-gate.md`.

Expected git state: Re-review has just passed (or been inlined to PASS). Working tree may be clean
OR have edits pending the gate's final commit — Release Gate is responsible for staging final
changes.

1. **git status** — `git status --porcelain`; capture state in artifact, do not require clean.
2. **Run one fresh full suite through bounded components** — detect the test command (shared
   probe). Phase 9 is the sole unconditional full-suite owner; earlier phases do not donate cached
   results. For obiwag-agents, the no-argument runner is known to exceed the foreground ceiling, so
   its required procedure is:
   - Build the expected Pester file set from the sorted, unique output of
     `git ls-files --cached --others --exclude-standard -- '*.tests.ps1'`.
   - For each index 1 through 3, run
     `tools/run-tests.ps1 -PowerShellOnly -ShardCount 3 -ShardIndex <INDEX> -ListTargets`. Parse
     only `TARGET=` lines and prove mechanically that the three lists are complete and disjoint:
     their union exactly equals the expected set, their total count equals the unique count, and
     no shard is empty. A target-audit mismatch fails before any tests run.
   - Run those same three commands without `-ListTargets`, then run
     `tools/run-tests.ps1 -PythonOnly` exactly once. Give each Pester shard the **10-minute Bash
     timeout** (`timeout: 600000`) and give the Python component the **5-minute Bash timeout**
     (`timeout: 300000`). Require a zero exit and nonzero executed tests from each, and sum the four
     component counts in the report. Together they are one full suite; do not also run the
     monolithic command.
   - If a component reaches its host timeout, identify it by captured PID, command line, and start
     time; terminate the exact timed-out process tree and verify it is gone. Record the cleanup and
     emit `RELEASE GATE FAILED: Pester component timed out at 10m` or `RELEASE GATE FAILED: Python
     component timed out at 5m`, as applicable. Never retry a timed-out component blindly and never
     leave an orphan.
   Other repositories may run a full detected command when it fits the applicable bound (10 minutes
   for a recognized Pester-only command, 5 minutes otherwise); otherwise they must use an equivalent
   complete/disjoint decomposition or a repository-approved bounded supervisor. Missing bounded
   execution is a release blocker, not permission to exceed the command-class limit.
3. **Run linter** using Recipe S detection and the shared fail-closed lint rules above. Must be green.
4. **Version bump** (obiwag-agents-specific; gate by `Test-Path tools/version.yaml`. Other repos
   skip):
   - Compute next version: read current from `tools/version.yaml` line `version: "X.Y.Z"`.
     obiwag-agents uses three-segment semver-ish (e.g. `0.69.32`). Determine bump type from the
     conventional-commit prefix of the AUTHOR commit (Phase 2 output) — read it from `git log` if
     Author committed, OR from the proposed commit message in `.obi/integration-report.md` if
     staged-but-not-committed:
     - Bump the patch segment only (Z+1, e.g. `0.69.46` → `0.69.47`), whatever the
       conventional-commit prefix. the user keeps this repo on the `0.69.x` line, so a `feat:` does not
       become `0.70.0` and the major/minor never change.
   - Run `tools\bump-version.ps1 -Version <next>`. NOTE: bump-version.ps1 takes mandatory
     `-Version` (NOT `-Patch`/`-Minor` — those flags do not exist). Optional `-DryRun` and
     `-Commit` switches are available; recipe stages files itself in step 9 so do NOT pass `-Commit`.
    - Verify all version-bearing source files updated. Enumerate one source path at a time with
      terminating errors; mixing leaf files and directories in one `Get-ChildItem -Path ...
      -Recurse` call can recurse the current tree for leaf inputs and silently continue after access
      failures:
      ```powershell
      $sourcePaths = @('tools', 'CLAUDE.md', 'README.md', 'docs', 'platforms', 'orchestration',
                       'phases', 'hooks', 'skills', 'policies')
      $sourceFiles = foreach ($sourcePath in $sourcePaths) {
          if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
              Get-Item -LiteralPath $sourcePath -ErrorAction Stop
          } else {
              Get-ChildItem -LiteralPath $sourcePath -Recurse -File -ErrorAction Stop
          }
      }
      $sourceFiles | Select-String -SimpleMatch '<OLD_VERSION>'
      ```
      Any enumeration error or remaining hit fails the recipe. `Select-String -Path <dir>
      -Recurse` is not valid PowerShell — `-Path` accepts files only and `Select-String` has no
      `-Recurse` parameter.
     If hits remain: recipe FAILS until zero hits. Note: `commands/` and `agents/` are DEPLOYED-target
     paths (created by deploy.ps1 under `~/.claude/`), NOT source paths — do NOT include them.
5. **Changelog entry** (obiwag-agents-specific): `bump-version.ps1` prepends a
   `TODO: describe this release.` stub under the new section in `CHANGELOG.md`. Replace that stub
   with a concise human-readable summary and fail if the new section retains the TODO. Release
   history does not belong in `tools/version.yaml`.
6. **Live deploy** (obiwag-agents-specific) — run the real deploy, not `-DryRun`. If any file under
   `tools/`, `CLAUDE.md`,
   `orchestration/`, `phases/`, `platforms/`, `policies/`, `docs/`, `skills/`, or `hooks/` changed,
   run a FULL live `tools\deploy.ps1` (no flags) with a **5-minute Bash timeout**
   (`timeout: 300000`). Capture stdout/stderr; recipe FAILS if deploy exits non-zero OR times out.
   On timeout, emit `RELEASE GATE FAILED: deploy.ps1 timed out at 5m — likely endpoint security rehash
   / MpEng scan / juju pull; investigate before retry`. Rationale: the only meaningful verification
   of endpoint security-trusted-path edits is actually deploying — `-DryRun` skips file writes which is
   the failure-mode-of-interest.
7. **Conventional-commit check**: `git log -1 --pretty=%B` must match
   `^(feat|fix|chore|docs|refactor|test|style|perf|build|ci)(\([^)]+\))?: .+`.
8. **Grep changed files** for `TODO`, `FIXME`, `XXX`, `console.log`, `Write-Debug`, `Write-Host` in
   production paths (exclude test files and intentionally-debug scripts).
9. **Stage by explicit file list** every task-owned source, documentation, test, version, and
   changelog change. Never use `git add -A`; preserve pre-existing user changes and exclude `.obi/`,
   `graphify-out/`, and other generated scratch state. Commit as `chore: release v<NEW>` OR fold
   into the feature commit if single-commit lane. **NEVER push from inside the recipe** — release
   gate stages, the user (or downstream automation) pushes.
10. **Write report** `.obi/reports/<run_id>-release-gate.md` with per-check verdict + captured
    outputs (including deploy.ps1 stdout/stderr from step 6).
11. **Emit** `RELEASE GATE PASSED` if all green. If any check fails: emit
    `RELEASE GATE FAILED: <first failing check>`; report body lists ALL failures (not just first).

Note: steps 4–6 are obiwag-agents-specific. When this recipe is copied to another repo, those steps
must be stripped or parameterized via project policy.

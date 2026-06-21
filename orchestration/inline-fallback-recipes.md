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

Recipes declare expected git state up front so the orchestrator can halt on contract violation
rather than silently doing the wrong thing.

## Shared probe — test-command detection

Used by Recipes R and G. Probe in order, take first hit:

1. `Test-Path tools, *.Tests.ps1` → `Invoke-Pester` (PS 5.1 host) / `Invoke-Pester -Output Detailed` (Pester 5+).
2. `Test-Path pyproject.toml` with `[tool.pytest*]` content OR `Test-Path pytest.ini` → `pytest`.
3. `Test-Path package.json` with a `test` script in scripts block → `npm test`.
4. `Test-Path go.mod` → `go test ./...`.
5. No discoverable command → emit blocking gap (recipe-specific handling; never silent pass).

## Recipe RV — Review (inline)

Goal: emit `REVIEW COMPLETE: PASS` or `REVIEW COMPLETE: FAIL [N] issues` without dispatching
obi-reviewer. Produce report artifact `.obi/reviews/<run_id>-review.md`.

Expected git state: Author has committed (HEAD subject matches conventional-commit prefix) OR
Author left changes staged (`git diff --staged --quiet` returns non-zero). If neither holds —
dirty working tree with nothing staged — halt with `NEEDS USER INPUT: Review recipe needs Author
commit or staged changes`.

1. **Detect diff source** (same logic as Recipe S step 1):
   - Staged changes present → use `git diff --staged` (capture file list via `git diff --staged
     --name-only`).
   - Else conventional-commit HEAD within last hour → use `git diff HEAD~1 HEAD` (file list via
     `git diff --name-only HEAD~1 HEAD`).
   - Else halt with NEEDS USER INPUT.
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
7. **Classify all findings** into Critical / Major / Minor / Nit. Count each tier separately.
8. **Write report** `.obi/reviews/<run_id>-review.md` with:
   - Header: target branch + commit sha or staged-diff marker.
   - Per-finding entry: `[tier] <category>: <detail>` with file:line refs.
   - Final line: `PASS` if zero Critical AND zero Major. `FAIL` otherwise.
9. **Emit**:
   - `REVIEW COMPLETE: PASS` if zero Critical AND zero Major findings.
   - `REVIEW COMPLETE: FAIL <N> issues` where `<N>` = Critical + Major count.
   The Integrate phase reads the report artifact for the detailed triage table.

Notes:
- Recipe RV intentionally elides the Codex adversarial-review pass that the dispatched
  `obi-reviewer` runs. Codex calls hit the network and add 30–90s; inline review skips that for
  determinism. When Codex review is required (large policy MRs, see
  `feedback_codex_pass_count_scales_with_scope`), escalate to Agent dispatch via the
  "input too large for inline" gate in `orchestration/obi-auto.md` Step 0.

## Recipe S — Simplify (inline)

Goal: emit `SIMPLIFY COMPLETE` (or `SIMPLIFY SKIPPED`) without dispatching obi-simplify. Produce
advisory report `.obi/reports/<run_id>-simplify.md`.

Expected git state: Author has just committed (HEAD subject matches
`^(feat|fix|refactor|chore|docs|test):`) OR Author left changes staged (`git diff --staged --quiet`
returns non-zero). If neither holds — dirty working tree with nothing staged — halt with
`NEEDS USER INPUT: Simplify recipe needs Author commit or staged changes; found uncommitted dirty
tree.`

1. **Detect diff source** — check staged FIRST (if Author left staged AND HEAD is any prior conventional
   commit, the recipe would otherwise diff the wrong thing):
   - If `git diff --staged --quiet` returns non-zero (staged changes present), use `git diff --staged`.
   - Else if HEAD subject matches `^(feat|fix|refactor|chore|docs|test):` AND `git log -1 --pretty=%ct`
     is within 1 hour of `Get-Date` (recent), treat as Author commit and use `git diff HEAD~1 HEAD`.
   - Else halt with NEEDS USER INPUT.
2. **Scan diff** for: unused vars, dead branches, redundant comments restating code, debug print
   statements, trailing whitespace, quote-style drift (PowerShell: prefer single).
3. **Edit in place** for each finding. After each edit run the project's lint (detect by file types
   in diff: `Invoke-ScriptAnalyzer .` for PS, `ruff check .` for Python, `golangci-lint run` for Go).
4. **Write report** `.obi/reports/<run_id>-simplify.md` summarizing scan findings + actions taken
   (or "no cleanup warranted"). Write this even on SKIPPED — downstream phases may inspect.
5. **Commit policy**:
   - If Author committed in step 1 AND cleanup landed: amend the Author commit (`git commit --amend
     --no-edit`). If the parent commit was already pushed (`git rev-parse '@{u}'` succeeds AND
     `git log '@{u}..HEAD'` is empty), DO NOT amend — create a separate `style: simplify post-author
     cleanup` commit. NOTE: `@{u}` MUST be single-quoted in PowerShell; the bare token is parsed as
     a hashtable literal (`@{...}`) and fails.
   - If Author left changes staged: stage cleanup edits too and let Integrate/Release Gate commit
     downstream — do not commit in this case.
6. **Emit** `SIMPLIFY COMPLETE` if lint passes AND any cleanup landed; `SIMPLIFY SKIPPED` if no
   cleanup warranted.

## Recipe R — Re-review (inline)

Goal: emit `RE-REVIEW COMPLETE` (with `Verdict: PASS` or `Verdict: NEEDS FIXES` in the artifact
body) without dispatching obi-rereviewer. Produce report artifact
`.obi/reviews/<run_id>-rereview.md`.

Expected git state: Integrate has committed all accepted review feedback. HEAD is the integration
commit, or — if integrate produced no changes — the Author commit. If `.obi/integration-report.md`
is missing, halt with NEEDS USER INPUT.

1. **Determine task base**: `git merge-base HEAD origin/master` (fall back to `origin/main` if
   master doesn't exist; final fallback `git rev-parse HEAD~10` if no remote). Store as $TaskBase.
2. **Read** `.obi/reviews/` for the latest review report (sort by filename, take last).
3. **Read** `.obi/integration-report.md` for the integrator's triage.
4. **Verify must-fix items** — for each: grep the changed files (`git diff --name-only
   $TaskBase..HEAD`) for evidence the fix landed. Confirm test coverage if review asked for it.
5. **Detect test command** (shared probe). If none discoverable: write artifact with
   `Verdict: NEEDS FIXES` and item "no discoverable test command — blocked on test infrastructure";
   emit `RE-REVIEW COMPLETE` and let orchestrator loop. Do NOT silently pass.
6. **Run the test command** with a **10-minute Bash timeout** (`timeout: 600000`). Must be green
   for verdict to be PASS. On timeout: write artifact with `Verdict: NEEDS FIXES` and item
   "test command exceeded 10-minute timeout — investigate hang before re-running"; emit
   `RE-REVIEW COMPLETE` and let orchestrator loop. Do NOT silently stall.
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
   - `bash`/`sh` → `bash -n <tempfile>` (parse-only flag — does not execute).
4. **Cold-reader check** when README diff exceeds 30 lines: re-read README as a first-time reader
   and flag sections with undefined acronyms, forward references, or missing prereqs. Soft-warn in
   artifact; does NOT fail the recipe.
5. **Write report** `.obi/reviews/<run_id>-readme-review.md` with:
   - First line: `Verdict: PASS` or `Verdict: NEEDS FIXES`
   - Per-claim status + per-example parse result + cold-reader warnings (if any)
6. **Emit** `README REVIEW COMPLETE`. Orchestrator parses Verdict; NEEDS FIXES loops to Phase 7.

## Recipe G — Release Gate (inline)

Goal: emit `RELEASE GATE PASSED` (or `RELEASE GATE FAILED: [reason]`). Produce report artifact
`.obi/reports/<run_id>-release-gate.md`.

Expected git state: Re-review has just passed (or been inlined to PASS). Working tree may be clean
OR have edits pending the gate's final commit — Release Gate is responsible for staging final
changes.

1. **git status** — `git status --porcelain`; capture state in artifact, do not require clean.
2. **Run tests** — detect test command (shared probe). Run with a **10-minute Bash timeout**
   (`timeout: 600000`). Must be green for PASSED. On timeout: emit `RELEASE GATE FAILED: test
   command timed out at 10m — investigate hang`. Do NOT silently stall.
3. **Run linter** (same detection as Recipe S). Must be green.
4. **Version bump** (obiwag-agents-specific; gate by `Test-Path tools/version.yaml`. Other repos
   skip):
   - Compute next version: read current from `tools/version.yaml` line `version: "X.Y.Z"`.
     obiwag-agents uses three-segment semver-ish (e.g. `0.69.32`). Determine bump type from the
     conventional-commit prefix of the AUTHOR commit (Phase 2 output) — read it from `git log` if
     Author committed, OR from the proposed commit message in `.obi/integration-report.md` if
     staged-but-not-committed:
     - **ALWAYS patch bump** (Z+1, e.g. `0.69.46` → `0.69.47`) regardless of the conventional-commit
       prefix — including `feat:`. **HARD RULE (the user, non-negotiable): NEVER bump the major or minor
       above `0.69`; the version stays in the `0.69.x` line forever.** This deliberately overrides the
       usual semver "feat → minor" convention — a `feat:` does NOT trigger a `0.70.0`.
   - Run `tools\bump-version.ps1 -Version <next>`. NOTE: bump-version.ps1 takes mandatory
     `-Version` (NOT `-Patch`/`-Minor` — those flags do not exist). Optional `-DryRun` and
     `-Commit` switches are available; recipe stages files itself in step 9 so do NOT pass `-Commit`.
   - Verify all version-bearing source files updated:
     `Get-ChildItem -Recurse -File -Path tools, CLAUDE.md, README.md, docs, platforms, orchestration, phases, hooks, skills, policies | Select-String -SimpleMatch '<OLD_VERSION>'`
     (NOTE: `Select-String -Path <dir> -Recurse` is NOT valid PowerShell — `-Path` accepts files
     only, `-Recurse` does not exist on Select-String. Always pipe from `Get-ChildItem -Recurse
     -File`.)
     If hits remain: recipe FAILS until zero hits. Note: `commands/` and `agents/` are DEPLOYED-target
     paths (created by deploy.ps1 under `~/.claude/`), NOT source paths — do NOT include them.
5. **Changelog entry** (obiwag-agents-specific): bump-version.ps1 already updates
   `tools/version.yaml` in step 4. Edit version.yaml if a human-readable change summary needs to
   be added for this release line.
6. **Live deploy** (obiwag-agents-specific) — this is the RISKY check the current obi-release-gate
   prompt explicitly requires; do NOT substitute `-DryRun`. If any file under `tools/`, `CLAUDE.md`,
   `orchestration/`, `phases/`, `platforms/`, `policies/`, `docs/`, `skills/`, or `hooks/` changed,
   run a FULL live `tools\deploy.ps1` (no flags) with a **10-minute Bash timeout**
   (`timeout: 600000`). Capture stdout/stderr; recipe FAILS if deploy exits non-zero OR times out.
   On timeout, emit `RELEASE GATE FAILED: deploy.ps1 timed out at 10m — likely an antivirus/security
   rescan or a large file set; investigate before retry`. Rationale: the only meaningful verification
   of whether the deploy is actually writing files is actually deploying — `-DryRun` skips file
   writes which is the failure-mode-of-interest.
7. **Conventional-commit check**: `git log -1 --pretty=%B` must match
   `^(feat|fix|chore|docs|refactor|test|style|perf|build|ci)(\([^)]+\))?: .+`.
8. **Grep changed files** for `TODO`, `FIXME`, `XXX`, `console.log`, `Write-Debug`, `Write-Host` in
   production paths (exclude test files and intentionally-debug scripts).
9. **Stage** the bumped version + changelog files (`tools/version.yaml` + the versioned files
   bump-version.ps1 touched). Commit as `chore: release v<NEW>` OR fold into the feature commit if
   single-commit lane. **NEVER push from inside the recipe** — release gate stages, the user (or
   downstream automation) pushes.
10. **Write report** `.obi/reports/<run_id>-release-gate.md` with per-check verdict + captured
    outputs (including deploy.ps1 stdout/stderr from step 6).
11. **Emit** `RELEASE GATE PASSED` if all green. If any check fails: emit
    `RELEASE GATE FAILED: <first failing check>`; report body lists ALL failures (not just first).

Note: steps 4–6 are obiwag-agents-specific. When this recipe is copied to another repo, those steps
must be stripped or parameterized via project policy.

# Codex Usage Recipes

> **Status:** v0.69.31 contract — replaces the deleted `skills/codex-adversarial-review/` Python wrapper.
> **Audience:** any phase or agent that wants a Codex second opinion.
> **Posture:** Codex is an escape hatch. Claude is primary. Codex output streams to the user's terminal — the transcript is the audit. There is no `.obi/review/codex-findings-*.{md,jsonl}` artifact anymore.

## Profile and CLI

- Codex CLI: `codex.cmd` (npm package `codex-cli`, verified at 0.128.0 on workstation).
- Profile: `review` — defined in the user's `~/.codex/review.config.toml` (newer codex) or, on legacy codex, as `[profiles.review]` in `~/.codex/config.toml`. NOT in this repo. Selects model (gpt-5.5), reasoning level (high), and tool policy. **Newer codex REJECTS the legacy `[profiles.review]` table** with `Error loading config.toml: --profile review cannot be used while ... legacy [profiles.review]`. Migrate by moving those keys into a top-level-keyed `~/.codex/review.config.toml` and deleting the `[profiles.review]` table from `config.toml`.
- Setup validation: `python tools/healthcheck.py --quick --strict` checks that `codex` is on PATH and that the review profile is configured (`~/.codex/review.config.toml`, or legacy `[profiles.review]`).
- All recipes shell out via `Bash` tool; output streams directly to the PowerShell terminal where Claude Code is running. Do not redirect.

## Recipe 1 — Branch-diff review (Phase 4 default)

Use this when you have a branch diff to review against `origin/master`. Wired into `phases/04-review/command.md` Step 0 and `platforms/claude-code/agents/obi-reviewer.md` Step 0.

```bash
{
  cat <<'PROMPT'
You are reviewing the branch diff below. Look for: race conditions, error-path
gaps, resource leaks, hallucinated APIs, missing tests, and zero-hallucination
violations. Be specific: cite file:line. Output structured findings.

---DIFF---
PROMPT
  git diff origin/master...HEAD   # run from inside the project repo
} | codex --profile review exec --json -
```

The grouped braces send a single concatenated stdin (prompt + diff) into codex. The earlier `pipe | codex - <<HEREDOC` form is **broken**: bash's here-doc redirect overrides the pipe, so codex sees only the prompt and never the diff. Always concatenate.

Reviewer reads Codex output from the terminal, then runs the standard Claude review per `skills/reviewing-code/SKILL.md` and produces the synthesized Output Contract with `[Codex] / [Claude] / [Synthesis] / [Codex — disputed]` attribution.

If `codex` is not on PATH, or `codex --profile review` fails, note "Codex unavailable — proceeding with Claude-only review" in the synthesis section and continue. Phase 4 must not block on Codex availability.

## Recipe 2 — Plan-file critique (rigor=max Phase 0)

Plan files at `~/.claude/plans/<task>.md` live outside any git repo. `codex exec` aborts with "not a git repository" against such paths. Workaround: `tools/codex-plan-prep.ps1` performs an ephemeral `git init` in `$env:TEMP`, copies the plan file in, runs `codex --cd <tmp>`, and cleans up. The helper also pipes the plan body + a review instruction into codex's stdin when CodexArgs ends with `-`, so codex actually sees the plan content.

```powershell
# Default (uses --profile review exec - and a built-in plan-review prompt)
.\tools\codex-plan-prep.ps1 -PlanPath "$HOME\.claude\plans\my-plan.md"

# Custom codex args
.\tools\codex-plan-prep.ps1 -PlanPath "$HOME\.claude\plans\my-plan.md" `
    -CodexArgs @('--profile','review','exec','--json','-')
```

Args go through the script's `-CodexArgs @(...)` array parameter — PowerShell scripts cannot reliably consume `--` as a stop-parsing separator under `powershell.exe -File`, so the array form is the supported invocation. The helper injects `-C <tempdir>` first, then appends your CodexArgs.

The transcript on the user's terminal is the audit. There is no `.obi/plan-codex-*.md` file written.

If the helper fails (codex not on PATH, git init fails, etc.), the calling phase must record `runtime.phase0.codex.status: unavailable` in the plan file write-back block and continue.

When a Codex critique produces an accepted plan change, log the catch:

    & $env:OBI_HOME\tools\log-codex-catch.ps1 `
        -Repo "<target-project>" -Ref "<run-id>" `
        -Phase plan -Category <category> -Severity <severity> `
        -Summary "<one-line accepted change>"

## Recipe 3 — Free-form second opinion

Use sparingly — Claude is primary. Reach for this when you want Codex's take on something that isn't a branch diff or a plan file: a single paragraph of code, a design question, a tricky regex.

```bash
codex --profile review exec - <<'PROMPT'
<your free-form prompt and context here>
PROMPT
```

If you need a working directory other than the current cwd, add `-C /path/to/dir`.

## Failure-mode quick reference

| Symptom | Likely cause | Action |
|---|---|---|
| `codex: command not found` | Codex CLI not installed or not on PATH | Note "Codex unavailable" and continue with Claude only |
| `codex exec` exits non-zero with "not a git repository" | Recipe 1 or 3 invoked outside a git repo | Use Recipe 2 (`codex-plan-prep.ps1`) instead |
| Output hangs for >60s with no progress | Codex sandbox blocking tool calls | Cancel (Ctrl+C), note "Codex tools blocked" in review, continue |
| Codex returns malformed JSON when `--json -` is used | Profile or CLI version mismatch | Drop `--json` and use plain text output; reviewer parses manually |

These are advisory. Codex is an escape hatch — its failures should never block the workflow.

## Verdicts vs availability

Two distinct outcomes, two distinct policies:

| Outcome | Definition | Workflow |
|---|---|---|
| **Codex unavailable** | CLI not on PATH, sandbox blocked tools >60s, `codex exec` exits non-zero before producing a verdict, network down | Note "Codex unavailable" and continue with Claude-only review. Not a fatal gate. |
| **Codex FAIL verdict** | Codex ran to completion and returned findings: hallucinated APIs, critical issues, plan inconsistencies, "this plan will fail because X" | **HARD STOP** per `policies/hard-stop-conditions.md` §6. Address every finding before proceeding, or record an explicit the user override in `.obi/state/codex-overrides.md`. |

The "Codex unavailability is not a fatal gate" non-goal below refers ONLY to availability. Multi-pass convergence (per memory `feedback_codex_multipass_converges_two_rounds`) targets a PASS verdict — running more passes does not mean accumulated FAILs become advisory.

## Non-goals

- **Codex is not a Claude replacement.** Every recipe is followed by a Claude pass. Reviewer attributes every finding.
- **Codex unavailability is not a fatal gate.** Workflow continues if Codex cannot run (CLI missing, sandbox dead, network down). A Codex FAIL verdict from a successful run IS a fatal gate — see "Verdicts vs availability" above and `policies/hard-stop-conditions.md` §6.
- **Codex does not write per-session artifacts.** No `.obi/review/codex-findings-*.{md,jsonl}` file. The terminal transcript is the session record. The catch log (`.obi/codex-catches.jsonl`) is committed shared knowledge, not a session artifact.

## Catch Log

The catch log (`.obi/codex-catches.jsonl`) records every confirmed Codex-only finding at the point of acceptance. It is committed to the repo as shared knowledge.

**Confirmation rule:** utterance does not equal catch; acceptance equals catch. A Codex finding becomes a catch only when the integrator accepts and applies it (or explicitly disputes it). Unapplied findings are not logged.

**Category taxonomy:**

| Category | Meaning |
|---|---|
| `invented-api` | Codex caught a hallucinated API, cmdlet, or endpoint |
| `missed-edge-case` | Codex caught an unhandled edge case |
| `test-gap` | Codex identified a missing or insufficient test |
| `security` | Security issue (secrets, injection, access control) |
| `regression-risk` | Change could regress existing behavior |
| `doc-mismatch` | Documentation contradicts implementation |
| `plan-gap` | Plan omits a required step or dependency |
| `other` | Does not fit the above categories |

**Schema:** each JSONL line contains `ts` (ISO 8601), `repo`, `ref`, `phase` (review/plan/freeform), `category`, `severity` (high/medium/low), `summary`, `disputed` (bool), `dispute_resolution` (codex-right/claude-right/unresolved/null).

**Helper invocation:**

    & $env:OBI_HOME\tools\log-codex-catch.ps1 `
        -Repo "<project>" -Ref "<issue>" `
        -Phase <review|plan|freeform> -Category <category> -Severity <severity> `
        -Summary "<one-line finding>"

For disputed findings add `-Disputed -DisputeResolution <codex-right|claude-right|unresolved>`. A later correction is allowed by re-appending with the same `ref` -- latest line wins at analysis time.

## Cross-references

- `phases/04-review/command.md` — Phase 4 wiring for Recipe 1.
- `platforms/claude-code/agents/obi-reviewer.md` — Step 0 entry point.
- `skills/reviewing-code/SKILL.md` — attribution rules and synthesized Output Contract.
- `tools/codex-plan-prep.ps1` — Recipe 2 helper (ephemeral git init).
- `orchestration/obi-auto.md` — rigor=max Phase 0 calls Recipe 2 on the resolved plan file.

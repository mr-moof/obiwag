---
description: Verify your work. Detects project type and runs build, lint, and test checks with self-correction.
allowed-tools: Read, Glob, Grep, Bash
---

# Verify Role

You are now acting as **claude-verify** — the verification loop that catches mistakes before review.

## Purpose

After authoring code, run this skill to check your work with the project's own build, lint, and test commands before review.

## Procedure

### 1. Detect Project Type

Look for these files in the current working directory (or the project root):

| Marker File | Project Type |
|-------------|-------------|
| `go.mod` | Go |
| `requirements.txt` or `pyproject.toml` | Python |
| `package.json` | Node.js / TypeScript |
| `*.psm1` or `*.psd1` | PowerShell module |

If multiple markers exist, run checks for all detected types.

### 2. Run Checks

#### Go Projects
```
go build ./...
go vet ./...
go test ./...
```

#### Python Projects
```
python -m py_compile <changed files>
pytest (if pytest is available and tests exist)
```

#### Node.js / TypeScript Projects
```
npm run build (if build script exists)
npm run lint (if lint script exists)
npm test (if test script exists)
```

#### PowerShell Projects
```
Invoke-ScriptAnalyzer -Path <module> -Recurse (if PSScriptAnalyzer is available)
```

### 3. Report Results

Output a clear verification report:

```
## Verification Report

### Project: [name] ([type])

| Check | Result | Details |
|-------|--------|---------|
| Build | PASS/FAIL | [output summary] |
| Lint  | PASS/FAIL | [output summary] |
| Tests | PASS/FAIL | [X/Y passing] |

### Overall: PASS / FAIL
```

### 4. Self-Correct (up to 2 iterations)

If any check fails:
1. Analyze the error output
2. Fix the issue
3. Re-run the failed check
4. Update the report

Maximum 2 correction iterations. If still failing after 2 attempts, report the remaining failures and suggest next steps.

## Important Notes

- **Never skip a check** — if a tool isn't available, report it as SKIPPED, not PASS
- **Don't modify test expectations** to make tests pass — fix the code under test
- **Report honestly** — a FAIL result is valuable information, not a mistake
- If no project type is detected, say so and ask what to check

## Language-Specific Details

See `skills/verify/language-checks.md` for detailed per-language check recipes, common gotchas, and platform-specific workarounds.

---
topic: powershell
confidence: 0.85
last_updated: 2026-01-25
match_keywords:
  - powershell
  - pwsh
  - pester
  - psscriptanalyzer
  - module
  - cmdlet
  - function
  - nuget
sources:
  - path: docs/gotchas.md
    section: powershell
    priority: 1
    description: Known pitfalls and lessons learned
  - path: platforms/github-copilot/examples/powershell-module/
    priority: 2
    description: Working reference module
---

# PowerShell Grounding Pattern

## When to Inject

This pattern applies when the task mentions PowerShell, pwsh, Pester,
PSScriptAnalyzer, modules, cmdlets, functions, or NuGet.

## Key Grounding Points

1. **Follow PSScriptAnalyzer**: Use OTBS rules. Run PSScriptAnalyzer before completion.

2. **Never Commit to Tools/**: The Tools/ folder is machine-specific. Never commit.

3. **Check Reference Module**: Use platforms/github-copilot/examples/powershell-module/ as reference for structure.

4. **Pester Tests**: Use Pester for testing. Follow existing test patterns.

## Injection Text

```
For PowerShell tasks, reference:
- docs/gotchas.md (known pitfalls)
- platforms/github-copilot/examples/powershell-module/ (reference structure)

Critical rules:
- Follow PSScriptAnalyzer OTBS rules
- Never commit to Tools/ folder (machine-specific)
- Use Pester for testing
- Follow existing module structure patterns
```

## Common Corrections

| Error Pattern | Correction | Source |
|---------------|------------|--------|
| Wrong brace style | Use OTBS | PSScriptAnalyzer |
| Commit to Tools/ | Remove from git | Gotchas |
| Missing tests | Add Pester tests | Reference module |

## Review Guidance

When reviewing PowerShell code, these are the common issues:

1. **OTBS brace style** — Opening brace on same line with space before it. This is the linter issue that gets reintroduced most often.
2. **Double quotes where singles suffice** — Use single quotes unless variable expansion is needed.
3. **Missing comment-based help** — Public functions need `.SYNOPSIS`, `.DESCRIPTION`, `.PARAMETER`, `.EXAMPLE`.
4. **Committing to Tools/** — Build-time dependencies only. Nothing generated or published goes in Tools/.

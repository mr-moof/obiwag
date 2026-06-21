---
name: platform-awareness
description: Windows platform constraints for Claude Code. Auto-apply when generating Bash commands, file paths, PowerShell scripts, or hook configurations.
user-invocable: false
---

# Platform Awareness

This guidance applies when running on **Windows** with Git Bash as the shell. These constraints apply to all tool calls in that environment.

## Path Rules

- **NEVER use Windows backslash paths** (`C:\dir\file`) in Bash commands — Bash interprets `\` as escape.
- **Use forward slashes** (`C:/dir/file`) or MSYS paths (`/c/dir/file`) or bare command names.
- **Quote paths with spaces** using double quotes.

## Shell Constraints

- `.sh` scripts do not execute natively on Windows — prefer PowerShell or cross-platform approaches.
- For complex PowerShell commands through Bash, write a `.ps1` helper file and call with:
  ```
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File <script.ps1>
  ```

## Python Execution Recipe

Always follow this two-step pattern:

1. **Write** the script to a temp file (e.g., `/tmp/script.py`)
2. **Bash**: `python /tmp/script.py`

Never:
- Use `python -c "..."` inline (hook blocks it, approval prompt pollutes settings)
- Use Windows backslash paths like `C:\Python314\python.exe`
- Use bare `python` — it's on PATH, no full path needed

## `/tmp/` Path Behavior

On Windows Git Bash, `/tmp/` maps to a real directory (usually `C:\Users\<user>\AppData\Local\Temp\` or the MSYS `/tmp`). Files written there persist for the session but may be cleaned on reboot.

## Execution Restrictions (environment-dependent)

Some managed Windows environments restrict where scripts and binaries may execute (allow-listing or restrictive execution policies). If a command fails with "Access is denied" even though file ACLs look correct, suspect an execution-policy / allow-listing restriction in your environment rather than a code bug.

**Rules of thumb:**
- For local HTTP servers, prefer `python -m http.server <port>` (Python is widely available and rarely restricted) over `npx serve` or similar.
- When a command fails with "Access is denied", check execution-policy / allow-listing before deeper debugging.

## Hook and Config Safety

- When editing hooks or configs, verify you're editing the **ACTIVE deployed file**, not just the repo source.
- Test that hook scripts exist and are reachable BEFORE committing config changes that reference them.
- Before modifying `settings.json`, `settings.local.json`, or hook configs — READ the current file first.
- After fixing hooks or configs, VERIFY the fix works with evidence before claiming success.

## Heredoc Safety

- NEVER put `#`-prefixed lines (markdown headers, comments) inside heredocs in Bash commands. This triggers Claude Code's built-in safety prompt.
- Instead: Write content to a temp file with the Write tool, then reference it with `$(cat /tmp/desc.txt)`.

## See Also

- `skills/platform-awareness/powershell-interop.md` — PS 5.1 and Pester 3.4 constraints

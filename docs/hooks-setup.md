---
domain: deployment
audience: maintainer
status: current
related:
  - DEPLOYMENT.md
  - docs/hooks-architecture.md
  - hooks/core/README.md
---

# Hooks Setup Guide

Comprehensive guide for configuring Obi Wag hooks in Claude Code.

## Overview

Hooks provide Obi's self-healing memory system by observing tool usage and learning from corrections.

## Architecture

```
~/.claude/
├── settings.json            # Contains hook configuration under "hooks" key
├── hooks/
│   ├── hook_wrapper.cmd    # Dispatch wrapper (called by settings.json hooks)
│   ├── pre_tool_use.py     # Runs before each tool call
│   ├── post_tool_use.py    # Runs after each tool call
│   ├── session_start.py    # Runs when Claude Code starts
│   ├── stop.py             # Runs when session ends
│   └── core/               # Shared hook utilities
```

## Hook Timeouts

Each hook has a timeout limit. If exceeded, the hook is skipped and Claude Code continues.

| Hook | Timeout | What happens if exceeded |
|------|---------|--------------------------|
| SessionStart | 5 seconds | Welcome message / pattern injection skipped |
| PreToolUse | 3 seconds | Safety check skipped, tool proceeds |
| PostToolUse | 3 seconds | Tool usage logging skipped |
| Stop | 10 seconds | Session summary / correction analysis skipped |

> **Note:** Hooks failing or timing out **does not** block Claude Code. The system degrades gracefully—you just lose memory system features.

## Installation Methods

### Method 1: Automatic (Recommended)

Use the deployment script which handles everything:

```powershell
cd ~/source/obiwag-agents

# Set environment variable first
[System.Environment]::SetEnvironmentVariable('CLAUDE_PROJECT_ROOT', (Get-Location).Path, 'User')

# Deploy
.\tools\deploy.ps1

# Verify
python tools\healthcheck.py
```

### Method 2: Manual (Troubleshooting)

When deployment script fails or for custom setups:

**Step 1: Copy hooks**
```powershell
$OBI_REPO = "C:\Users\username\source\obiwag-agents"
Copy-Item -Path "$OBI_REPO\hooks\*" `
          -Destination "$env:USERPROFILE\.claude\hooks\" `
          -Recurse -Force
```

**Step 2: Set environment variable**
```powershell
[System.Environment]::SetEnvironmentVariable(
    'CLAUDE_PROJECT_ROOT',
    'C:\Users\username\source\obiwag-agents',
    'User'
)
```

**Step 3: Restart shell/Claude Code**
```powershell
# Close and reopen terminal, then:
echo $env:CLAUDE_PROJECT_ROOT  # Should show your path
```

### Method 3: Absolute Paths (when environment variables don't work)

When `CLAUDE_PROJECT_ROOT` isn't being picked up, edit the `"hooks"` key in `~/.claude/settings.json` to use absolute paths in the `hook_wrapper.cmd` commands:

```json
{
  "hooks": {
    "SessionStart": [{ "command": "\"C:/Users/you/.claude/hooks/hook_wrapper.cmd\" session_start", "timeout": 10 }],
    "PreToolUse": [{ "command": "\"C:/Users/you/.claude/hooks/hook_wrapper.cmd\" pre_tool_use", "timeout": 5 }],
    "PostToolUse": [{ "command": "\"C:/Users/you/.claude/hooks/hook_wrapper.cmd\" post_tool_use", "timeout": 10 }],
    "Stop": [{ "command": "\"C:/Users/you/.claude/hooks/hook_wrapper.cmd\" stop", "timeout": 10 }]
  }
}
```

**Key points:**
- Hook configuration lives inline in `settings.json`, not in a separate `hooks.json` file
- Use forward slashes (`/`) or escaped backslashes (`\\\\`)
- Windows paths: `C:/Users/...` (forward slashes work in Python/JSON)

## Verification

### Check Environment Variable
```powershell
# Should output your obiwag-agents path
echo $env:CLAUDE_PROJECT_ROOT
```

### Test Hook Execution
```powershell
# Should run without errors
python "$env:CLAUDE_PROJECT_ROOT\hooks\pre_tool_use.py"
```

### Run Health Check
```powershell
cd ~/source/obiwag-agents
python tools/healthcheck.py
# Should show: [OK] Core module imports: all successful
# and [OK] for each hook execution test
```

> **Note:** Structural hook checks (file presence, JSON validity in settings.json)
> are handled by `config-guardian.ps1`. The health check tests runtime execution only.

## Troubleshooting

### Error: "python: can't open file '.../$HOME/...'"

**Cause:** Hooks using Unix `$HOME` variable on Windows

**Fix:**
```powershell
# Edit ~/.claude/settings.json "hooks" entries
# Replace all "$HOME" with actual path or "%USERPROFILE%"
```

### Error: "python: can't open file '.../$ {CLAUDE_PROJECT_ROOT}/...'"

**Cause:** Environment variable not set

**Fix Option 1:**
```powershell
[System.Environment]::SetEnvironmentVariable('CLAUDE_PROJECT_ROOT', '<your-path>', 'User')
# Then restart Claude Code
```

**Fix Option 2:** Use absolute paths (see Method 3 above)

### Hooks Blocking All Commands

**Emergency disable:**
```powershell
claude config set hooks.enabled false
```

**Or remove hook entries from settings.json:**
```powershell
# Edit ~/.claude/settings.json and remove or comment out the "hooks" key
```

### Hooks Not Running (No Output/Errors)

Check if enabled:
```powershell
claude config get hooks.enabled
# Should return: true
```

Enable if needed:
```powershell
claude config set hooks.enabled true
```

## Platform-Specific Notes

### Windows

- In `settings.json` hook commands, use forward slashes or escaped backslashes for paths (e.g. `C:/Users/you/.claude/...`).
- If `tools/deploy.ps1` is blocked by execution policy, allow local/signed scripts for the session: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.

### Linux/Mac

Standard `$HOME` expansion works:
```json
{
  "command": "python \"${CLAUDE_PROJECT_ROOT}/hooks/session_start.py\""
}
```

## See Also

- `DEPLOYMENT.md` - Full deployment guide
- `tools/healthcheck.py` - Validate deployment
- `docs/memory-system.md` - How memory system works

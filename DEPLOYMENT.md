---
domain: deployment
audience: maintainer
status: current
related:
  - docs/prerequisites.md
  - docs/hooks-setup.md
---

# Obi Wag Deployment Guide

Deploy Obi Wag to Claude Code and Codex from a single source of truth.

**Before you start:** Install the tools listed in [`docs/prerequisites.md`](docs/prerequisites.md) (Git, Python 3.8+, Node 18+, Claude Code, PowerShell 5.1+, gh). This guide assumes they are already on PATH.

---

## Quick Start

Clone the repo anywhere you like, install the one Python dependency, deploy, and verify.

### Windows (PowerShell)

```powershell
# Step 1: Clone (anywhere — C:\src is just an example)
git clone https://github.com/user/obiwag-agents.git C:\src\obiwag-agents

# Step 2: Install Python dependencies (one-time)
python -m pip install pyyaml

# Step 3: Deploy
cd C:\src\obiwag-agents
.\tools\deploy.ps1

# Step 4: Verify setup (optional quick environment check)
.\tools\verify-setup.ps1

# Step 5: Verify deployment
python tools\healthcheck.py --quick
```

> **Windows execution policy:** If PowerShell refuses to run `deploy.ps1` ("running scripts is disabled on this system"), allow local scripts for your user with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or invoke it via `pwsh -File tools\deploy.ps1`.

**Deployed tools location.** `tools/deploy.ps1` writes the runtime PowerShell helpers (`config-guardian.ps1`, `codex-plan-prep.ps1`, `run-grep-gates.ps1`, `parse-plan-phase0.ps1`, `probes/`, `lib/`, etc.) under `$OBI_HOME` (defaults to a local tools directory set by deploy). The deploy step sets the User-scope env var `OBI_HOME` and writes `$env:OBI_HOME` for the current session. **Restart Claude Code after the first deploy** so child processes inherit the User-scope value. Callers (`CLAUDE.md`, `orchestration/obi-auto.md` rigor=max sections, `tools/statusline-command.ps1`) reference `$env:OBI_HOME\tools\<script>.ps1`.

### macOS / Linux

```bash
# Clone (anywhere — ~/obiwag-agents is just an example)
git clone https://github.com/user/obiwag-agents.git ~/obiwag-agents

# Install Python dependencies
python -m pip install pyyaml

# Deploy (PowerShell 7+ / pwsh)
cd ~/obiwag-agents
pwsh -File tools/deploy.ps1

# Verify
python tools/healthcheck.py
```

---

## Verification

After deployment, verify the setup using a two-tier approach.

### Tier 1: Quick Environment Check (Windows)

Run the quick environment check. `verify-setup.ps1` is a thin shim that forwards to
`config-guardian.ps1 -Quick -CheckOnly` (the consolidated structural validator):

```powershell
cd C:\src\obiwag-agents   # or wherever you cloned
.\tools\verify-setup.ps1
```

**What it checks (structural):**
- `CLAUDE_PROJECT_ROOT` environment variable
- Claude Code CLI availability
- Python installation
- Hooks configuration (JSON validity, path issues)
- Commands deployment
- Repository access

Hook *execution* is a runtime check, verified by Tier 2 (`healthcheck.py`), not the quick check.

**Expected output:** All checks showing `[OK]`.

**If checks fail:** See the [Troubleshooting](#troubleshooting) section below.

### Tier 2: Runtime Health Check (All Platforms)

Run the runtime health check to validate deployment state (hooks execution, drift, version sync).
Structural checks (JSON validity, hook presence, heredoc pollution, dependencies) are handled by `config-guardian.ps1`, which `deploy.ps1` runs post-deploy.

```powershell
# Quick smoke test (recommended for routine checks)
python tools\healthcheck.py --quick

# Full validation (recommended after major changes)
python tools\healthcheck.py

# Verbose output (for debugging)
python tools\healthcheck.py -v
```

**Expected output:**
```
STATUS: HEALTHY

Obi Wag is properly deployed and ready to use.
- Claude Code: Run /obi to start orchestrator
- Codex: use /obi or /review as prompt-level commands
```

### In-Session Verification (Claude Code)

After scripts pass, test inside a Claude Code session:

**Start Claude Code:**
```powershell
claude
```

**Test 1: Basic functionality**
```
hello
```
Expected: Normal response, no hook errors

**Test 2: Obi commands available**
```
/obi
```
Expected: Shows Obi Wag orchestrator instructions

**Test 3: Autonomous mode**
```
/obi-auto
```
Expected: Asks for task description

---

## When to Run Verification

| Scenario | Run This | Why |
|----------|----------|-----|
| **Initial deployment** | `tools\verify-setup.ps1` + `tools\healthcheck.py --quick` | Catch configuration issues early |
| **After `git pull` + redeploy** | `tools\healthcheck.py --quick` | Ensure updates deployed correctly |
| **Troubleshooting issues** | `tools\verify-setup.ps1` (Windows) | Diagnose environment problems |
| **New machine/session** | `tools\verify-setup.ps1` | Verify environment setup |
| **Before major work** | `tools\healthcheck.py` (full) | Runtime pre-flight check (hooks, drift, version) |

---

## Verification Documentation

For detailed verification procedures, see:
- **`tools/verify-setup.ps1`** - Quick-check shim (forwards to `config-guardian.ps1 -Quick`)
- **`tools/healthcheck.py`** - Runtime health check source (hooks execution, drift, version sync)
- **`tools/config-guardian.ps1`** - Structural config validator (JSON, hook files, dependencies)

---

## Architecture Overview

```
obiwag-agents/
├── phases/                        # 10-phase lifecycle (commands live here)
│   ├── 01-discovery/command.md    # → ~/.claude/commands/discovery.md
│   ├── 02-author/command.md       # → ~/.claude/commands/author.md
│   ├── 03-simplify/command.md     # → ~/.claude/commands/simplify.md
│   └── ...                        # 04-review through 10-learning
│
├── orchestration/                 # Obi orchestrator commands
│   ├── obi.md                     # → ~/.claude/commands/obi.md
│   ├── obi-auto.md                # → ~/.claude/commands/obi-auto.md
│   └── obi-memory-review.md
│
├── policies/                      # Governance (markdown)
│   ├── zero-hallucination.md
│   ├── three-strike-rule.md
│   └── ...                        # express-lane, hard-stop, vendor-rules
│
├── hooks/                         # Python hooks (memory system)
│   ├── hook_wrapper.cmd           # Hook entry point (called by settings.json)
│   ├── session_start.py
│   ├── pre_tool_use.py
│   ├── post_tool_use.py
│   ├── stop.py
│   └── core/                      # Shared hook modules
│
├── users/                         # User-specific settings
│   └── <username>/settings.json   # → ~/.claude/settings.json
│
├── skills/                        # Methodology guides
├── docs/                          # Documentation
├── platforms/codex/               # Codex AGENTS.md + hooks → repo/.codex + ~/.codex
└── tools/                         # deploy.ps1, healthcheck.py
```

---

## Prerequisites

See [`docs/prerequisites.md`](docs/prerequisites.md) for the full list of required tools, version floors, and OS-specific notes. At minimum, you need Git, Python 3.8+ with `pyyaml`, Node 18+, Claude Code, PowerShell 5.1+ (or PowerShell 7+ / `pwsh`), and gh CLI.

One-time Python dependency install:
```powershell
python -m pip install pyyaml
```

---

## Deployment Targets

### Claude Code

Files are deployed to `~/.claude/`:

```
~/.claude/
├── CLAUDE.md              # Main configuration
├── settings.json          # User-specific settings (if user dir exists)
├── commands/              # Slash commands (17 total)
│   ├── author.md          # /author - Write code
│   ├── discovery.md       # /discovery - Research phase
│   ├── doc.md             # /doc - Co-author specs
│   ├── integrate.md       # /integrate - Apply feedback
│   ├── learning.md        # /learning - Capture learnings
│   ├── obi.md             # /obi - Manual orchestrator
│   ├── obi-auto.md        # /obi-auto - Autonomous mode
│   ├── obi-collect.md     # /obi-collect - Collect artifacts
│   ├── obi-memory-review.md # /obi-memory-review - Review learnings
│   ├── obi-swarm.md       # /obi-swarm - Parallel issue swarm
│   ├── obi-update.md      # /obi-update - Check for updates
│   ├── readme.md          # /readme - Documentation
│   ├── readme-review.md   # /readme-review - Verify docs
│   ├── release.md         # /release - Release gate
│   ├── re-review.md       # /re-review - Verify integration
│   ├── review.md          # /review - Code review
│   └── simplify.md        # /simplify - Post-author cleanup
└── hooks/                 # Python hooks (deployed by deploy.ps1)
    ├── hook_wrapper.cmd
    └── *.py
```

**Note:** `settings.json` is only deployed if `users/<your-username>/settings.json` exists in the repo. See "User-Specific Settings" section below.

### User-Specific Settings (Claude Code)

The deploy scripts support user-specific `settings.json` files, allowing each user to maintain their own Claude Code configuration in the repo.

**Location:** `users/<username>/settings.json`

**How it works:**
1. Deploy script checks for `users/$USERNAME/settings.json`
2. If found, copies it to `~/.claude/settings.json`
3. If not found, no settings.json is deployed (existing settings preserved)

**Use case:** Sync your Claude Code configuration (permissions, hooks, model preferences) across multiple machines via git.

**Example structure:**
```
users/
├── user/
│   └── settings.json    # the user's settings
├── jsmith/
│   └── settings.json    # John's settings
└── ...
```

**To add your own settings:**
```powershell
# 1. Create your user directory
mkdir users/$env:USERNAME

# 2. Copy your current settings
Copy-Item "$env:USERPROFILE\.claude\settings.json" "users/$env:USERNAME/settings.json"

# 3. Commit and push
git add users/$env:USERNAME/
git commit -m "Add my user-specific settings"
git push
```

---

## Update Workflow

When policies or roles change, pull the latest and redeploy:

```powershell
cd C:\src\obiwag-agents   # or wherever you cloned

# Pull latest changes
git pull

# Redeploy
.\tools\deploy.ps1

# Verify
.\tools\verify-setup.ps1        # Windows quick environment check (optional)
python tools\healthcheck.py --quick
```

On macOS/Linux, run `pwsh -File tools/deploy.ps1` in place of `.\tools\deploy.ps1`.

---

## Deployment Script Options

### PowerShell (deploy.ps1) — Windows

```powershell
.\tools\deploy.ps1                    # Full deployment
.\tools\deploy.ps1 -ClaudeOnly        # Claude Code only
.\tools\deploy.ps1 -CodexOnly         # Codex only
.\tools\deploy.ps1 -DryRun            # Preview without changes
.\tools\deploy.ps1 -Force             # Overwrite drifted/locally-modified files
```

If PowerShell blocks the script, see the [Windows execution policy](#windows-powershell) note above (`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or run via `pwsh -File`).

### macOS / Linux

Use PowerShell 7+ (`pwsh`) to run `deploy.ps1` cross-platform:

```bash
pwsh -File tools/deploy.ps1                     # Full deployment
pwsh -File tools/deploy.ps1 -ClaudeOnly          # Claude Code only
pwsh -File tools/deploy.ps1 -DryRun              # Preview changes
```

---

## Hooks Setup (Claude Code Only)

Hooks provide the memory system. They require proper environment configuration to work correctly.

### Prerequisites

Hooks require the `CLAUDE_PROJECT_ROOT` environment variable to be set, pointing to your obiwag-agents directory.

**Windows — Set permanently:**
```powershell
# Option 1: Set for current user (recommended)
[System.Environment]::SetEnvironmentVariable('CLAUDE_PROJECT_ROOT', 'C:\src\obiwag-agents', 'User')

# Option 2: Set for current session only (temporary)
$env:CLAUDE_PROJECT_ROOT = "C:\src\obiwag-agents"

# Verify it's set
echo $env:CLAUDE_PROJECT_ROOT
```

**macOS / Linux:**
```bash
# Add to ~/.bashrc or ~/.zshrc
export CLAUDE_PROJECT_ROOT="$HOME/obiwag-agents"

# Reload shell
source ~/.bashrc  # or source ~/.zshrc
```

### Deploy Hooks

**Option A: Automatic (via deploy script)**

The deploy script handles this automatically:
```powershell
.\tools\deploy.ps1  # Hooks are deployed from hooks/
```

**Option B: Manual (if deploy script fails)**

```powershell
# Copy hooks to .claude directory
Copy-Item -Path "hooks\*" -Destination "$env:USERPROFILE\.claude\hooks\" -Recurse -Force

# Verify hooks were copied
Test-Path "$env:USERPROFILE\.claude\hooks\hook_wrapper.cmd"
```

**Option C: Absolute Paths (if `${CLAUDE_PROJECT_ROOT}` doesn't expand)**

If the env var isn't being resolved in hook commands, hard-code the absolute path instead:

> **Path Format Note:** JSON files always use forward slashes (`/`) even on Windows. PowerShell commands use backslashes (`\`). This is a common source of confusion.

```powershell
# Edit ~/.claude/settings.json and replace ${CLAUDE_PROJECT_ROOT} in hook commands with the absolute path.
# Hook configuration lives in settings.json under the "hooks" key.
# Example: update the command paths in each hook entry:
#   "command": "\"C:/src/obiwag-agents/hooks/hook_wrapper.cmd\" pre_tool_use"
```

### Verify Hooks Work

**Test hook execution:**
```powershell
# Test manually
python "$env:CLAUDE_PROJECT_ROOT\hooks\pre_tool_use.py"

# Should run without errors (may output nothing - that's OK)
```

**Check in Claude Code:**
After restarting Claude Code, hooks should appear in debug output. Look for hook feedback in the CLI.

### Enable Hooks (if disabled)

Hooks should be enabled by default. If they're not working:

```powershell
# Check current setting
claude config get hooks.enabled

# Enable if needed
claude config set hooks.enabled true
```

---

## Troubleshooting

### Windows: Script Won't Run

```
File cannot be loaded because running scripts is disabled on this system.
```

**Cause:** Windows PowerShell execution policy blocks unsigned local scripts.

**Fix:** Allow local scripts for your user, or invoke via PowerShell 7+:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# ...then re-run:
.\tools\deploy.ps1

# Alternatively, run without changing policy:
pwsh -File tools\deploy.ps1
```

### Health Check Fails: Module Not Found

```
ModuleNotFoundError: No module named 'yaml'
```

**Fix:**
```powershell
python -m pip install pyyaml
```

### Health Check Fails: Files Not Found

```
[FAIL] Claude commands directory not found
```

**Fix:** Ensure target directories exist:
```powershell
New-Item -ItemType Directory -Path "$env:USERPROFILE\.claude\commands" -Force
```

Then re-run deployment.

### Commands Not Working After Deployment

**Claude Code:**
1. Exit Claude Code completely (`Ctrl+C` or type `exit`)
2. Restart: `claude`
3. Try `/obi` again

**VS Code:**
1. Reload window: `Ctrl+Shift+P` → "Developer: Reload Window"
2. Or restart VS Code completely

### Drift Detected in Health Check

```
[WARN] author.md: DRIFT DETECTED
```

**Cause:** Deployed files differ from source (manual edits or stale deployment).

**Fix:**
```powershell
.\tools\deploy.ps1
python tools\healthcheck.py --quick
```

### Hooks Failing: CLAUDE_PROJECT_ROOT Not Set

**Error:**
```
python: can't open file 'C:\Users\<user>\${CLAUDE_PROJECT_ROOT}\...'
```
or
```
python: can't open file 'C:\Users\<user>\$HOME\.claude\hooks\...'
```

**Cause:** Environment variable not set or using wrong variable syntax.

**Fix Option 1: Set CLAUDE_PROJECT_ROOT (Recommended)**
```powershell
# Set permanently
[System.Environment]::SetEnvironmentVariable('CLAUDE_PROJECT_ROOT', '<full-path-to-obiwag-agents>', 'User')

# Restart Claude Code
```

**Fix Option 2: Use absolute paths in settings.json hooks**

Edit `~/.claude/settings.json` and update the hook command paths under the `"hooks"` key, replacing `${CLAUDE_PROJECT_ROOT}` with your actual path:

```json
{
  "command": "\"C:/src/obiwag-agents/hooks/hook_wrapper.cmd\" pre_tool_use"
}
```

**Important:** Use forward slashes (/) or escaped backslashes (\\\\) in JSON paths.

### Hooks Blocking All Commands

If hooks are preventing you from using Claude Code at all:

**Emergency fix:**
```powershell
# Disable hooks temporarily
claude config set hooks.enabled false

# Or temporarily remove hooks from settings.json
# Edit ~/.claude/settings.json and rename the "hooks" key to "hooks_disabled"

# Fix the configuration, then re-enable
claude config set hooks.enabled true
# And rename "hooks_disabled" back to "hooks" in settings.json
```

---

## Questions?

Open an issue on the project's repository.

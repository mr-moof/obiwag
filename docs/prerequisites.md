# Prerequisites

Single source of truth for tools and versions required to run Obi Wag. Referenced from both `README.md` (concepts + quick-start) and `DEPLOYMENT.md` (authoritative install guide).

If this document and another doc disagree, **this document wins**. Update it here first, then let the others link.

---

## Required Tools

| Software | Minimum Version | Purpose |
|----------|-----------------|---------|
| **Git** | 2.x+ | Clone the repository, version control |
| **Python** | 3.8+ | Runs hooks, health check, and helper tools. Needs `pyyaml`. |
| **PyYAML** | any | Parses `tools/version.yaml` (installed via `pip install pyyaml`) |
| **PowerShell** | 5.1+ (Windows), 7+ (`pwsh`) on Linux/Mac | Runs `tools/deploy.ps1` and verification scripts. Ships with Windows 10/11. |
| **Node.js** | 18+ (LTS) | Required to install Claude Code CLI |
| **Claude Code** | latest | AI coding assistant; installed via `npm install -g @anthropic-ai/claude-code` |
| **gh CLI** | latest | GitHub workflow commands (`/fixissue`, `/obi-swarm`). Required for any GitHub issue/PR operation. |

All version floors above are enforced by tooling (hooks, healthcheck, deploy). Older versions are not supported; upgrade if you hit version errors.

---

## Install Commands (Windows)

```powershell
# Git — install from https://git-scm.com/download/win

# Python 3.8+ from https://www.python.org/downloads/
#   - check "Add python.exe to PATH" during install
#   - disable Windows Store python.exe aliases:
#       Settings > Apps > Advanced app settings > App execution aliases
#       turn OFF python.exe AND python3.exe
python -m pip install pyyaml

# Node.js LTS (18+)
choco install nodejs-lts -y       # or download from https://nodejs.org/

# Claude Code
npm install -g @anthropic-ai/claude-code
claude                            # launch once to authenticate

# gh CLI
winget install GitHub.cli          # or: choco install gh -y
gh auth login                      # HTTPS (browser or PAT)
```

## Install Commands (Linux/Mac)

```bash
# Python 3.8+
python3 -m pip install pyyaml

# Node.js 18+ (use your package manager or nvm)
# Claude Code
npm install -g @anthropic-ai/claude-code

# gh CLI — see https://cli.github.com
gh auth login

# PowerShell Core (needed to run deploy.ps1 on non-Windows)
# See https://learn.microsoft.com/powershell/scripting/install/installing-powershell
```

---

## Verify Your Install

```powershell
git --version                 # git version 2.x+
python --version              # Python 3.8+
node --version                # v18+
claude --version              # any
gh --version                  # any
$PSVersionTable.PSVersion     # Major 5 (or 7+)
python -c "import yaml; print(yaml.__version__)"
```

All commands should exit 0 and print a version. If `python` opens the Microsoft Store instead, you still have the Store aliases enabled — disable them and re-try.

---

## OS / Environment Notes

### Windows

- Ships with PowerShell 5.1, which is sufficient for `tools/deploy.ps1` and hooks. PowerShell 7 (`pwsh`) is optional.
- Clone anywhere you like, e.g. `C:\src\obiwag-agents`.
- If `tools/deploy.ps1` is blocked by execution policy, allow signed/local scripts for your session: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`.

### Linux/Mac

- Use `pwsh` (PowerShell 7+) to run `tools/deploy.ps1`.
- Set `CLAUDE_PROJECT_ROOT` in `~/.bashrc` or `~/.zshrc`; see `DEPLOYMENT.md` > "Hooks Setup".

---

## Next Steps

Once every tool above verifies, go to `DEPLOYMENT.md` for the deploy flow, or `README.md` > "Quick Start" for the abbreviated path.

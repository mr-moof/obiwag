---
domain: overview
audience: user
status: current
related:
  - DEPLOYMENT.md
  - docs/prerequisites.md
  - docs/glossary.md
  - docs/hooks-architecture.md
---

# ObiWag AI Agent Framework

**v0.69.58**

**AI coding agents with a zero-hallucination, multi-phase workflow** — available for Claude Code and Codex.

Named after the user's dog Obi, who passed in 2025. She was a good girl.

---

## What is Obi Wag?

Obi Wag is an AI orchestration framework that enforces a strict multi-phase workflow for code quality and hallucination prevention. Instead of letting AI write code freely, Obi routes work through specialized agents with quality gates at each step.

**Key Features:**
- **Zero-hallucination policy** — Never invent APIs (vendor or technology)
- **10-phase workflow** — Discovery through Release Gate
- **Self-healing memory** — Learns from session corrections
- **Lane-first routing** — each change runs the phases its lane declares (trivial/express/standard/max)
- **Lifecycle-centric structure** — 10 numbered phases with commands and agents co-located

---

## Architecture Overview

**Architecture diagrams:** [Session & Infrastructure](docs/obi-session-and-infrastructure.svg) | [Workflow & Memory](docs/obi-workflow-and-memory.svg)

```
obiwag-agents/
├── CLAUDE.md                      # Entry point for Claude Code sessions
│
├── phases/                        # THE 10-PHASE LIFECYCLE
│   ├── README.md                  # Lifecycle overview, phase table (generated), flow
│   ├── phase-table.json           # Phase routing source of truth (rendered into the README table)
│   ├── 01-discovery/              # command.md + agent.md
│   ├── 02-author/                 # command.md
│   ├── ...                        # (each phase is a numbered directory)
│   └── 10-learning/               # command.md
│
├── orchestration/                 # HOW PHASES GET TRIGGERED
│   ├── obi.md                     # Manual mode dispatcher
│   ├── obi-auto.md                # Autonomous mode (Ralph loop)
│   └── utilities/                 # obi-collect, obi-memory-review, obi-update
│
├── policies/                      # QUALITY GATES
│   ├── zero-hallucination.md
│   ├── three-strike-rule.md
│   └── ...                        # express-lane, vendor-rules, etc.
│
├── hooks/                         # RUNTIME HOOKS (Python)
│   ├── hook_wrapper.cmd           # Graceful degradation wrapper
│   ├── session_start.py
│   ├── pre_tool_use.py
│   ├── post_tool_use.py
│   ├── stop.py
│   ├── core/                      # Shared hook modules
│   └── tests/                     # Hook unit tests
│
├── skills/                        # REUSABLE CAPABILITIES
│   ├── commit-conventions/
│   ├── platform-awareness/
│   ├── reviewing-code/             # Two-pass: Codex first (direct CLI), Claude synthesizes
│   ├── sql-safety/
│   ├── swarm-guardrails/
│   ├── vendor-integration/
│   └── verify/
│
│ # Codex (gpt-5.5) is invoked directly via codex.cmd per docs/policies/codex-usage.md.
│
├── docs/                          # REFERENCE DOCUMENTATION
│   ├── workflow/                  # Phase details, looping, resume
│   ├── domain-patterns/            # Vendor/technology API patterns (you populate)
│   └── ...                        # standards, glossary, FAQ, etc.
│
├── platforms/                     # MULTI-PLATFORM SUPPORT
│   └── codex/                    # Codex AGENTS.md + hooks
│
├── users/                         # PER-USER SETTINGS
│   └── <username>/settings.json
│
├── tools/                         # BUILD & DEPLOY TOOLING
│   ├── deploy.ps1                 # Deployment (cross-platform via pwsh)
│   ├── healthcheck.py             # Runtime health checker (structural checks in config-guardian.ps1)
│   ├── statusline-command.ps1     # Terminal status line with phase tracking
│   └── verify-setup.ps1           # Quick environment check
│
└── .obi/                          # Runtime memory state
```

---

## Prerequisites

See [`docs/prerequisites.md`](docs/prerequisites.md) for the authoritative list of required tools, versions, and OS-specific notes (Windows, macOS, Linux).

---

## Quick Start

Assumes prerequisites are already installed. For fresh installs or platform-specific notes, see [`DEPLOYMENT.md`](DEPLOYMENT.md).

```powershell
# 1. Clone (anywhere)
git clone https://github.com/<your-username>/obiwag-agents.git C:\src\obiwag-agents

# 2. Install the Python dep
python -m pip install pyyaml

# 3. Deploy
cd C:\src\obiwag-agents
.\tools\deploy.ps1

# 4. Verify
.\tools\verify-setup.ps1
python tools\healthcheck.py --quick
```

Both verifiers should report success. If verify-setup reports issues, follow its remediation output or see [`DEPLOYMENT.md`](DEPLOYMENT.md) > "Troubleshooting".

### When Rules Change

```powershell
# 1. Edit the policy or command directly
#    e.g., policies/zero-hallucination.md
#    e.g., phases/04-review/command.md

# 2. Deploy
.\tools\deploy.ps1

# 3. Verify changes deployed
python tools\healthcheck.py --quick
```

### Using Obi

After deployment:
- **Manual mode:** Type `/obi` to start the orchestrator
- **Autonomous mode:** Type `/obi-auto [describe your task]` for end-to-end automation
- **Swarm mode:** Type `/obi-swarm` to resolve multiple GitHub issues in parallel

---

## The 10-Phase Workflow

| Phase | Command | Purpose | Skip Condition |
|-------|---------|---------|----------------|
| 1. Discovery | `/discovery` | Research patterns, find reference code | - |
| 2. Author | `/author` | Write code + tests, document deliberate choices | - |
| 3. Simplify | `/simplify` | Post-author cleanup | - |
| 4. Review | `/review` | Verify author's claims against evidence | - |
| 5. Integrate | `/integrate` | Apply accepted feedback | - |
| 6. Re-review | `/re-review` | Verify integration | standard/max lanes only |
| 7. README | `/readme` | Document reality only | - |
| 8. README Review | `/readme-review` | Verify documentation | standard/max lanes only |
| 9. Release Gate | `/release` | Final verification + staging | - |
| 10. Learning | `/learning` | Capture session learnings | - |

**All phases require manual invocation** (or use `/obi-auto` for autonomous mode).

**Lanes (lane-first):** each change is classified into a lane that declares the phases it runs —
`trivial` (≤5 lines: Author + Release only), `express` (<25 lines: omits Re-review + README
Review), `standard` (all 10), `max` (`/obi-auto-max`: +Phase 0 + gates). Lane definitions live in
`phases/phase-table.json`; see `docs/policies/express-lane.md` + `docs/policies/trivial-change.md`.

---

## Operating Modes

| Mode | Command | How It Works |
|------|---------|--------------|
| **Manual** | `/obi` | You invoke each phase: `/author` -> `/review` -> `/integrate` |
| **Autonomous** | `/obi-auto` | Ralph loop handles everything automatically |
| **Swarm** | `/obi-swarm` | Fetches `ready` issues, spawns parallel workers in isolated worktrees, merges results |

---

## Available Commands

| Command | Purpose |
|---------|---------|
| `/discovery` | Research patterns before implementing |
| `/author` | Write code + tests |
| `/simplify` | Post-author cleanup |
| `/review` | Quality gate |
| `/integrate` | Apply review feedback |
| `/re-review` | Verify integration quality |
| `/readme` | Document reality |
| `/readme-review` | Verify documentation accuracy |
| `/release` | Final verification |
| `/learning` | Capture session learnings |
| `/fixissue` | Fix a GitHub issue end-to-end |
| `/triage` | Incident triage against available knowledge sources |
| `/obi` | Start manual orchestration |
| `/obi-auto [task]` | Start autonomous workflow |
| `/obi-auto-max [task]` | Invokes /obi-auto with rigor=max (Phase 0 prereq lock-in + 4 gates) |
| `/obi-update` | Check for and apply Obi updates |
| `/obi-memory-review` | Review calibration proposals |
| `/obi-swarm` | Parallel issue swarm resolution |
| `/obi-collect` | Collect and bundle audit findings |
| `/doc` | Co-author specs, design docs, RFCs, proposals |

---

## Core Policies

All policies are in `policies/` as Markdown files (deployed to `~/.claude/docs/policies/`).

### Zero-Hallucination Policy (`zero-hallucination.md`)

**Non-Negotiable:**
- Never invent APIs, endpoints, cmdlets, parameters, or return shapes — vendor or technology
- Use ONLY what is proven in this repo (existing wrappers, docs, contracts)
- If proof missing: output `MISSING SOURCE:` and ask for reference material

### Hard Stop Conditions (`hard-stop-conditions.md`)

Obi will immediately halt on these violations:
- Any invented/unsupported API usage (vendor or technology)
- Any external API usage not backed by repo evidence
- Nothing released without proper report-out

### Anti-Brute-Force Rule (`three-strike-rule.md`)

If the same issue fails **3 consecutive times**:
1. **STOP** — Do not attempt a 4th fix
2. **Diagnose** — Summarize what was tried and why it failed
3. **Seek input** — Ask for clarification or working examples
4. **Resume** only after new information is obtained

**Strike #2 Checkpoint:** After second consecutive failure, Obi pauses and asks before attempting strike #3.

### Vendor & Technology Rules (`vendor-rules.md`)

The framework ships with **no** vendor integrations. When you add one, never call a
vendor API or SDK directly from business logic — always go through a documented,
evidence-backed wrapper, and capture it under `docs/domain-patterns/<name>.md`.
(PowerShell projects: follow PSScriptAnalyzer OTBS rules.) See `docs/policies/vendor-rules.md`.

---

## Platform Comparison

| Feature | Claude Code | Codex |
|---------|-------------|-------|
| Source | `phases/`, `orchestration/`, `policies/` | `platforms/codex/` |
| Output format | `commands/*.md` | `AGENTS.md` + skills/hooks |
| Install location | `~/.claude/` | repo `AGENTS.md`, `.codex/hooks.json`, `~/.codex/skills/` |
| Hooks support | Yes | Yes |
| Memory system | Full | Platform-scoped under `~/.codex/.obi` |
| Autonomous mode | `/obi-auto` | Prompt-level `/obi-auto` contract |
| IDE integration | Terminal | Codex CLI/API |
| Best for | Multi-file authoring | Codex-native execution/review |

Both share the same core principles (zero-hallucination, 3-strike rule, workflow phases).

---

## Self-Healing Memory System

Obi learns from session corrections and adapts over time.

### How It Works

1. **Hooks observe** — SessionStart, PostToolUse, and Stop hooks track what happens
2. **Corrections are detected** — When you correct Obi, it's logged
3. **Patterns emerge** — After multiple similar corrections, an evolution is proposed
4. **Human gate** — You review and accept/reject calibration changes
5. **Obi adapts** — Accepted changes adjust future behavior

### Memory Commands

```
/obi-memory-review                  # List pending proposals
/obi-memory-review accept <id>      # Accept and apply
/obi-memory-review reject <id>      # Reject with reason
/obi-memory-review calibration      # Show current settings
```

---

## User-Specific Settings

Each user can maintain their own `settings.json` in the repo, allowing you to sync Claude Code configuration across your machines without affecting other users.

### How It Works

1. **Create your settings directory:**
   ```
   users/<your-username>/settings.json
   ```

2. **Deploy syncs automatically:**
   When you run `tools/deploy.ps1`, if a settings file exists for your username, it's copied to `~/.claude/settings.json`.

3. **Other users unaffected:**
   Users without a matching directory in `users/` simply don't get a settings.json deployed.

### What Goes in settings.json

| Setting | Purpose |
|---------|---------|
| `permissions.allow` | Auto-approved tool patterns (no prompts) |
| `hooks` | Hook configuration (SessionStart, PostToolUse, etc.) |
| `model` | Default model preference |
| `extraKnownMarketplaces` | Custom plugin sources |
| `statusLine` | Custom status line command |

### Syncing Across Machines

```powershell
# On machine A: Make changes, commit, push
cd C:\src\obiwag-agents
git add users/<username>/settings.json
git commit -m "Update my settings"
git push

# On machine B: Pull and deploy
cd C:\src\obiwag-agents
git pull
.\tools\deploy.ps1  # Your settings.json is deployed automatically
```

---

## Development Workflow

### Modifying a Policy

```powershell
# 1. Edit the policy directly
code policies/zero-hallucination.md

# 2. Deploy
.\tools\deploy.ps1
```

### Adding a New Phase Command

1. Create `phases/0N-name/command.md` following existing patterns
2. If the phase has a subagent, add `platforms/claude-code/agents/obi-<name>.md`
3. Update the required agent lists in `tools/deploy.ps1` validation
4. Run `.\tools\deploy.ps1` to deploy

### Testing Changes

```powershell
# Dry-run to see what would be deployed
.\tools\deploy.ps1 -DryRun

# Deploy when ready
.\tools\deploy.ps1

# Validate deployment
python tools\healthcheck.py --quick
```

---

## Documentation Structure

```
obiwag-agents/
├── README.md                    <- You are here (concepts + quick start)
├── CLAUDE.md                    <- Entry point for Claude Code sessions
├── DEPLOYMENT.md                <- Installation & setup
├── phases/README.md             <- Lifecycle overview with flow diagram
├── policies/                    <- Quality gates (deployed to docs/policies/)
├── docs/                        <- Reference docs (deployed to ~/.claude/docs/)
│   ├── workflow/                <- Phase details, looping, resume
│   ├── data-sources.md           <- Queryable data source catalog
│   ├── domain-patterns/          <- Vendor/technology API patterns
│   ├── hooks-architecture.md    <- Hooks & patterns flow
│   ├── memory-system.md         <- Self-healing memory
│   ├── glossary.md              <- Term definitions
│   └── faq.md                   <- Frequently asked questions
└── .obi/
    └── patterns/                <- Auto-injectable patterns
```

---

## Deployment Scripts

### PowerShell (Windows)

```powershell
.\tools\deploy.ps1                    # Deploy to all platforms
.\tools\deploy.ps1 -ClaudeOnly        # Claude Code only
.\tools\deploy.ps1 -CodexOnly         # Codex only
.\tools\deploy.ps1 -DryRun            # Preview changes
```

### Linux/Mac (via PowerShell Core)

```bash
pwsh -File tools/deploy.ps1                     # Deploy to all platforms
pwsh -File tools/deploy.ps1 -ClaudeOnly          # Claude Code only
pwsh -File tools/deploy.ps1 -DryRun              # Preview changes
```

---

## Questions?

Open an issue on the project's repository.

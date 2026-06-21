---
domain: hook-runtime
audience: maintainer
status: current
related:
  - hooks/core/README.md
  - docs/hooks-setup.md
  - docs/memory-system.md
---

# Hooks & Patterns Architecture

> **Last Updated:** 2026-06-21 | **Version:** 0.69.58

This document describes how Obi's hooks system works, including the pattern/skill injection that happens automatically based on task context.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           USER TYPES A PROMPT                                │
│                    "The build failed with a linter error"                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CLAUDE CODE TRIGGERS HOOKS                           │
│                                                                              │
│   ~/.claude/settings.json defines 6 hook points under the "hooks" key:       │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│   │ SessionStart │  │ PreToolUse   │  │ PostToolUse  │  │     Stop     │   │
│   │  (5s limit)  │  │  (3s limit)  │  │  (3s limit)  │  │  (10s limit) │   │
│   └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                          SessionStart runs first
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        PATTERN MATCHING ENGINE                               │
│                                                                              │
│   1. Load patterns from:                                                     │
│      • ~/source/obiwag-agents/.obi/patterns/*.md  (shared patterns)         │
│      • ~/.claude/.obi/memory/patterns/*.md        (user patterns)           │
│                                                                              │
│   2. Extract keywords from prompt:                                           │
│      "failed" → matches debugging pattern                                    │
│      "linter error" → matches debugging pattern                              │
│                                                                              │
│   3. Calculate confidence:                                                   │
│      base_confidence × (0.7 + 0.1 × match_count)                            │
│      0.85 × 0.9 = 0.765 → above 0.7 threshold ✓                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                            Confidence ≥ 0.7?
                                      │
                    ┌─────────────────┴─────────────────┐
                    │ YES                               │ NO
                    ▼                                   ▼
┌──────────────────────────────────┐    ┌──────────────────────────────────┐
│      INJECT PATTERN TEXT         │    │      NO INJECTION                │
│                                  │    │      (normal session start)      │
│  [Obi Memory] Detected: debugging│    │                                  │
│                                  │    │  [CODING_SESSION_START]          │
│  🔧 DEBUGGING MODE ACTIVATED     │    │  Obi Wag vX.XX is installed.    │
│                                  │    │  Run /obi to start orchestrator. │
│  Before attempting ANY fix...    │    │                                  │
│  Phase 1: Root Cause...          │    │                                  │
│  Phase 2: Pattern Analysis...    │    │                                  │
│  ...                             │    │                                  │
└──────────────────────────────────┘    └──────────────────────────────────┘
```

---

## Patterns (Auto-Injectable Skills)

Patterns are markdown files with YAML frontmatter that define:
- **Keywords** to match against user prompts
- **Confidence** threshold for the pattern
- **Injection text** to add to session context

### Pattern File Structure

```yaml
---
topic: debugging
confidence: 0.85
match_keywords:
  - error
  - failed
  - bug
  - broken
  - crash
  - debug
  - troubleshoot
---

# Debugging Pattern

## Injection Text

```
🔧 DEBUGGING MODE ACTIVATED
...methodology text here...
```
```

### Available Patterns

| Pattern | Keywords | Purpose |
|---------|----------|---------|
| `debugging.md` | error, failed, bug, broken, crash, debug... | 4-phase debugging methodology |
| `verification.md` | done, complete, fixed, ready, working now... | Verification checklist before completion |
| `planning.md` | plan, implement, build, create, design... | Bite-sized task planning |
| `powershell.md` | powershell, pwsh, cmdlet, module... | PowerShell best practices |
| `github.md` | github, pipeline, pr, pull request... | GitHub CI/CD patterns |

---

## Hook Execution Flow

### 1. SessionStart (5s timeout)

```
┌─────────────────────────────────────────────────────────────────┐
│                      session_start.py                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Load calibration from ~/.claude/.obi/calibration.md          │
│     └─ Check: auto_inject_sources = true?                        │
│                                                                  │
│  2. Load patterns from .obi/patterns/                            │
│     └─ Parse YAML frontmatter + injection text                   │
│                                                                  │
│  3. Match prompt keywords to patterns                            │
│     └─ Calculate confidence scores                               │
│                                                                  │
│  4. If match ≥ threshold (0.7):                                  │
│     └─ Inject pattern text into session context                  │
│                                                                  │
│  5. Display welcome message + recent sessions                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Daily-throttled maintenance (OPT-06 #179).** Drift hashing, the GC sweeps,
the MEMORY.md size check, and the drift baseline snapshot run at most once per
24h, gated by `~/.claude/.obi/state/last-maintenance.json`. The synchronous
path — welcome banner, version, grounding/correction injection, calibration
display, cached project listing — runs every start. Consequence: a newly
introduced drift warning can surface **at most one session late**, which is
acceptable for an advisory signal. Set `OBI_FORCE_MAINTENANCE=1` to bypass the
throttle (testing / healthcheck).

**Session-id sentinel (OPT-04 #177).** SessionStart writes
`~/.claude/.obi/state/current-session.json` (`{session_id, started_at, pid}`);
PostToolUse / PreCompact read it when their own input lacks a `session_id`, so a
session crossing an hour boundary stays pinned to one state file instead of
forking onto a new hour-bucket seed. Stop clears it.

### 2. PreToolUse (3s timeout)

```
┌─────────────────────────────────────────────────────────────────┐
│                       pre_tool_use.py                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Bash anti-pattern blocker:                                      │
│  - Blocks cd <path> && ... chains (use absolute paths)           │
│  - Blocks cat/head/tail (use Read tool)                          │
│  - Blocks grep/rg (use Grep tool)                                │
│  - Blocks find/ls for file search (use Glob tool)                │
│  - Blocks sed/awk for edits (use Edit tool)                      │
│                                                                  │
│  Returns blocking error message via hook system                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3. PostToolUse (3s timeout)

```
┌─────────────────────────────────────────────────────────────────┐
│                      post_tool_use.py                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Track tool usage to session state                            │
│     └─ Increment tool_count                                      │
│     └─ Append to tools_used list                                 │
│                                                                  │
│  Note: Does NOT detect corrections                               │
│        (no access to user messages)                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Heartbeat (OPT-22).** During an autonomous `/obi-auto` run (when `.obi/state/run-id.txt`
exists), PostToolUse also writes `.obi/state/heartbeat-<run_id>.json` — a liveness signal the
dispatch watchdog polls to detect a stalled subagent. Best-effort and a no-op outside autonomous
runs; the run-id is read once per process (OPT-03 no-redundant-read). The watchdog applies to the
`Agent` fallback dispatch path; OPT-23 headless workers set `OBI_WORKER=1` (their hooks no-op, no
heartbeat) and are bounded by the worker's own `-TimeoutSec` instead.

### 4. Stop (10s timeout)

```
┌─────────────────────────────────────────────────────────────────┐
│                          stop.py                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. Read transcript (if available)                               │
│                                                                  │
│  2. Analyze for corrections                                      │
│     └─ Split into human/assistant turns                          │
│     └─ Pattern match human turns for correction language         │
│     └─ "No, that's wrong", "Actually,", "Should be..."          │
│                                                                  │
│  3. Detect learnings (learning_detector.py)                      │
│     └─ Gotchas, vendor knowledge, platform workarounds           │
│                                                                  │
│  4. Write session summary to ~/.claude/.obi/memory/sessions/     │
│                                                                  │
│  5. Propose calibration evolutions if patterns emerge            │
│     └─ E.g., "increase verification budget for cloud"            │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 5. SubagentStop (5s timeout)

Fires when a dispatched subagent finishes (Discovery / Author / Learning under `/obi-auto`).
`subagent_stop.py` extracts the canonical phase-completion signal (e.g. `DISCOVERY COMPLETE`)
from the SubagentStop `last_assistant_message` input — falling back to the tail of
`agent_transcript_path` — and appends `{ts, agent_id, phase, signal, source}` to
`.obi/state/dispatch-state.json` under `completions[]`. The resume protocol reads this so a phase
whose subagent completed can be skipped even when the parent dispatch result was dropped (OPT-22).
Advisory only: emits `{}` on success or failure so subagent teardown is never blocked.

**OPT-23 interaction:** under headless dispatch (the new default for delegated phases), a phase runs
as a separate `claude -p` worker — not a Task-tool subagent — so the parent's SubagentStop does not
fire; the orchestrator reads the worker's JSON result directly. This hook now serves the `Agent`
fallback path and genuine Task subagents. Worker hooks self-suppress via `OBI_WORKER=1`
(`hooks/core/worker_guard.py`), so a worker never writes the parent's completion record or heartbeat.

(`PreCompact` is the 6th registered hook point; it is a thin pass-through and is omitted here.)

---

## State Locations

### Shared (Git Repo)

```
obiwag-agents/.obi/
├── patterns/           ← Pattern definitions (keywords + injection text)
│   ├── debugging.md
│   ├── verification.md
│   ├── planning.md
│   ├── powershell.md
│   └── github.md
└── calibration-template.md
```

### User-Specific (Home Directory)

```
~/.claude/.obi/
├── calibration.md      ← Active settings (thresholds, toggles)
├── memory/
│   ├── sessions/       ← Post-session summaries
│   ├── corrections/    ← Correction logs
│   ├── patterns/       ← User-specific pattern overrides (empty by default)
│   └── evolutions/     ← Accepted/rejected calibration changes
├── active-sessions/    ← Live session state (JSON)
└── hook-execution-log.jsonl  ← Hook timing/status log
```

---

## Confidence Calculation

The pattern matcher uses this formula:

```python
# For each pattern with at least 1 keyword match:
match_boost = min(1.0, 0.7 + (0.1 × matched_keywords))
confidence = base_confidence × match_boost

# Example: "The build failed with an error"
# Matches: "failed" + "error" (2 keywords)
# debugging pattern: base = 0.85
# match_boost = 0.7 + (0.1 × 2) = 0.9
# confidence = 0.85 × 0.9 = 0.765 ✓ (above 0.7 threshold)
```

| Matches | Boost | With 0.85 base |
|---------|-------|----------------|
| 1 | 0.80 | 0.68 |
| 2 | 0.90 | 0.77 ✓ |
| 3 | 1.00 | 0.85 ✓ |
| 4+ | 1.00 | 0.85 ✓ |

---

## Adding New Patterns

1. Create `.obi/patterns/your-pattern.md`:

```yaml
---
topic: your-topic
confidence: 0.80
match_keywords:
  - keyword1
  - keyword2
  - keyword3
---

# Your Pattern Name

## Injection Text

```
Your injection text here.
This will be added to the session context.
```
```

2. Deploy: `.\tools\deploy.ps1` or copy manually

3. Test: Start a Claude Code session with matching keywords

---

## Debugging Hooks

### Check Hook Execution Log

```powershell
Get-Content ~/.claude/.obi/hook-execution-log.jsonl | Select-Object -Last 10
```

### Test Pattern Matching

```python
import sys
sys.path.insert(0, 'hooks')
from core.pattern_matcher import match_task_to_patterns

matches = match_task_to_patterns('your test prompt here')
for pattern, confidence in matches:
    print(f"{pattern['topic']}: {confidence:.2f}")
```

### Verify Patterns Load

```python
from core.pattern_matcher import load_patterns
patterns = load_patterns()
for p in patterns:
    print(f"{p['topic']}: {len(p.get('match_keywords', []))} keywords")
```

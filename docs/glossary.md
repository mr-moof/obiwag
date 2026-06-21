---
domain: reference
audience: user
status: current
related:
  - README.md
  - docs/workflow/phases.md
  - docs/hooks-architecture.md
---

# Obi Wag Glossary

Quick reference for terms used throughout Obi Wag documentation.

---

## Core Concepts

### Phase
One of the 10 sequential stages in the Obi workflow. Phases are: Discovery, Author, Simplify, Review, Integrate, Re-review, README, README Review, Release Gate, and Learning. Each phase has a corresponding command.

### Command
A slash command (e.g., `/author`, `/review`) that invokes a specific phase. Commands are markdown files in `~/.claude/commands/`. All phases require manual command invocation unless using autonomous mode.

### Agent
The AI persona that executes a phase. When you invoke `/author`, Claude acts as the "author agent" with specific instructions and constraints.

### Skill
A reusable capability invoked via the Skill tool. Commands are implemented as skills in Claude Code. When you type `/author`, Claude Code loads the skill from `author.md`.

---

## Workflow Terms

### Ralph Loop
The autonomous iteration engine that powers `/obi-auto`. Named after the feedback loop pattern, Ralph advances through phases based on completion signals, handles break signals, and maintains state in `.obi/ralph-state.json`.

### Completion Signal
A text string that indicates a phase completed successfully. Examples: `DISCOVERY COMPLETE`, `AUTHOR COMPLETE`, `RELEASE GATE PASSED`. The Ralph loop watches for these to advance to the next phase.

### Break Signal
A text string that halts workflow execution. Examples:
- `NEEDS USER INPUT` - Missing information, ask user
- `HARD STOP: [reason]` - Policy violation, immediate halt
- `3-STRIKE LIMIT` - Repeated failure, stop and diagnose

### Express Lane
A workflow optimization for small changes (<25 lines of code). Express Lane changes skip Re-review (phase 6) and README Review (phase 8) since they carry lower risk.

---

## Quality Terms

### Hard Stop
An immediate workflow termination triggered by policy violations. Hard stops occur for: hallucinated APIs, missing repo evidence, bypassed wrapper boundaries, or missing report-out.

### Strike
One failed attempt at fixing an issue. The Three-Strike Rule limits consecutive failures to prevent brute-force debugging. After Strike #2, Obi pauses to ask for input before attempting Strike #3.

### Zero-Hallucination
The core policy that prohibits inventing APIs, endpoints, parameters, or return shapes — whether vendor-specific or technology-specific. All API usage must be backed by evidence in the repository (existing wrappers, references, docs, or tests).

---

## Memory System Terms

### Pattern
A grounding source file in `.obi/patterns/` that defines known-good information for a topic (e.g., `debugging.md`, `powershell.md`). Patterns are injected at session start based on keyword matching.

### Grounding
The practice of basing responses on verified evidence rather than general knowledge. The memory system injects grounding sources to prevent hallucination.

### Calibration
Configuration settings that control memory system behavior. Stored in `~/.claude/.obi/calibration.md`. Includes verification budgets, safety flags, and pattern matching thresholds.

### Evolution
A proposed change to calibration based on observed corrections. Evolutions are proposed (not applied automatically) and require human acceptance via `/obi-memory-review accept <id>`.

---

## Infrastructure Terms

### Hook
A script that runs at specific points in a Claude Code session. Hook types: SessionStart, PreToolUse, PostToolUse, Stop. Hooks power the memory system but are optional.

### Wrapper
A module that encapsulates vendor SDK calls (e.g., a `CloudClient` wrapper module). Wrappers provide a stable interface and are the only approved way to interact with vendor APIs.

### Repo Evidence
Proof that an API, endpoint, or pattern exists and works. Evidence includes: existing wrappers, `docs/domain-patterns/` docs, `docs/` reference docs, test files, or working usage examples. Required before using any API.

---

## File Locations

| Term | Location |
|------|----------|
| Commands | `~/.claude/commands/` |
| Hooks | `~/.claude/hooks/` |
| State files | `.obi/` (per-project) |
| Memory state | `~/.claude/.obi/` (per-user) |
| Patterns | `obiwag-agents/.obi/patterns/` |
| Calibration | `~/.claude/.obi/calibration.md` |

---

## See Also

- [Workflow Phases](../workflow/phases.md) - Full phase documentation
- [Memory System](memory-system.md) - Memory architecture details
- [FAQ](../faq.md) - Common questions and answers

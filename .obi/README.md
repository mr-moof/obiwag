# Obi Memory System

Self-healing memory for the Obi orchestrator. Learns from corrections, injects grounding sources, proposes calibration changes.

## Core Principles

1. **State lives in markdown** - Git-trackable, Claude-readable, self-documenting
2. **Hooks as observers** - Read state and enforce, don't create state
3. **Evolution is proposed, not applied** - Human gate on all changes
4. **Grounding is learned** - Track source citations during corrections
5. **Failure is survivable** - Obi down = Claude Code still works

## Directory Structure

### In This Repo (Shared Knowledge)

```
.obi/
├── patterns/                 # Grounding sources by topic
│   ├── debugging.md          # Debugging patterns
│   ├── github.md             # GitHub operations patterns
│   ├── planning.md           # Planning patterns
│   ├── powershell.md         # PowerShell patterns
│   └── verification.md       # Verification patterns
├── calibration-template.md   # Default calibration schema
└── README.md                 # This file
```

> **Note:** Discovery reports (`discovery-report*.md`) and session logs (`session-*.md`) are ephemeral session artifacts. They are `.gitignore`d and must not be committed.

### In User Home (Runtime State)

```
~/.claude/.obi/
├── memory/
│   ├── sessions/             # Post-session summaries
│   ├── patterns/             # User-specific pattern overrides
│   ├── corrections/          # Daily correction logs (JSONL)
│   └── evolutions/
│       ├── applied/          # Accepted proposals
│       └── rejected/         # Rejected proposals
├── pending/                  # Evolution proposals awaiting review
├── calibration.md            # Active calibration settings
└── grounding-log.jsonl       # Source citation history
```

## Hook Integration

Hooks are wired in `~/.claude/settings.json` and call Python scripts in `hooks/`:

| Hook | Script | Purpose |
|------|--------|---------|
| SessionStart | `session_start.py` | Inject grounding sources |
| PostToolUse | `post_tool_use.py` | Log corrections |
| Stop | `stop.py` | Generate session summary |

## Pattern Files

Pattern files define grounding sources for task types.
Each pattern also includes a `## Review Guidance` section with domain-specific failure modes for the reviewer to check.

**Relationship to `docs/domain-patterns/`:** `.obi/patterns/*.md` are lightweight,
YAML-fronted snippets the SessionStart hook auto-injects when keywords match.
`docs/domain-patterns/*.md` are the richer vendor/technology references those
snippets point to via their `sources` field. Keep the two aligned: if a vendor
gets a new `docs/domain-patterns/` doc, mirror the keyword trigger in a
`.obi/patterns/` snippet so the hook can surface it.

Format:

```yaml
---
topic: cloud
confidence: 0.85
match_keywords:
  - aws
  - azure
  - cloud provider
sources:
  - path: docs/domain-patterns/cloud.md
    priority: 1
---

# Injection Text
[Text injected into session context when pattern matches]
```

## Calibration

The `calibration.md` file controls memory system behavior:

```yaml
verification:
  default_budget: 1          # Solve-verify cycles before human gate
  budgets_by_type:
    cloud: 2                 # Per-task-type budgets

safety:
  auto_inject_sources: true  # Inject patterns at session start
  log_corrections: true      # Log user corrections
  generate_summaries: true   # Create session summaries
  propose_evolutions: true   # Generate pending proposals
```

## Evolution Workflow

1. **Session runs** - Hooks log corrections and outcomes
2. **Session ends** - Stop hook analyzes, may propose evolution
3. **Review** - Run `/obi-memory-review` to see proposals
4. **Decide** - Accept or reject each proposal
5. **One change per cycle** - Prevents calibration drift

## Commands

```
/obi-memory-review                  # List pending proposals
/obi-memory-review accept <id>      # Accept and apply
/obi-memory-review reject <id>      # Reject with reason
/obi-memory-review calibration      # Show current settings
/obi-memory-review sessions         # Show session history
```

## Adding New Patterns

1. Create file: `.obi/patterns/<topic>.md`
2. Add YAML frontmatter with `topic`, `match_keywords`, `sources`
3. Add injection text in markdown body
4. Pattern auto-loads on next session

## Troubleshooting

### Hooks not running
- Check `~/.claude/settings.json` has correct hook configuration
- Verify Python 3 is available: `python --version`
- Check hook scripts exist in `hooks/`

### Pattern not matching
- Verify keywords in `match_keywords` appear in task text
- Check `confidence` threshold in calibration
- Run `/obi-memory-review calibration` to see settings

### Evolution not proposed
- Check `propose_evolutions: true` in calibration
- Need 2+ corrections in session to trigger analysis
- Need pattern in recent session history

## Safety

- **No auto-evolution**: All changes require human acceptance
- **One change per cycle**: Prevents drift
- **Prior state preserved**: Can rollback any change
- **Graceful degradation**: Hook failure doesn't block Claude Code

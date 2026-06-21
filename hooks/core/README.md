---
domain: hook-runtime
audience: maintainer
status: current
related:
  - ../../docs/hooks-architecture.md
  - ../../docs/hooks-setup.md
  - ../stop.py
  - ../pre_tool_use.py
---

# Obi Core Modules

Core Python modules for the Obi Wag memory and workflow system.

## Modules

### strike_counter.py
Implements Strike #2 checkpoint enforcement to prevent wasted debugging iterations.

**Classes:**
- `StrikeCounter` - Tracks consecutive failures with persistent state

**Key Features:**
- Tracks failure patterns across attempts
- Triggers checkpoint at Strike #2
- Generates user-friendly checkpoint messages
- Resets counter when new approach is taken

**Usage:**
```python
from core.strike_counter import check_strike_status

result = check_strike_status(
    'linting:config_not_found',
    {'error_message': 'Config not found', 'attempt_description': 'Tried root/.psscriptanalyzer.psd1'}
)

if result['should_checkpoint']:
    print(result['checkpoint_message'])
    # Ask user for input before Strike #3
```

## Additional Core Modules

Other modules in this directory:

- **calibration.py** - Loads calibration settings from `~/.claude/.obi/calibration.md`
- **correction_retriever.py** - RAG-style correction injection based on past session corrections
- **git_sync.py** - Git operations for syncing learnings to source repo
- **hook_logger.py** - Timing and logging for hook execution
- **learning_detector.py** - Detects learnable content from session transcripts
- **memory_reader.py** - Read/write session summaries and memory files
- **pattern_matcher.py** - Task type detection and grounding source injection
- **drift_detector.py** - Drift detection between deployed and source repo files
- **session_state.py** - Per-session state management
- **version.py** - Version info and update checking

## Dependencies

- Python 3.10+ required (code uses PEP 604 `X | Y` union syntax)
- No external packages (stdlib only)

## File Locations

- **Strike state:** `.obi/strike-state.json` (relative to hook CWD)
- **Calibration:** `~/.claude/.obi/calibration.md`
- **Session data:** `~/.claude/.obi/memory/sessions/`

## Error Handling

All modules gracefully handle missing files and return defaults rather than raising exceptions.
Hook failures are non-blocking — they never prevent Claude Code from operating.

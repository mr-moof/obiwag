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

This directory holds ~39 modules; an inline list here goes stale the moment one is
added or removed (it documented the deleted `git_sync.py` for two releases). Use the
generated/authoritative sources instead:

- **Architecture and per-hook call graph:** [`docs/hooks-architecture.md`](../../docs/hooks-architecture.md)
- **Which detectors run in which hook, in order:** [`detector_registry.py`](detector_registry.py)
- **What gets deployed:** the `core` list in [`tools/lib/hook-manifest.json`](../../tools/lib/hook-manifest.json)
- **Everything on disk:** `ls hooks/core/*.py`

Removal notes worth keeping:


- *(removed 0.69.88)* `git_sync.py` — learning-sync git operations. Reachable only through the lazy re-export map in this package's `__init__.py`; no caller anywhere in the repo.
- *(removed 0.69.88)* `stop_advisories.py` — its detectors moved to `detector_registry.py` in OPT-15, leaving `run_shutdown_advisories` returning `None` unconditionally while still costing the Stop hook a deadline check, an import, and a timer.

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

"""
Shared constants and utilities for Obi Wag health check tools.

Used by:
  - tools/healthcheck.py (full deployment validator)

Keep model version constants here as the single source of truth.
"""

import os
import sys

# ANSI colors (Windows 10+ supports these)
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'
BOLD = '\033[1m'


def color(text: str, color_code: str) -> str:
    """Apply ANSI color if terminal supports it."""
    if sys.platform == 'win32':
        os.system('')
    return f"{color_code}{text}{RESET}"


# Valid model identifiers — agents/settings should use an always-latest ALIAS
# (fable, opus, sonnet) or a full provider ID. Aliases are preferred: the model
# then tracks the latest generation automatically (policy: always latest of a
# type). Frontier phases (Discovery, Author) and the Claude peer use `fable`
# (the user, 2026-09-01); the balanced phases use `sonnet`. 'haiku'/'claude-haiku-'
# are deliberately EXCLUDED — never use haiku; migrate such agents to sonnet. Not
# currently imported by any checker (kept as the documented allowlist).
VALID_MODEL_PREFIXES = [
    'fable',
    'opus',
    'sonnet',
    'claude-fable-',
    'claude-opus-',
    'claude-sonnet-',
    'gemini-',
    'gpt-',
]

# Aliases always resolve to the latest generation, so there is no freshness
# floor to enforce. Left empty intentionally; populate only if pinned full-ID
# models are reintroduced and need a minimum-version nag.
MINIMUM_MODEL_VERSIONS = {}

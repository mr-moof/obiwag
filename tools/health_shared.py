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


# Valid model prefixes — agents must use one of these
VALID_MODEL_PREFIXES = [
    'claude-opus-4',
    'claude-sonnet-4',
    'gemini-',
    'gpt-',
]

# Minimum model versions per provider (used for freshness check).
# Update these when new model generations ship.
MINIMUM_MODEL_VERSIONS = {
    'claude-opus': 'claude-opus-4.6',
    'claude-sonnet': 'claude-sonnet-4.6',
}

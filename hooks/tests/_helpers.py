"""Shared test helpers for the hooks unit-test suite.

Keep stateless scaffolding here (stubs, factories) so individual test
modules stay focused on the behavior they cover. Pytest fixtures that
need dependency injection should live in ``conftest.py`` instead.
"""

import sys
from pathlib import Path


# Ensure hooks/ is on sys.path so this module can be imported alongside the
# hook entry points (each test module already does the same insert).
HOOKS_DIR = Path(__file__).parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))


class StubSessionState:
    """Minimal dict-backed SessionState stand-in for unit tests.

    Mirrors the read/write surface of ``core.session_state.SessionState``
    that the hook entry points exercise:

    - ``get(key, default)`` — dict-style read
    - ``set(key, value)`` — dict-style write
    - ``snapshot()``       — copy of the current state

    Extracted from duplicate definitions in test_compact_nag.py and
    test_pre_compact.py (#151).
    """

    def __init__(self, data: dict):
        self._data = dict(data)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def snapshot(self) -> dict:
        return dict(self._data)

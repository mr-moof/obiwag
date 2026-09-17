"""Tests for core.version lazy version resolution (issue #175, OPT-02).

Importing ``core.version`` must perform ZERO filesystem I/O. Version
resolution moved from an import-time ``CURRENT_VERSION = _load_current_version()``
constant to a lazily-evaluated, ``functools.lru_cache``-backed
``get_current_version()``. A deprecated module-level ``CURRENT_VERSION``
shim is retained for one release via PEP 562 ``__getattr__``.
"""

import builtins
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

import core.version as version_module


def test_import_performs_no_filesystem_io(monkeypatch):
    """Reloading core.version with FS access poisoned must not raise.

    If any filesystem call happened at import time, the poisoned
    ``builtins.open`` / ``Path.is_file`` would raise and fail the reload.
    """
    def _no_open(*args, **kwargs):
        raise AssertionError("import-time builtins.open() is forbidden (#175)")

    def _no_is_file(*args, **kwargs):
        raise AssertionError("import-time Path.is_file() is forbidden (#175)")

    monkeypatch.setattr(builtins, "open", _no_open)
    monkeypatch.setattr(Path, "is_file", _no_is_file)

    # Fresh import under the poisoned FS surface. No raise => no FS I/O.
    reloaded = importlib.reload(version_module)
    assert reloaded is not None


def test_get_current_version_returns_string():
    """get_current_version resolves to a non-empty string."""
    version = version_module.get_current_version()
    assert isinstance(version, str)
    assert version


def test_get_current_version_is_cached():
    """Repeated calls return the same cached object (lru_cache)."""
    first = version_module.get_current_version()
    second = version_module.get_current_version()
    assert first == second
    assert version_module.get_current_version.cache_info().hits >= 1


def test_current_version_shim_resolves_lazily():
    """The deprecated CURRENT_VERSION attribute resolves via PEP 562 __getattr__."""
    assert version_module.CURRENT_VERSION == version_module.get_current_version()


def test_current_version_importable_directly():
    """`from core.version import CURRENT_VERSION` triggers module __getattr__."""
    from core.version import CURRENT_VERSION
    assert CURRENT_VERSION == version_module.get_current_version()


def test_current_version_reexported_from_core_package():
    """`from core import CURRENT_VERSION` still resolves through the shim."""
    from core import CURRENT_VERSION
    assert CURRENT_VERSION == version_module.get_current_version()


def test_bogus_attribute_raises_attribute_error():
    """__getattr__ only intercepts CURRENT_VERSION; others raise AttributeError."""
    with pytest.raises(AttributeError):
        _ = version_module.NONEXISTENT_ATTR


def test_no_module_level_current_version_constant():
    """The eager module-level constant must be gone (only the shim remains)."""
    assert "CURRENT_VERSION" not in version_module.__dict__


def test_check_for_updates_uses_resolved_remote_default(monkeypatch, tmp_path):
    """Fetch, compare, and log all target the resolved remote default branch."""
    calls = []
    responses = iter([
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="1\n", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="abc123 fix\n", stderr=""),
    ])

    def fake_run(args, **kwargs):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(version_module, "get_obi_source_repo", lambda: tmp_path)
    monkeypatch.setattr(version_module, "get_remote_default_branch", lambda _repo: "main")
    monkeypatch.setattr(version_module.subprocess, "run", fake_run)

    has_updates, _, commits = version_module.check_for_updates()

    assert has_updates is True
    assert commits == ["abc123 fix"]
    assert calls[0] == ["git", "fetch", "origin", "main"]
    assert calls[1] == ["git", "rev-list", "--count", "HEAD..origin/main"]
    assert calls[2] == ["git", "log", "--oneline", "HEAD..origin/main"]


def test_apply_updates_pulls_resolved_remote_default(monkeypatch, tmp_path):
    """The updater pulls the remote default branch before deployment."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(version_module, "get_obi_source_repo", lambda: tmp_path)
    monkeypatch.setattr(version_module, "get_remote_default_branch", lambda _repo: "main")
    monkeypatch.setattr(version_module.subprocess, "run", fake_run)

    success, _ = version_module.apply_updates()

    assert success is True
    assert calls[0] == ["git", "pull", "origin", "main"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

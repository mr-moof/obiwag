"""Tests for lazy ``core`` package initialization (issue #174 / OPT-01).

Importing the ``core`` package (and, specifically, a lightweight submodule like
``core.paths``) must NOT transitively import heavy dependencies such as PyYAML
or ``core.learning_detector``. Re-exported names are resolved lazily via a
PEP 562 module-level ``__getattr__``, so the eager import cost that every hook
invocation used to pay is gone.
"""

import subprocess
import sys

import pytest


# Names that must remain absent from sys.modules after a fresh ``import core.paths``.
_HEAVY_MODULES = ('yaml', 'core.learning_detector')


def _fresh_import_command() -> str:
    """A standalone snippet that asserts the acceptance criterion in a clean interpreter."""
    return (
        "import sys; import core.paths; "
        "assert 'yaml' not in sys.modules, 'yaml leaked'; "
        "assert 'core.learning_detector' not in sys.modules, 'learning_detector leaked'; "
        "print('LAZY OK')"
    )


def test_importing_core_paths_does_not_pull_heavy_deps():
    """Acceptance criterion, run in a fresh subprocess to avoid sys.modules pollution.

    Running in-process is unreliable because other tests (or pytest plugins)
    may already have imported ``yaml`` / ``core.learning_detector``. A fresh
    interpreter with PYTHONPATH set to the hooks dir is the authoritative check.
    """
    hooks_dir = str(__import__('pathlib').Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, '-c', _fresh_import_command()],
        cwd=hooks_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"lazy-import check failed (rc={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert 'LAZY OK' in result.stdout, result.stdout


def test_core_init_has_no_eager_submodule_imports():
    """The package module itself must not have eagerly bound re-exported submodules.

    After importing ``core`` (without touching any re-export), the heavy
    submodules should not be present in ``sys.modules`` solely due to the
    package import. We import in a subprocess so the assertion is not defeated
    by modules already loaded in the test session.
    """
    hooks_dir = str(__import__('pathlib').Path(__file__).resolve().parents[1])
    snippet = (
        "import sys; import core; "
        "leaked = [m for m in ('core.version', 'core.learning_detector', "
        "'core.memory_reader', 'core.calibration', 'yaml') if m in sys.modules]; "
        "assert not leaked, f'eager imports leaked: {leaked}'; "
        "print('NO EAGER OK')"
    )
    result = subprocess.run(
        [sys.executable, '-c', snippet],
        cwd=hooks_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"eager-import check failed (rc={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert 'NO EAGER OK' in result.stdout, result.stdout


def test_reexports_still_resolve():
    """Representative re-exported names must still resolve via __getattr__."""
    import core

    from core import load_calibration, get_obiwag_repo_path, CURRENT_VERSION

    assert callable(load_calibration)
    assert callable(get_obiwag_repo_path)
    # CURRENT_VERSION resolves through .version's own lazy shim (OPT-02).
    assert isinstance(CURRENT_VERSION, str)
    # getattr access path also works.
    assert core.get_version_info is not None


def test_all_reexports_resolve():
    """Every name in ``core.__all__`` must resolve via the lazy machinery."""
    import core

    for name in core.__all__:
        assert hasattr(core, name), f"re-export {name!r} failed to resolve"


def test_unknown_attribute_raises():
    """Unknown attributes raise AttributeError (normal lookup semantics)."""
    import core

    with pytest.raises(AttributeError):
        _ = core.NONEXISTENT_ATTRIBUTE_174


def test_all_matches_attr_sources():
    """``__all__`` and the lazy source map must stay in sync."""
    import core

    assert set(core.__all__) == set(core._ATTR_SOURCES), (
        "core.__all__ and core._ATTR_SOURCES diverged"
    )

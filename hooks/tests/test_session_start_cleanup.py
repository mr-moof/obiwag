"""Regression tests for safe SessionStart maintenance behavior."""

import json
import os
import sys
import time
from pathlib import Path


HOOKS_DIR = Path(__file__).parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from session_start import _cleanup_local_settings_regrowth, _gc_old_files  # noqa: E402


def test_project_local_settings_are_never_deleted(tmp_path: Path) -> None:
    """Permission arrays merge across scopes; local files are intentional data."""
    local_file = tmp_path / "project" / ".claude" / "settings.local.json"
    local_file.parent.mkdir(parents=True)
    original = {
        "permissions": {"allow": ["Bash(project-only:*)"]},
        "hooks": {"Stop": [{"hooks": []}]},
    }
    local_file.write_text(json.dumps(original), encoding="utf-8")
    welcome: list[str] = []

    _cleanup_local_settings_regrowth(welcome)

    assert json.loads(local_file.read_text(encoding="utf-8")) == original
    assert welcome == []


def test_cleanup_compatibility_entrypoint_is_a_pure_noop(tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))
    welcome = ["existing"]
    _cleanup_local_settings_regrowth(welcome)
    assert sorted(tmp_path.rglob("*")) == before
    assert welcome == ["existing"]


def test_gc_prunes_files_older_than_cutoff(tmp_path: Path) -> None:
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_text("stale", encoding="utf-8")
    new.write_text("fresh", encoding="utf-8")
    os.utime(str(old), (1, time.time() - 60 * 86400))

    removed = _gc_old_files(str(tmp_path), ".md", max_age_days=30)

    assert removed == 1
    assert not old.exists()
    assert new.exists()


def test_gc_respects_suffix_filter(tmp_path: Path) -> None:
    ignored = tmp_path / "old.json"
    ignored.write_text("ignored", encoding="utf-8")
    os.utime(str(ignored), (1, time.time() - 60 * 86400))
    assert _gc_old_files(str(tmp_path), ".md", max_age_days=30) == 0
    assert ignored.exists()


def test_gc_missing_directory_returns_zero(tmp_path: Path) -> None:
    assert _gc_old_files(str(tmp_path / "missing"), ".md", max_age_days=30) == 0


def test_gc_deadline_stops_bounded_scan(tmp_path: Path) -> None:
    stale = tmp_path / "stale.md"
    stale.write_text("stale", encoding="utf-8")
    os.utime(str(stale), (1, time.time() - 60 * 86400))
    assert _gc_old_files(
        str(tmp_path),
        ".md",
        max_age_days=30,
        deadline_monotonic=time.monotonic() - 1,
    ) == 0
    assert stale.exists()

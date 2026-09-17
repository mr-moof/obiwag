#!/usr/bin/env python3
"""SessionStart hook for Obi Memory System.

This hook runs at the start of each Claude Code session to:
1. Output the Obi welcome message
2. Detect task type from user prompt
3. Inject grounding sources based on pattern matching
4. Read calibration settings for the session
"""

import os
import sys
import json
import time
from datetime import datetime

# Add hook root to path for imports
HOOK_ROOT = os.path.dirname(os.path.abspath(__file__))
if HOOK_ROOT not in sys.path:
    sys.path.insert(0, HOOK_ROOT)

from core.paths import get_obi_root
from core.hook_logger import log_swallowed


# Maintenance config — see _gc_maintenance / _check_memory_md_size
GC_RETENTION_DAYS = 30
MEMORY_MD_WARN_LINES = 150
MEMORY_MD_HARD_CAP = 200
# Claude currently allows five seconds for SessionStart.  Finish synchronous
# work by three seconds so cold Python startup and output handling have room.
SESSION_START_SOFT_BUDGET_SECONDS = 3.0


def _resolve_project_working_dir():
    """Find the explicitly configured project root or the user's source folder."""
    candidates = [
        os.environ.get("OBI_PROJECTS_ROOT", ""),
        os.path.join(os.path.expanduser("~"), "source"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate):
            return candidate
    return None


def _scan_projects_cached(working_dir, deadline_monotonic=None):
    """Return sorted subdirs of working_dir using an mtime-invalidated cache.

    Caches to ~/.claude/.obi/project-cache.json keyed by working_dir.
    Cache hit: skip os.scandir entirely (avoids per-session stat-storm
    on network-profile remote desktops).  Cache miss: rescan and rewrite.
    """
    cache_path = str(get_obi_root() / ".obi" / "project-cache.json")

    try:
        current_mtime = os.path.getmtime(working_dir)
    except OSError:
        return []

    cache = {}
    try:
        if os.path.isfile(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        cache = {}

    entry = cache.get(working_dir) if isinstance(cache, dict) else None
    if isinstance(entry, dict) and entry.get("mtime") == current_mtime:
        projects = entry.get("projects")
        if isinstance(projects, list):
            return projects

    projects = []
    try:
        with os.scandir(working_dir) as entries:
            for entry in entries:
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    return []
                if entry.is_dir() and not entry.name.startswith("."):
                    projects.append(entry.name)
    except OSError:
        return []
    projects.sort()

    cache[working_dir] = {"mtime": current_mtime, "projects": projects}
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except OSError:
        pass  # Cache write failure must not block session start.

    return projects


def _gc_old_files(directory, suffix, max_age_days, deadline_monotonic=None):
    """Prune files in `directory` ending in `suffix` older than `max_age_days`.

    Returns the count of files removed. Best-effort — never blocks startup.
    Both ~/.claude/.obi/memory/sessions/ and ~/.claude/todos/ grow once-per-
    session-or-agent without bound; this keeps disk usage stable.
    """
    if not os.path.isdir(directory):
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        for entry in os.scandir(directory):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                break
            if not entry.is_file() or not entry.name.endswith(suffix):
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed


def _gc_maintenance(welcome_parts, deadline_monotonic=None):
    """Best-effort GC of unbounded-growth dirs at session start."""
    try:
        sessions_pruned = _gc_old_files(
            str(get_obi_root() / ".obi" / "memory" / "sessions"),
            ".md",
            GC_RETENTION_DAYS,
            deadline_monotonic,
        )
        todos_pruned = _gc_old_files(
            str(get_obi_root() / "todos"),
            ".json",
            GC_RETENTION_DAYS,
            deadline_monotonic,
        )
        if sessions_pruned or todos_pruned:
            parts = []
            if sessions_pruned:
                parts.append(f"{sessions_pruned} session summary file(s)")
            if todos_pruned:
                parts.append(f"{todos_pruned} agent todo file(s)")
            welcome_parts.append(
                "\n[Maintenance] GC'd "
                + ", ".join(parts)
                + f" older than {GC_RETENTION_DAYS} days."
            )
    except Exception as exc:
        log_swallowed("gc_maintenance", exc)


def _check_memory_md_size(welcome_parts, deadline_monotonic=None):
    """Warn if MEMORY.md is approaching the 200-line CLAUDE.md load truncation cap.

    CLAUDE.md states only the first 200 lines of MEMORY.md are loaded into
    context. Once we cross MEMORY_MD_WARN_LINES, the user has roughly one
    quarter of the headroom left and should triage older entries via
    /obi-memory-review before older learnings start getting silently dropped.
    """
    try:
        from core.project_memory import get_project_memory_dir
        memory_dir = get_project_memory_dir()
        if not memory_dir:
            return
        memory_path = os.path.join(memory_dir, "MEMORY.md")
        if not os.path.isfile(memory_path):
            return
        with open(memory_path, "r", encoding="utf-8") as f:
            line_count = 0
            for _ in f:
                line_count += 1
                if line_count > MEMORY_MD_HARD_CAP:
                    break
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    return
        if line_count >= MEMORY_MD_WARN_LINES:
            welcome_parts.append(
                f"\n[Maintenance] MEMORY.md is at {line_count}/{MEMORY_MD_HARD_CAP} lines "
                f"— archive entries older than 90 days via /obi-memory-review before the "
                f"load-truncation cap silently drops older learnings."
            )
    except Exception as exc:
        log_swallowed("check_memory_md_size", exc)


def _cleanup_local_settings_regrowth(welcome_parts):
    """Compatibility no-op; permission arrays merge across Claude scopes.

    Older versions deleted a project file whenever its allow-list was shorter
    than the user list.  That premise is false and could destroy intentional
    local configuration.  Keep the callable for old tests/imports, but never
    mutate settings here; healthcheck owns non-destructive diagnostics.
    """
    del welcome_parts


def _maintenance_marker_path():
    """Path to the once-per-day maintenance marker."""
    from core.session_state import get_state_dir
    return os.path.join(get_state_dir(), "last-maintenance.json")


def _maintenance_due(now=None):
    """True if daily SessionStart maintenance should run this session (OPT-06 #179).

    Runs when the marker is missing, >=24h old, or OBI_FORCE_MAINTENANCE is set
    (testing / healthcheck). Any read error errs toward running — visibility
    over silence.
    """
    if os.environ.get("OBI_FORCE_MAINTENANCE"):
        return True
    now = now or datetime.now()
    try:
        with open(_maintenance_marker_path(), "r", encoding="utf-8") as f:
            last_iso = json.load(f).get("last_run_iso")
        if not last_iso:
            return True
        return (now - datetime.fromisoformat(last_iso)).total_seconds() >= 24 * 3600
    except (OSError, ValueError, json.JSONDecodeError):
        return True


def _record_maintenance_run(now=None):
    """Stamp the maintenance marker after a run. Best-effort."""
    now = now or datetime.now()
    try:
        from core.session_state import get_state_dir
        os.makedirs(get_state_dir(), exist_ok=True)
        with open(_maintenance_marker_path(), "w", encoding="utf-8") as f:
            json.dump({"last_run_iso": now.isoformat()}, f, indent=2)
    except OSError:
        pass


def _handle(input_data, timer, deadline_monotonic=None):
    if deadline_monotonic is None:
        deadline_monotonic = time.monotonic() + SESSION_START_SOFT_BUDGET_SECONDS
    from core.hook_logger import HookTimer

    # SessionStart's payload carries session metadata and `source`
    # (startup|resume|clear|compact) — never prompt text. Log that, not a
    # phantom `prompt=` field.
    timer.set_input_summary(f"source={input_data.get('source', 'unknown')}")

    # Persist the session-id sentinel so PostToolUse/PreCompact stay pinned to
    # one state file across an hour boundary (OPT-04 #177). Best-effort.
    with HookTimer("SessionStart/session_state"):
        from core.memory_reader import generate_session_id
        from core.session_state import write_current_session
        write_current_session(input_data.get('session_id') or generate_session_id())

    with HookTimer("SessionStart/configuration"):
        from core.calibration import load_calibration
        from core.version import get_version_display
        version_display = get_version_display()
        calibration = load_calibration()
    welcome_parts = [
        f"[CODING_SESSION_START] Obi Wag {version_display} is installed. "
        f"Run /obi to start the orchestrator workflow."
    ]

    # Grounding is NOT done here. This hook has no prompt to match against, so
    # the old attempt scored an empty string and injected nothing for as long as
    # it existed. It lives in user_prompt_submit.py, on the event that actually
    # carries `prompt` (issue #202). Both stay empty so detectors downstream see
    # honest values rather than a guess.
    task_text = ''
    task_type = ''

    default_budget = calibration.get('verification', {}).get('default_budget', 1)
    welcome_parts.append(
        f"\n[Calibration] Verification budget: {default_budget} (adjust with /obi-memory-review)"
    )

    # Daily maintenance — throttled to once per 24h so session-open latency
    # does not pay for drift hashing + GC sweeps every start (OPT-06 #179).
    # Synchronous work above (welcome, version, calibration) always runs;
    # OBI_FORCE_MAINTENANCE=1 bypasses the throttle.
    # Drift warnings can therefore appear at most one session late.
    is_maintenance_due = _maintenance_due()

    # Registry-driven detectors (OPT-15: drift_detector_session + drift_nag
    # baseline save). correction_retriever was removed in 0.69.69 — see
    # detector_registry._build_session_start_detectors.
    from core.detector_registry import DetectorContext, run_session_start_detectors

    det_ctx = DetectorContext(
        hook_point='session_start',
        cwd=os.getcwd(),
        start_time=time.time(),
        deadline_monotonic=deadline_monotonic,
        calibration=calibration,
        task_type=task_type,
        task_text=task_text,
        maintenance_due=is_maintenance_due,
    )
    with HookTimer("SessionStart/detectors"):
        det_result = run_session_start_detectors(det_ctx)
    if det_result:
        welcome_parts.append(det_result)

    maintenance_completed = False
    if is_maintenance_due and time.monotonic() < (deadline_monotonic - 0.35):
        # drift_detector + drift_nag baseline: migrated to detector registry
        # (OPT-15). Runs via run_session_start_detectors() above.

        with HookTimer("SessionStart/maintenance"):
            _gc_maintenance(welcome_parts, deadline_monotonic)
            _check_memory_md_size(welcome_parts, deadline_monotonic)
            maintenance_completed = (
                time.monotonic() < deadline_monotonic
                and not (det_result and 'Daily check deferred' in det_result)
            )
            if maintenance_completed:
                _record_maintenance_run()

    # List projects in working directory (cached — see Issue #133).
    if time.monotonic() < (deadline_monotonic - 0.25):
        try:
            with HookTimer("SessionStart/project_listing"):
                working_dir = _resolve_project_working_dir()
                if working_dir:
                    subdirs = _scan_projects_cached(working_dir, deadline_monotonic)
                    if subdirs:
                        welcome_parts.append("\n\n📁 Projects in working directory:")
                        for subdir in subdirs:
                            welcome_parts.append(f"   • {subdir}")
        except Exception as exc:
            log_swallowed("project_listing", exc)

    timer.set_output_summary("context injected successfully")
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(welcome_parts),
        }
    }


def main():
    hook_started_monotonic = time.monotonic()
    deadline_monotonic = hook_started_monotonic + SESSION_START_SOFT_BUDGET_SECONDS
    from core.worker_guard import exit_if_worker
    exit_if_worker()  # OPT-23: no-op inside a headless worker subprocess
    from core.hook_runtime import run_hook
    fallback = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "[CODING_SESSION_START] Obi Wag is installed. "
                "Run /obi to start the orchestrator workflow."
            ),
        }
    }
    def bounded_handle(input_data, timer):
        return _handle(input_data, timer, deadline_monotonic=deadline_monotonic)

    run_hook("SessionStart", bounded_handle, error_name="session_start", fallback=fallback)


if __name__ == '__main__':
    main()

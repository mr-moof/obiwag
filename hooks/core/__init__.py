"""Obi Memory System - Core Hook Utilities

This module provides shared utilities for Obi's self-healing memory hooks.

Re-exported names (functions/classes/constants) are resolved lazily via a
PEP 562 module-level ``__getattr__`` (issue #174). Importing this package no
longer eagerly imports every submodule — and, transitively, PyYAML and the
heavier modules. ``from core import <name>`` keeps working for external
callers, but the owning submodule is only imported the first time ``<name>``
is actually accessed. This keeps per-hook-invocation latency low: a hook that
only needs ``core.paths`` no longer pays to import ``learning_detector``,
``version`` (PyYAML + file I/O), etc.

Note: ``from core import <submodule>`` (e.g. ``from core import calibration``)
still works through normal submodule import and does not go through
``__getattr__`` — that hook is only for the re-exported attributes below.
"""

import importlib

# Static map of re-exported attribute name -> owning submodule (relative).
# Kept in sync with ``__all__``. No submodule is imported at module top;
# resolution happens lazily in ``__getattr__``.
_ATTR_SOURCES = {
    # Calibration
    'load_calibration': '.calibration',
    'get_verification_budget': '.calibration',
    # Memory reader
    'get_memory_path': '.memory_reader',
    'get_repo_path': '.memory_reader',
    'read_session_history': '.memory_reader',
    'write_session_summary': '.memory_reader',
    'log_correction': '.memory_reader',
    'read_grounding_log': '.memory_reader',
    'read_claude_history': '.memory_reader',
    'classify_correction': '.memory_reader',
    # Pattern matcher
    'load_patterns': '.pattern_matcher',
    'match_task_to_patterns': '.pattern_matcher',
    'get_injection_text': '.pattern_matcher',
    # Session state
    'SessionState': '.session_state',
    'get_session_state': '.session_state',
    'cleanup_old_sessions': '.session_state',
    # Hook logger
    'HookTimer': '.hook_logger',
    'get_recent_hook_logs': '.hook_logger',
    'get_hook_stats': '.hook_logger',
    'get_hook_log_path': '.hook_logger',
    # Version
    'CURRENT_VERSION': '.version',
    'VersionInfo': '.version',
    'get_version_info': '.version',
    'get_version_display': '.version',
    'check_for_updates': '.version',
    'apply_updates': '.version',
    # Learning detector
    'Learning': '.learning_detector',
    'LearningType': '.learning_detector',
    'detect_learnings': '.learning_detector',
    'format_learnings_summary': '.learning_detector',
    'serialize_learnings': '.learning_detector',
    'deserialize_learnings': '.learning_detector',
    # Paths
    'get_obiwag_repo_path': '.paths',
    # Git sync
    'PushStrategy': '.git_sync',
    'SyncResult': '.git_sync',
    'FileChange': '.git_sync',
    'get_push_strategy': '.git_sync',
    'sync_learnings': '.git_sync',
    'format_sync_result': '.git_sync',
    # Quality signals
    'check_file_size': '.quality_signals',
    'find_edits_without_read': '.quality_signals',
    'find_file_thrashing': '.quality_signals',
    'compute_quality_signals': '.quality_signals',
    'write_quality_signals_jsonl': '.quality_signals',
}


def __getattr__(name: str):
    """Lazily resolve re-exported attributes (PEP 562).

    On first access of a re-exported name, import its owning submodule and
    return the requested attribute. Unknown names raise ``AttributeError`` so
    normal attribute-lookup semantics (and ``hasattr``) are preserved.
    """
    submodule = _ATTR_SOURCES.get(name)
    if submodule is not None:
        module = importlib.import_module(submodule, __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    """Include lazily re-exported names in ``dir(core)``."""
    return sorted(set(globals()) | set(_ATTR_SOURCES))


__all__ = [
    # Calibration
    'load_calibration',
    'get_verification_budget',
    # Memory reader
    'get_memory_path',
    'get_repo_path',
    'read_session_history',
    'write_session_summary',
    'log_correction',
    'read_grounding_log',
    'read_claude_history',
    'classify_correction',
    # Pattern matcher
    'load_patterns',
    'match_task_to_patterns',
    'get_injection_text',
    # Session state
    'SessionState',
    'get_session_state',
    'cleanup_old_sessions',
    # Hook logger
    'HookTimer',
    'get_recent_hook_logs',
    'get_hook_stats',
    'get_hook_log_path',
    # Version
    'CURRENT_VERSION',
    'VersionInfo',
    'get_version_info',
    'get_version_display',
    'check_for_updates',
    'apply_updates',
    # Learning detector
    'Learning',
    'LearningType',
    'detect_learnings',
    'format_learnings_summary',
    'serialize_learnings',
    'deserialize_learnings',
    # Git sync
    'PushStrategy',
    'SyncResult',
    'FileChange',
    'get_obiwag_repo_path',
    'get_push_strategy',
    'sync_learnings',
    'format_sync_result',
    # Quality signals
    'check_file_size',
    'find_edits_without_read',
    'find_file_thrashing',
    'compute_quality_signals',
    'write_quality_signals_jsonl',
]

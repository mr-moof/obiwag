"""Persistent suppression list for dismissed learnings.

The Stop-hook learning detector re-scans the full session transcript on every
Stop. Without a memory of dismissals, a detector false positive regenerates
on every turn — clearing ``pending-learnings.json`` is Sisyphean. This module
records the ``(type, category)`` of learnings the user has explicitly cleared
or rejected via ``/obi-memory-review`` so :func:`detect_learnings` can drop
them before they ever reach the queue again.

The store lives at ``.obi/suppressed-learnings.json`` and holds a list of
string keys of the form ``"<type>::<category>"`` (category lowercased,
empty string when the learning carries no category). Everything here is
best-effort and never raises — suppression must not be able to block
detection.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Set

from core.paths import get_obi_root
from core.hook_logger import log_swallowed


def get_suppression_path() -> str:
    """Path to the suppression store."""
    return str(get_obi_root() / ".obi" / "suppressed-learnings.json")


def _key(learning_type: str, category: str = "") -> str:
    return f"{(learning_type or '').strip().lower()}::{(category or '').strip().lower()}"


def learning_key(learning) -> str:
    """Suppression key for a Learning object."""
    type_val = getattr(getattr(learning, "type", None), "value", "") or ""
    category = ""
    meta = getattr(learning, "metadata", None)
    if isinstance(meta, dict):
        category = meta.get("category", "") or ""
    return _key(type_val, category)


def load_suppressed() -> Set[str]:
    """Load the set of suppressed keys. Returns empty set on any error."""
    try:
        with open(get_suppression_path(), encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("keys", []))
    except Exception as exc:
        log_swallowed("learning_suppression_load", exc)
        return set()


def is_suppressed(learning) -> bool:
    """True if this learning's (type, category) has been dismissed before."""
    return learning_key(learning) in load_suppressed()


def filter_suppressed(learnings: List) -> List:
    """Drop learnings whose (type, category) is on the suppression list."""
    suppressed = load_suppressed()
    if not suppressed:
        return learnings
    return [l for l in learnings if learning_key(l) not in suppressed]


def _load_store() -> Dict[str, Any]:
    """Load the full suppression store (keys + metadata). Returns empty
    structure on any error."""
    try:
        with open(get_suppression_path(), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"keys": [], "metadata": {}}
        return data
    except Exception as exc:
        log_swallowed("learning_suppression_load_store", exc)
        return {"keys": [], "metadata": {}}


def _save_store(store: Dict[str, Any]) -> bool:
    """Write the full suppression store atomically."""
    try:
        path = get_suppression_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        return True
    except Exception as exc:
        log_swallowed("learning_suppression_save_store", exc)
        return False


def load_suppression_metadata(key: str) -> Optional[Dict[str, Any]]:
    """Load metadata for a specific suppression key.

    Returns None if the key has no metadata or on any error.
    """
    store = _load_store()
    return store.get("metadata", {}).get(key)


def suppress_key(learning_type: str, category: str = "") -> bool:
    """Add a ``(type, category)`` key to the suppression store.

    Called by /obi-memory-review's clear-learnings / reject paths. Returns
    True if written, False on error.
    """
    try:
        store = _load_store()
        keys = set(store.get("keys", []))
        keys.add(_key(learning_type, category))
        store["keys"] = sorted(keys)
        return _save_store(store)
    except Exception as exc:
        log_swallowed("learning_suppression_save", exc)
        return False


def suppress_key_with_metadata(
    learning_type: str,
    category: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Add a suppression key with associated metadata.

    The metadata dict is stored under ``store["metadata"][key]`` and is
    backward-compatible -- existing callers that only read ``keys`` are
    unaffected. Used by the codex blindspot detector to record the catch
    count at suppression time (``suppressed_at_count``) and timestamp
    (``suppressed_at``).

    Returns True if written, False on error.
    """
    try:
        store = _load_store()
        k = _key(learning_type, category)
        keys = set(store.get("keys", []))
        keys.add(k)
        store["keys"] = sorted(keys)
        if metadata:
            if "metadata" not in store:
                store["metadata"] = {}
            store["metadata"][k] = metadata
        return _save_store(store)
    except Exception as exc:
        log_swallowed("learning_suppression_save_meta", exc)
        return False


def suppress_learning(learning) -> bool:
    """Suppress by Learning object (convenience wrapper)."""
    type_val = getattr(getattr(learning, "type", None), "value", "") or ""
    category = ""
    meta = getattr(learning, "metadata", None)
    if isinstance(meta, dict):
        category = meta.get("category", "") or ""
    return suppress_key(type_val, category)

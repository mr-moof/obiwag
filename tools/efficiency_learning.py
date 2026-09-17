"""Read-only Learning eligibility and queue evidence collection."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from tools.peer_review.io_utils import sha256_bytes, sha256_file
except ModuleNotFoundError:
    from peer_review.io_utils import sha256_bytes, sha256_file


def learning_eligibility(evidence: dict[str, Any]) -> dict[str, Any]:
    required = ("pending_learnings", "pending_evolutions", "memory_health", "blindspot_evidence",
                "session_findings", "codex_hook_evidence")
    for name in required:
        item = evidence.get(name)
        if not isinstance(item, dict) or item.get("available") is not True or item.get("parse_ok") is not True:
            return {"action": "dispatch", "reason": f"{name} evidence unavailable or malformed"}
    count_fields = (("pending_learnings", "count"), ("pending_evolutions", "count"),
                    ("session_findings", "corrections"), ("session_findings", "reusable_discoveries"),
                    ("session_findings", "unresolved_failures"), ("codex_hook_evidence", "candidates"))
    count_fields += (("blindspot_evidence", "candidates"),)
    try:
        if any(type(evidence[group][field]) is not int or evidence[group][field] < 0
               for group, field in count_fields):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"action": "dispatch", "reason": "eligibility counts unavailable or malformed"}
    work = [f"{group}.{field}" for group, field in count_fields if int(evidence[group][field]) > 0]
    if evidence["memory_health"].get("due") is not False:
        work.append("memory_health.due")
    if work:
        return {"action": "dispatch", "reason": "actionable learning evidence", "work": work}
    return {"action": "skip", "signal": "LEARNING SKIPPED: no actionable work",
            "reason": "all required queues and evidence are available, valid, empty, and health work is not due"}


def collect_learning_evidence(value: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Read queue/maintenance evidence; never treat parse errors as an empty queue."""
    if not value.get("state_root"):
        raise ValueError("learning requires explicit state_root (the active runtime's .obi directory)")
    root = Path(value["state_root"]).resolve()
    evidence = {name: value.get(name, {}) for name in ("session_findings", "codex_hook_evidence")}
    sources = []

    def observe(name: str, relative: str, parse):
        path = root / relative
        try:
            content = path.read_bytes()
            parsed = parse(content.decode("utf-8-sig"))
            evidence[name] = {"available": True, "parse_ok": True, **parsed}
            sources.append({"path": str(path), "sha256": sha256_bytes(content)})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            evidence[name] = {"available": False, "parse_ok": False, "error": str(exc)}

    def learning_count(text):
        queue = json.loads(text)["learnings"]
        if not isinstance(queue, list):
            raise ValueError("learnings must be an array")
        return {"count": len(queue)}

    observe("pending_learnings", "pending-learnings.json", learning_count)
    try:
        pending = root / 'pending'
        if not pending.is_dir():
            raise FileNotFoundError("pending evolution directory unavailable")
        proposals = list(pending.glob('*.md'))
        evidence['pending_evolutions'] = {"available": True, "parse_ok": True, "count": len(proposals)}
        sources.extend({"path": str(path), "sha256": sha256_file(path)} for path in proposals)
    except OSError as exc:
        evidence['pending_evolutions'] = {"available": False, "parse_ok": False, "error": str(exc)}

    def health_due(text):
        last = datetime.fromisoformat(json.loads(text)['last_run_iso'])
        now = datetime.now(last.tzinfo) if last.tzinfo else datetime.now()
        age = (now - last).total_seconds()
        return {"due": bool(os.environ.get('OBI_FORCE_MAINTENANCE')) or age < 0 or age >= 86400}

    observe('memory_health', 'state/last-maintenance.json', health_due)
    catches = project_root.resolve() / '.obi/codex-catches.jsonl'
    try:
        # The catch-log owner explicitly defines missing/empty as no entries.
        content = catches.read_bytes() if catches.exists() else b''
        entries = [json.loads(line) for line in content.decode('utf-8-sig').splitlines() if line.strip()]
        if any(not isinstance(entry, dict) for entry in entries):
            raise ValueError('catch records must be objects')
        # Nonempty logs require the existing threshold/suppression review. Do not recreate it.
        evidence['blindspot_evidence'] = {"available": True, "parse_ok": True, "candidates": len(entries)}
        sources.append({"path": str(catches), "sha256": sha256_bytes(content), "missing_is_empty": True})
    except (OSError, UnicodeError, ValueError) as exc:
        evidence['blindspot_evidence'] = {"available": False, "parse_ok": False, "error": str(exc)}
    evidence['observed_sources'] = sources
    evidence['state_root'] = str(root)
    return evidence

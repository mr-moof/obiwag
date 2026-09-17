"""Validate model results and mechanically check every citation."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from .io_utils import read_json_file, sha256_file

RESULT_KEYS = {
    "schema_version",
    "provider",
    "verdict",
    "scope_complete",
    "citations",
    "findings",
    "limitations",
}
FINDING_KEYS = {
    "id",
    "severity",
    "category",
    "summary",
    "evidence_ids",
    "rationale",
    "recommendation",
}
CITATION_KEYS = {"id", "path", "line_start", "line_end"}
VERDICTS = {"pass", "fail", "inconclusive"}
SEVERITIES = {"high", "medium", "low"}
CATEGORIES = {
    "invented-api",
    "missed-edge-case",
    "security",
    "test-gap",
    "regression-risk",
    "doc-mismatch",
    "plan-gap",
    "other",
}


def _base_result(provider: str, limitation: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider,
        "peer_verdict": None,
        "validation_status": "invalid",
        "scope_complete": False,
        "findings": [],
        "rejected_findings": [],
        "limitations": [limitation],
    }


def unavailable_result(provider: str, reason: str) -> dict[str, Any]:
    result = _base_result(provider, reason)
    result["validation_status"] = "not_run"
    return result


def invalid_result(provider: str, reason: str) -> dict[str, Any]:
    """A review that ran but whose output cannot be trusted."""
    return _base_result(provider, reason)


def capsule_inventory(capsule_root: Path) -> dict[str, str]:
    """Hash every regular file under the capsule root, keyed by POSIX-relative path.

    Taken BEFORE the provider launches and kept in the runner's memory, never
    inside the capsule: under `--approve-for-me` the peer can rewrite anything in
    the capsule -- including `scope-manifest.json` -- so a manifest-based check
    would be self-authorizing (peer finding PR-001, run 20260901T221915Z).
    Includes the support files (request, instructions, manifest, diff) so a
    tampered manifest or an added file is caught the same way as a changed
    snapshot. Symlinks are not followed.
    """
    inventory: dict[str, str] = {}
    root = capsule_root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            inventory[rel] = sha256_file(path)
        except OSError:
            inventory[rel] = "<unreadable>"
    return inventory


def capsule_changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths added, removed, or modified between two inventories (sorted).

    Provider-owned scratch that the adapters themselves create (for example the
    Claude adapter's `empty-mcp.json`) is written before the inventory is taken,
    so anything new here was written during the review.
    """
    changed: list[str] = []
    for rel, digest in before.items():
        if rel not in after:
            changed.append(f"{rel} (deleted)")
        elif after[rel] != digest:
            changed.append(rel)
    for rel in after:
        if rel not in before:
            changed.append(f"{rel} (added)")
    return sorted(changed)


def capsule_modified_paths(manifest_path: Path, capsule_root: Path) -> list[str]:
    """Return manifest paths whose snapshot changed during the review.

    `--approve-for-me` is the only exec configuration the managed Codex profile
    permit, and it does NOT enforce the read-only sandbox: a probe confirmed the
    automatic reviewer approves writes. Comparing every snapshot against the
    manifest hash after the run is therefore the actual read-only enforcement.
    """
    try:
        manifest = read_json_file(manifest_path, max_bytes=8 * 1024 * 1024)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return ["<manifest unreadable after the review>"]
    changed: list[str] = []
    for entry in manifest.get("files", []):
        snapshot = capsule_root / Path(entry["snapshot_path"])
        if not snapshot.exists() or sha256_file(snapshot) != entry["sha256"]:
            changed.append(entry["path"])
    return changed


# Provider STDERR text that proves the peer's tool calls were refused rather than
# run. Under The configured managed Codex requirements approval_policy is forced to
# UnlessTrusted, and exec mode then rejects every shell call -- the review still
# "completes" in ~80 s with verdict inconclusive and validation_status valid.
# Transport-complete is not evidence that the peer read anything. These markers
# are matched against stderr ONLY: the events stream carries tool OUTPUT, and a
# peer that reads this very file would otherwise be classified as refused.
TOOL_FAILURE_MARKERS = (
    "command execution approval is not supported in exec mode",
    "approval request failed",
    "Rejected(",
)


def unusable_execution_reason(
    result: dict[str, Any], stderr_text: str, successful_commands: int = 0
) -> str | None:
    """Return why a transport-complete review is actually unusable, else None.

    A peer that could not run a single tool call has reviewed nothing; reporting
    it as a clean pass hides a total failure of the review. Conversely, a peer
    that completed at least one command execution DID review something, so no
    text heuristic may override that structural evidence.
    """
    if successful_commands > 0:
        return None
    for marker in TOOL_FAILURE_MARKERS:
        if marker in stderr_text:
            return f"peer tool calls were refused by the provider ({marker!r})"
    verdict = result.get("peer_verdict")
    if verdict == "inconclusive" and not result.get("findings"):
        for limitation in result.get("limitations") or []:
            lowered = str(limitation).lower()
            if any(
                token in lowered
                for token in ("tool", "command", "approval", "sandbox", "permission")
            ):
                return f"peer returned inconclusive after a tool failure: {limitation}"
    return None


def _schema_errors(value: Any, expected_provider: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["result is not a JSON object"]
    if set(value) != RESULT_KEYS:
        errors.append("result fields do not match the v1 contract")
    if value.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if value.get("provider") != expected_provider:
        errors.append(f"provider must be {expected_provider}")
    if value.get("verdict") not in VERDICTS:
        errors.append("verdict is invalid")
    if not isinstance(value.get("scope_complete"), bool):
        errors.append("scope_complete must be boolean")
    if not isinstance(value.get("citations"), list):
        errors.append("citations must be an array")
    if not isinstance(value.get("findings"), list):
        errors.append("findings must be an array")
    if not isinstance(value.get("limitations"), list) or not all(
        isinstance(item, str) for item in value.get("limitations", [])
    ):
        errors.append("limitations must be an array of strings")
    return errors


def _finding_schema_errors(finding: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(finding, dict):
        return ["finding is not an object"]
    if set(finding) != FINDING_KEYS:
        errors.append("finding fields do not match the v1 contract")
    if not isinstance(finding.get("id"), str) or not finding.get("id"):
        errors.append("finding id is missing")
    if finding.get("severity") not in SEVERITIES:
        errors.append("severity is invalid")
    if finding.get("category") not in CATEGORIES:
        errors.append("category is invalid")
    if not isinstance(finding.get("summary"), str) or not finding.get("summary"):
        errors.append("summary is missing")
    for key in ("rationale", "recommendation"):
        if not isinstance(finding.get(key), str):
            errors.append(f"{key} must be a string")
    evidence_ids = finding.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        errors.append("evidence_ids must reference at least one citation")
    elif not all(isinstance(item, str) and item for item in evidence_ids):
        errors.append("evidence_ids must contain non-empty strings")
    elif len(set(evidence_ids)) != len(evidence_ids):
        errors.append("evidence_ids must not contain duplicates")
    return errors


def _citation_errors(
    citation: Any, manifest_files: dict[str, dict[str, Any]], capsule_root: Path
) -> list[str]:
    errors: list[str] = []
    if not isinstance(citation, dict) or set(citation) != CITATION_KEYS:
        return ["citation fields do not match the v1 contract"]
    citation_id = citation.get("id")
    if not isinstance(citation_id, str) or not citation_id:
        errors.append("citation id is missing")
    path = citation.get("path")
    if not isinstance(path, str) or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
        errors.append("citation path is unsafe")
        return errors
    entry = manifest_files.get(path)
    if entry is None:
        return [f"citation path is outside the manifest: {path}"]
    start, end = citation.get("line_start"), citation.get("line_end")
    if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
        errors.append("citation lines must be integers")
    elif start < 1 or end < start or end > entry["lines"]:
        errors.append(f"citation lines out of bounds for {path} (1-{entry['lines']})")
    snapshot = capsule_root / Path(entry["snapshot_path"])
    if not snapshot.exists() or sha256_file(snapshot) != entry["sha256"]:
        errors.append(f"snapshot hash mismatch for {path}")
    return errors


def validate_result(
    raw_path: Path, manifest_path: Path, capsule_root: Path, expected_provider: str
) -> dict[str, Any]:
    try:
        raw = read_json_file(raw_path, max_bytes=256 * 1024)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return _base_result(expected_provider, f"malformed peer result: {exc}")
    top_errors = _schema_errors(raw, expected_provider)
    if top_errors:
        result = _base_result(expected_provider, "; ".join(top_errors))
        if isinstance(raw, dict) and raw.get("verdict") in VERDICTS:
            result["peer_verdict"] = raw["verdict"]
        return result

    try:
        manifest = read_json_file(manifest_path, max_bytes=8 * 1024 * 1024)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return _base_result(expected_provider, f"peer manifest is unreadable: {exc}")
    files = {entry["path"]: entry for entry in manifest["files"]}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    limitations = list(raw["limitations"])
    citation_map: dict[str, dict[str, Any]] = {}
    citation_failures: dict[str, list[str]] = {}
    citation_failure_count = 0
    for index, citation in enumerate(raw["citations"]):
        errors = _citation_errors(citation, files, capsule_root)
        citation_id = citation.get("id") if isinstance(citation, dict) else None
        label = citation_id if isinstance(citation_id, str) and citation_id else f"index {index}"
        if isinstance(citation_id, str) and citation_id and (
            citation_id in citation_map or citation_id in citation_failures
        ):
            errors.append(f"duplicate citation id: {citation_id}")
            citation_map.pop(citation_id, None)
        if errors:
            citation_failure_count += 1
            if isinstance(citation_id, str) and citation_id:
                citation_failures.setdefault(citation_id, []).extend(errors)
            limitations.append(f"Rejected citation {label}: " + "; ".join(errors))
        elif isinstance(citation_id, str) and citation_id not in citation_failures:
            citation_map[citation_id] = citation

    for finding in raw["findings"]:
        errors = _finding_schema_errors(finding)
        if isinstance(finding, dict) and finding.get("id") in seen_ids:
            errors.append(f"duplicate finding id: {finding.get('id')}")
        if isinstance(finding, dict) and isinstance(finding.get("id"), str):
            seen_ids.add(finding["id"])
        resolved_citations: list[dict[str, Any]] = []
        if isinstance(finding, dict) and isinstance(finding.get("evidence_ids"), list):
            for citation_id in finding["evidence_ids"]:
                if not isinstance(citation_id, str) or not citation_id:
                    continue
                if citation_id in citation_failures:
                    errors.extend(
                        f"citation {citation_id}: {item}"
                        for item in citation_failures[citation_id]
                    )
                elif citation_id not in citation_map:
                    errors.append(f"citation id is not defined: {citation_id}")
                else:
                    citation = citation_map[citation_id]
                    resolved_citations.append(
                        {
                            "path": citation["path"],
                            "line_start": citation["line_start"],
                            "line_end": citation["line_end"],
                        }
                    )
        errors = list(dict.fromkeys(errors))
        if errors:
            rejected.append({"finding": finding, "validation_errors": errors})
            limitations.append(
                f"Rejected finding {finding.get('id', '<unknown>') if isinstance(finding, dict) else '<unknown>'}: "
                + "; ".join(errors)
            )
        else:
            normalized = dict(finding)
            normalized.pop("evidence_ids", None)
            normalized["evidence"] = resolved_citations
            accepted.append(normalized)

    has_rejections = bool(rejected or citation_failure_count)
    status = "valid" if not has_rejections else ("partial" if accepted else "invalid")
    return {
        "schema_version": 1,
        "provider": expected_provider,
        "peer_verdict": raw["verdict"],
        "validation_status": status,
        "scope_complete": raw["scope_complete"],
        "findings": accepted,
        "rejected_findings": rejected,
        "limitations": limitations,
    }

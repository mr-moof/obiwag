"""Build a sanitized, immutable-by-contract repository review capsule."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .io_utils import atomic_write_json, canonical_json_bytes, read_json_file, sha256_bytes

DEFAULT_MAX_TOTAL_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_FILES = 5000
REQUEST_MAX_BYTES = 256 * 1024
REQUEST_MAX_EVIDENCE_ITEMS = 32
REQUEST_MAX_ATTACHMENTS = 32
EXCLUDED_PREFIXES = (".git/", ".obi/review/", ".obi/reviews/", "graphify-out/")
REQUEST_KEYS = {
    "schema_version",
    "objective",
    "acceptance_criteria",
    "focus",
    "include_paths",
    "evidence",
    "attachments",
}


class PacketError(ValueError):
    """The requested review packet cannot be built safely."""


@dataclass(frozen=True)
class Capsule:
    run_id: str
    root: Path
    repo: Path
    request_path: Path
    manifest_path: Path
    request_hash: str
    capsule_hash: str
    included_files: int
    included_bytes: int


def remove_capsule(root: Path) -> None:
    """Remove a capsule whose copied evidence files are read-only on Windows."""
    if not root.exists():
        return

    def make_writable_and_retry(function, path, _error) -> None:
        try:
            os.chmod(path, stat.S_IWRITE)
            function(path)
        except OSError:
            raise

    shutil.rmtree(root, onerror=make_writable_and_retry)


def _normalize_relative(value: str) -> str:
    candidate = value.replace("\\", "/").strip()
    while candidate.startswith("./"):
        candidate = candidate[2:]
    pure = PurePosixPath(candidate)
    if (
        not candidate
        or pure.is_absolute()
        or ".." in pure.parts
        or re.match(r"^[A-Za-z]:", candidate)
    ):
        raise PacketError(f"unsafe relative path: {value!r}")
    return pure.as_posix()


def load_request(path: Path) -> dict[str, Any]:
    try:
        request = read_json_file(path, max_bytes=REQUEST_MAX_BYTES)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PacketError(f"invalid request file: {exc}") from exc
    if not isinstance(request, dict):
        raise PacketError("request must be a JSON object")
    unknown = set(request) - REQUEST_KEYS
    if unknown:
        raise PacketError(f"unknown request fields: {', '.join(sorted(unknown))}")
    if request.get("schema_version") != 1:
        raise PacketError("request.schema_version must be 1")
    if not isinstance(request.get("objective"), str) or not request["objective"].strip():
        raise PacketError("request.objective must be a non-empty string")
    criteria = request.get("acceptance_criteria")
    if not isinstance(criteria, list) or not all(
        isinstance(item, str) and item.strip() for item in criteria
    ):
        raise PacketError("request.acceptance_criteria must be an array of strings")
    focus = request.get("focus", [])
    if not isinstance(focus, list) or not all(isinstance(item, str) for item in focus):
        raise PacketError("request.focus must be an array of strings")
    includes = request.get("include_paths", [])
    if not isinstance(includes, list) or not all(isinstance(item, str) for item in includes):
        raise PacketError("request.include_paths must be an array of paths/globs")
    request["include_paths"] = [_normalize_relative(item) for item in includes]
    evidence = request.get("evidence", [])
    if not isinstance(evidence, list):
        raise PacketError("request.evidence must be an array")
    if len(evidence) > REQUEST_MAX_EVIDENCE_ITEMS:
        raise PacketError(
            f"request.evidence exceeds {REQUEST_MAX_EVIDENCE_ITEMS} items; narrow the review"
        )
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"label", "source", "deployed"}:
            raise PacketError("each evidence item requires label, source, and deployed")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(item["label"])):
            raise PacketError(f"unsafe evidence label: {item.get('label')!r}")
        item["source"] = _normalize_relative(str(item["source"]))
        if not isinstance(item["deployed"], str) or not item["deployed"].strip():
            raise PacketError("evidence.deployed must be a non-empty path")
    request.setdefault("focus", [])
    request.setdefault("evidence", [])
    attachments = request.get("attachments", [])
    if not isinstance(attachments, list):
        raise PacketError("request.attachments must be an array")
    if len(attachments) > REQUEST_MAX_ATTACHMENTS:
        raise PacketError(
            f"request.attachments exceeds {REQUEST_MAX_ATTACHMENTS} items; narrow the review"
        )
    for item in attachments:
        if not isinstance(item, dict) or set(item) != {"label", "path"}:
            raise PacketError("each attachment requires label and path")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", str(item["label"])):
            raise PacketError(f"unsafe attachment label: {item.get('label')!r}")
        if not isinstance(item["path"], str) or not item["path"].strip():
            raise PacketError("attachment.path must be a non-empty path")
    request.setdefault("attachments", [])
    return request


def _git_paths(repo_root: Path, *, timeout_sec: float = 20) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PacketError(f"unable to enumerate repository: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise PacketError(f"git ls-files failed: {detail}")
    decoded = proc.stdout.decode("utf-8", errors="strict")
    return sorted({_normalize_relative(item) for item in decoded.split("\0") if item})


def _matches_scope(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        base = pattern.rstrip("/")
        if path == base or path.startswith(base + "/") or fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def _excluded(path: str) -> bool:
    lowered = path.lower()
    return any(lowered == prefix.rstrip("/") or lowered.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and attributes & flag)


def _read_text_file(path: Path, max_file_bytes: int) -> tuple[bytes | None, str | None]:
    size = path.stat().st_size
    if size > max_file_bytes:
        return None, "file_limit"
    data = path.read_bytes()
    if len(data) > max_file_bytes:
        return None, "file_limit"
    if b"\0" in data[:8192]:
        return None, "binary"
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "non_utf8"
    return data, None


def _entry(citation_path: str, snapshot_path: str, kind: str, data: bytes) -> dict[str, Any]:
    return {
        "path": citation_path,
        "snapshot_path": snapshot_path,
        "kind": kind,
        "sha256": sha256_bytes(data),
        "bytes": len(data),
        "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
    }


def payload_scope_sha256(
    *,
    repo_root: Path,
    provider: str,
    request_sha256: str,
    entries: Iterable[dict[str, Any]],
) -> str:
    """Hash exactly the file bytes represented by a preflight or capsule manifest."""
    projected = [
        {
            "path": str(entry["path"]),
            "kind": str(entry["kind"]),
            "sha256": str(entry["sha256"]),
            "bytes": int(entry["bytes"]),
        }
        for entry in entries
    ]
    projected.sort(key=lambda entry: (entry["path"], entry["kind"], entry["sha256"]))
    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "repo_root": str(repo_root.resolve()),
                "provider": provider,
                "request_sha256": request_sha256,
                "files": projected,
            }
        )
    )


def _copy_entry(root: Path, snapshot_path: str, data: bytes) -> None:
    destination = root / Path(snapshot_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    try:
        destination.chmod(stat.S_IREAD)
    except OSError:
        pass


def _git_diff(
    repo_root: Path, include_paths: list[str], *, timeout_sec: float = 20
) -> bytes:
    command = ["git", "diff", "--no-ext-diff", "--no-color", "HEAD", "--"]
    command.extend(include_paths)
    try:
        proc = subprocess.run(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PacketError(f"unable to capture repository diff: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise PacketError(f"git diff failed: {detail}")
    return proc.stdout


def _evidence_path(repo_root: Path, value: str, *, require_repo: bool) -> Path:
    raw = Path(value)
    resolved = (repo_root / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if require_repo:
        try:
            resolved.relative_to(repo_root)
        except ValueError as exc:
            raise PacketError(f"source evidence escapes repository: {value}") from exc
    return resolved


def _peer_request(repo_root: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Remove host paths before request.json enters the provider capsule."""
    sanitized = deepcopy(request)
    sanitized["evidence"] = []
    for item in request["evidence"]:
        label = item["label"]
        source = _evidence_path(repo_root, item["source"], require_repo=True)
        deployed = _evidence_path(repo_root, item["deployed"], require_repo=False)
        sanitized["evidence"].append(
            {
                "label": label,
                "source": f"evidence/{label}/source/{source.name}",
                "deployed": f"evidence/{label}/deployed/{deployed.name}",
            }
        )
    sanitized["attachments"] = []
    for item in request["attachments"]:
        attachment = _evidence_path(repo_root, item["path"], require_repo=False)
        sanitized["attachments"].append(
            {
                "label": item["label"],
                "path": f"attachments/{item['label']}/{attachment.name}",
            }
        )
    return sanitized


def build_capsule(
    repo_root: Path,
    request: dict[str, Any],
    run_id: str,
    *,
    runtime_root: Path | None = None,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    provider: str | None = None,
    expected_payload_scope_sha256: str | None = None,
) -> Capsule:
    repo_root = repo_root.resolve()
    if not (repo_root / ".git").exists():
        raise PacketError(f"not a git repository root: {repo_root}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_id):
        raise PacketError("unsafe run ID")
    runtime = (runtime_root or Path(tempfile.gettempdir()) / "obi-peer-review").resolve()
    # Run IDs are unique only within a repository. Namespace the temporary
    # capsule by canonical repository path so parallel reviews in different
    # checkouts (and parallel pytest workers) cannot collide or consume one
    # another's stale capsule.
    repo_namespace = sha256_bytes(str(repo_root).casefold().encode("utf-8"))[:16]
    root = runtime / repo_namespace / run_id
    if root.exists():
        raise PacketError(f"capsule already exists: {root}")

    entries: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
    comparisons: list[dict[str, Any]] = []
    total = 0
    root.mkdir(parents=True)
    try:
        provider_request = _peer_request(repo_root, request)
        request_bytes = canonical_json_bytes(provider_request)
        request_hash = sha256_bytes(request_bytes)
        atomic_write_json(root / "request.json", provider_request, use_lock=False)

        for relative in _git_paths(repo_root):
            if _excluded(relative) or not _matches_scope(relative, request["include_paths"]):
                exclusions.append({"path": relative, "reason": "scope"})
                continue
            source = repo_root / Path(relative)
            if not source.exists() or not source.is_file():
                exclusions.append({"path": relative, "reason": "missing_or_not_file"})
                continue
            if _is_reparse_or_symlink(source):
                exclusions.append({"path": relative, "reason": "link_or_reparse"})
                continue
            data, reason = _read_text_file(source, max_file_bytes)
            if data is None:
                exclusions.append({"path": relative, "reason": str(reason)})
                continue
            snapshot = f"repo/{relative}"
            total += len(data)
            if total > max_total_bytes or len(entries) >= max_files:
                raise PacketError("capsule limit exceeded; narrow include_paths and retry once")
            _copy_entry(root, snapshot, data)
            entries.append(_entry(relative, snapshot, "repository", data))

        diff = _git_diff(repo_root, request["include_paths"])
        if diff:
            if len(diff) > max_file_bytes or total + len(diff) > max_total_bytes:
                raise PacketError("diff exceeds capsule limit; narrow include_paths")
            _copy_entry(root, "CHANGES.diff", diff)
            entries.append(_entry("CHANGES.diff", "CHANGES.diff", "diff", diff))
            total += len(diff)

        for item in request["evidence"]:
            label = item["label"]
            source_path = _evidence_path(repo_root, item["source"], require_repo=True)
            deployed_path = _evidence_path(repo_root, item["deployed"], require_repo=False)
            pair: dict[str, Any] = {
                "label": label,
                "source": f"evidence/{label}/source/{source_path.name}",
                "deployed": f"evidence/{label}/deployed/{deployed_path.name}",
            }
            hashes: list[str | None] = []
            for role, path in (("source", source_path), ("deployed", deployed_path)):
                if not path.exists() or not path.is_file() or _is_reparse_or_symlink(path):
                    pair[f"{role}_present"] = False
                    hashes.append(None)
                    continue
                data, reason = _read_text_file(path, max_file_bytes)
                if data is None:
                    raise PacketError(f"{role} evidence {label!r} rejected: {reason}")
                snapshot = f"evidence/{label}/{role}/{path.name}"
                citation = snapshot
                total += len(data)
                if total > max_total_bytes or len(entries) >= max_files:
                    raise PacketError("evidence exceeds capsule limit")
                _copy_entry(root, snapshot, data)
                entry = _entry(citation, snapshot, f"evidence_{role}", data)
                entries.append(entry)
                pair[f"{role}_present"] = True
                pair[f"{role}_sha256"] = entry["sha256"]
                hashes.append(entry["sha256"])
            pair["hashes_match"] = hashes[0] is not None and hashes[0] == hashes[1]
            comparisons.append(pair)

        for item in request["attachments"]:
            label = item["label"]
            attachment = _evidence_path(repo_root, item["path"], require_repo=False)
            if not attachment.exists() or not attachment.is_file() or _is_reparse_or_symlink(attachment):
                raise PacketError(f"attachment not found or unsafe: {label}")
            data, reason = _read_text_file(attachment, max_file_bytes)
            if data is None:
                raise PacketError(f"attachment {label!r} rejected: {reason}")
            snapshot = f"attachments/{label}/{attachment.name}"
            total += len(data)
            if total > max_total_bytes or len(entries) >= max_files:
                raise PacketError("attachments exceed capsule limit")
            _copy_entry(root, snapshot, data)
            entries.append(_entry(snapshot, snapshot, "attachment", data))

        if expected_payload_scope_sha256:
            if not provider:
                raise PacketError("payload scope verification requires provider")
            actual_payload_scope_sha256 = payload_scope_sha256(
                repo_root=repo_root,
                provider=provider,
                request_sha256=request_hash,
                entries=entries,
            )
            if actual_payload_scope_sha256 != expected_payload_scope_sha256:
                raise PacketError("payload changed after authorization")

        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "request_sha256": request_hash,
            "files": entries,
            "excluded": exclusions,
            "evidence_comparisons": comparisons,
            "totals": {"files": len(entries), "bytes": total},
        }
        capsule_hash = sha256_bytes(canonical_json_bytes(manifest))
        manifest["capsule_sha256"] = capsule_hash
        atomic_write_json(root / "scope-manifest.json", manifest, use_lock=False)
        instructions = (
            "# Peer review capsule\n\n"
            "Review only the snapshot under `repo/`, `CHANGES.diff`, declared `evidence/`, "
            "and declared `attachments/`. "
            "Do not use network access, remote repositories, MCP, or paths outside this capsule. "
            "Cite repository files using the manifest `path` value and evidence files using their "
            "manifest path. The request is in `request.json`.\n"
        )
        (root / "REVIEW_INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")
        return Capsule(
            run_id=run_id,
            root=root,
            repo=root / "repo",
            request_path=root / "request.json",
            manifest_path=root / "scope-manifest.json",
            request_hash=request_hash,
            capsule_hash=capsule_hash,
            included_files=len(entries),
            included_bytes=total,
        )
    except Exception:
        remove_capsule(root)
        raise

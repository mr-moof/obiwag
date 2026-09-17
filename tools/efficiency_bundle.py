#!/usr/bin/env python3
"""Snapshot and restore only files owned by an Obi deployment manifest."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

try:
    from tools.peer_review.io_utils import (
        atomic_write_json,
        canonical_json_bytes,
        read_json_file,
        sha256_file,
    )
except ModuleNotFoundError:  # direct ``python tools/efficiency_bundle.py`` execution
    from peer_review.io_utils import (  # type: ignore[no-redef]
        atomic_write_json,
        canonical_json_bytes,
        read_json_file,
        sha256_file,
    )


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 16 * 1024 * 1024
SNAPSHOT_METADATA = "snapshot.json"
SNAPSHOT_MANIFEST = "deployment-manifest.json"


class BundleError(ValueError):
    """A fail-closed bundle snapshot or restore error."""


def _root(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise BundleError(f"{label} is not a directory: {resolved}")
    return resolved


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise BundleError("manifest deployed paths must be nonempty strings")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise BundleError(f"manifest deployed path must be relative: {value}")
    if any(part in ("", ".", "..") for part in posix.parts):
        raise BundleError(f"unsafe manifest deployed path: {value}")
    return posix.as_posix()


def _target(runtime_root: Path, relative: str) -> Path:
    target = (runtime_root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        target.relative_to(runtime_root)
    except ValueError as exc:
        raise BundleError(f"manifest deployed path escapes runtime root: {relative}") from exc
    return target


def _load_manifest(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    try:
        value = read_json_file(path, max_bytes=MAX_JSON_BYTES)
        mappings_value = value["mappings"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise BundleError(f"deployment manifest is unreadable or malformed: {exc}") from exc
    if not isinstance(mappings_value, dict) or not mappings_value:
        raise BundleError("deployment manifest mappings must be a nonempty object")

    mappings: dict[str, str] = {}
    for raw_relative, source in mappings_value.items():
        relative = _relative_path(raw_relative)
        if relative in mappings:
            raise BundleError(f"duplicate normalized manifest path: {relative}")
        if not isinstance(source, str) or not source:
            raise BundleError(f"manifest source path must be a nonempty string: {relative}")
        mappings[relative] = source

    hashes_value = value.get("expected_candidate_hashes", value.get("deployed_hashes", {}))
    if hashes_value is None:
        hashes_value = {}
    if not isinstance(hashes_value, dict):
        raise BundleError("deployment manifest deployed_hashes must be an object")
    hashes: dict[str, str] = {}
    for raw_relative, digest in hashes_value.items():
        relative = _relative_path(raw_relative)
        if relative not in mappings:
            raise BundleError(f"deployed hash has no mapping: {relative}")
        if not isinstance(digest, str) or len(digest) != 64:
            raise BundleError(f"invalid deployed SHA-256: {relative}")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise BundleError(f"invalid deployed SHA-256: {relative}") from exc
        hashes[relative] = digest.lower()
    return mappings, hashes


def _manifest_relative(runtime_root: Path, manifest_path: Path) -> str:
    resolved = manifest_path.resolve()
    try:
        relative = resolved.relative_to(runtime_root)
    except ValueError as exc:
        raise BundleError("deployment manifest must be inside the runtime root") from exc
    return _relative_path(relative.as_posix())


def prepare_scope(runtime_root: Path, manifest_path: Path, scope_path: Path) -> dict[str, Any]:
    """Normalize an explicitly reviewed, single-root source/target scope before deployment.

    Legacy Codex manifests mix project/user/runtime roots and cannot safely be passed directly.
    Candidate hashes come from source bytes, never from whatever happens to be deployed later.
    """
    runtime = _root(runtime_root, 'runtime_root')
    _manifest_relative(runtime, manifest_path)
    if manifest_path.exists():
        raise BundleError('scope manifest already exists; choose a new run-scoped path')
    scope = read_json_file(scope_path, max_bytes=MAX_JSON_BYTES)
    source_root = _root(Path(scope['source_root']), 'source_root')
    mappings = scope['mappings']
    if not isinstance(mappings, dict) or not mappings:
        raise BundleError('scope requires nonempty explicit mappings')
    normalized, hashes = {}, {}
    for relative, source in mappings.items():
        relative, source = _relative_path(relative), _relative_path(source)
        _target(runtime, relative)
        source_path = _target(source_root, source)
        if not source_path.is_file():
            raise BundleError(f'candidate source is missing: {source}')
        normalized[relative] = source
        hashes[relative] = sha256_file(source_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path, {'version': 'efficiency-scope-1',
        'source_root': str(source_root), 'mappings': normalized,
        'expected_candidate_hashes': hashes})
    return {'action': 'prepare', 'manifest': str(manifest_path), 'files': len(mappings)}


def snapshot(runtime_root: Path, manifest_path: Path, snapshot_dir: Path) -> dict[str, Any]:
    runtime = _root(runtime_root, "runtime_root")
    manifest = manifest_path.resolve()
    manifest_relative = _manifest_relative(runtime, manifest)
    mappings, _ = _load_manifest(manifest)
    destination = snapshot_dir.resolve()
    if destination.exists():
        raise BundleError(f"snapshot directory already exists: {destination}")

    destination.mkdir(parents=True)
    files_root = destination / "files"
    entries: list[dict[str, Any]] = []
    for relative in sorted(mappings):
        target = _target(runtime, relative)
        if target.exists() and not target.is_file():
            raise BundleError(f"managed path is not a file: {target}")
        present = target.is_file()
        digest = sha256_file(target) if present else None
        if present:
            backup = files_root / Path(*PurePosixPath(relative).parts)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            if sha256_file(backup) != digest:
                raise BundleError(f"snapshot copy hash mismatch: {relative}")
        entries.append({"path": relative, "present": present, "sha256": digest})

    manifest_copy = destination / SNAPSHOT_MANIFEST
    shutil.copy2(manifest, manifest_copy)
    manifest_hash = sha256_file(manifest)
    if sha256_file(manifest_copy) != manifest_hash:
        raise BundleError("snapshot manifest copy hash mismatch")
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "runtime_root": str(runtime),
        "manifest": {"path": manifest_relative, "sha256": manifest_hash},
        "files": entries,
    }
    atomic_write_json(destination / SNAPSHOT_METADATA, metadata)
    return {"action": "snapshot", "snapshot_dir": str(destination), "files": len(entries)}


def _load_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    try:
        metadata = read_json_file(snapshot_dir / SNAPSHOT_METADATA, max_bytes=MAX_JSON_BYTES)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BundleError(f"snapshot metadata is unreadable or malformed: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("schema_version") != SCHEMA_VERSION:
        raise BundleError("unsupported snapshot schema")
    if not isinstance(metadata.get("runtime_root"), str):
        raise BundleError("snapshot runtime_root is missing")
    if not isinstance(metadata.get("manifest"), dict) or not isinstance(metadata.get("files"), list):
        raise BundleError("snapshot manifest or files are missing")
    return metadata


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=str(destination.parent), prefix=f".{destination.name}.restore.")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _ownership_restore_plan(runtime: Path, path: Path | None, mappings: dict[str, str],
                            hashes: dict[str, str], old: dict[str, dict[str, Any]]):
    """Reconcile Claude schema-2 ownership without replacing unrelated manifest entries."""
    if path is None:
        return None
    path = _target(runtime, _manifest_relative(runtime, path))
    digest = sha256_file(path)
    value = read_json_file(path, max_bytes=MAX_JSON_BYTES)
    if (value.get('version') != '2.0' or not isinstance(value.get('mappings'), dict)
            or not isinstance(value.get('deployed_hashes'), dict)):
        raise BundleError('ownership manifest must use the Claude schema-2 contract')
    for relative in sorted(set(mappings) | set(old)):
        prior = old.get(relative, {'present': False, 'sha256': None})
        current_mapping = value['mappings'].get(relative)
        current_hash = value['deployed_hashes'].get(relative)
        if current_mapping is None and current_hash is None and not prior['present']:
            continue  # Already removed by a previous restore.
        if current_mapping != mappings.get(relative):
            raise BundleError(f'ownership mapping collision: {relative}')
        allowed = {item.lower() for item in (hashes.get(relative), prior['sha256']) if item}
        if not isinstance(current_hash, str) or current_hash.lower() not in allowed:
            raise BundleError(f'ownership hash collision: {relative}')
        if prior['present']:
            value['deployed_hashes'][relative] = prior['sha256']
        else:
            value['mappings'].pop(relative, None)
            value['deployed_hashes'].pop(relative, None)
    return path, digest, value


def restore(runtime_root: Path, candidate_manifest: Path, snapshot_dir: Path,
            ownership_manifest: Path | None = None) -> dict[str, Any]:
    runtime = _root(runtime_root, "runtime_root")
    snapshot_root = _root(snapshot_dir, "snapshot_dir")
    metadata = _load_snapshot(snapshot_root)
    if Path(metadata["runtime_root"]).resolve() != runtime:
        raise BundleError("snapshot runtime_root does not match the requested runtime root")

    manifest_relative = _relative_path(metadata["manifest"].get("path"))
    manifest = candidate_manifest.resolve()
    if _manifest_relative(runtime, manifest) != manifest_relative:
        raise BundleError("candidate manifest path does not match the snapshot manifest path")
    candidate_mappings, candidate_hashes = _load_manifest(manifest)
    old_mappings, _ = _load_manifest(snapshot_root / SNAPSHOT_MANIFEST)
    if sha256_file(snapshot_root / SNAPSHOT_MANIFEST) != metadata["manifest"].get("sha256"):
        raise BundleError("snapshot manifest hash mismatch")

    old_entries: dict[str, dict[str, Any]] = {}
    for entry in metadata["files"]:
        if not isinstance(entry, dict):
            raise BundleError("snapshot file entry must be an object")
        relative = _relative_path(entry.get("path"))
        if relative in old_entries or relative not in old_mappings:
            raise BundleError(f"snapshot file scope does not match its manifest: {relative}")
        present = entry.get("present")
        digest = entry.get("sha256")
        if type(present) is not bool or (present and not isinstance(digest, str)) or (not present and digest is not None):
            raise BundleError(f"invalid snapshot file entry: {relative}")
        if present:
            backup = snapshot_root / "files" / Path(*PurePosixPath(relative).parts)
            if not backup.is_file() or sha256_file(backup) != digest:
                raise BundleError(f"snapshot file hash mismatch: {relative}")
        old_entries[relative] = entry
    if set(old_entries) != set(old_mappings):
        raise BundleError("snapshot file scope does not match its manifest")

    actions: list[tuple[str, Path, Path | None]] = []
    collisions: list[str] = []
    for relative in sorted(set(old_entries) | set(candidate_mappings)):
        target = _target(runtime, relative)
        old = old_entries.get(relative, {"present": False, "sha256": None})
        if target.exists() and not target.is_file():
            collisions.append(f"{relative}: current target is not a file")
            continue
        current_hash = sha256_file(target) if target.is_file() else None
        if current_hash == old["sha256"]:
            continue
        expected_current = candidate_hashes.get(relative)
        if current_hash is None and expected_current is not None and old['present']:
            collisions.append(f"{relative}: expected candidate file is missing")
            continue
        if current_hash is not None and expected_current is None:
            collisions.append(f"{relative}: expected current candidate hash is absent")
            continue
        if current_hash is not None and current_hash != expected_current:
            collisions.append(f"{relative}: current file does not match the candidate hash")
            continue
        if old["present"]:
            backup = snapshot_root / "files" / Path(*PurePosixPath(relative).parts)
            actions.append(("restore", target, backup))
        elif current_hash is not None:
            actions.append(("remove", target, None))

    if collisions:
        raise BundleError("restore collision preflight failed: " + "; ".join(collisions))

    if ownership_manifest is not None and ownership_manifest.resolve() == manifest:
        raise BundleError('ownership manifest must be separate from the bundle scope manifest')
    ownership = _ownership_restore_plan(runtime, ownership_manifest, candidate_mappings,
                                       candidate_hashes, old_entries)

    for action, target, source in actions:
        if action == "restore":
            assert source is not None
            _atomic_copy(source, target)
        else:
            target.unlink()
    if ownership is not None:
        owner_path, owner_digest, owner_value = ownership
        if sha256_file(owner_path) != owner_digest:
            raise BundleError('ownership manifest changed during restore; rerun after reconciliation')
        atomic_write_json(owner_path, owner_value)
    _atomic_copy(snapshot_root / SNAPSHOT_MANIFEST, manifest)
    return {
        "action": "restore",
        "snapshot_dir": str(snapshot_root),
        "files_restored": sum(action == "restore" for action, _, _ in actions),
        "files_removed": sum(action == "remove" for action, _, _ in actions),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "snapshot", "restore"))
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--scope", type=Path, help="Reviewed source_root and relative target/source mappings")
    parser.add_argument("--ownership-manifest", type=Path,
                        help="Claude schema-2 manifest whose scoped ownership hashes must be restored")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == 'prepare':
            if args.scope is None:
                raise BundleError('prepare requires --scope')
            result = prepare_scope(args.runtime_root, args.manifest, args.scope)
        elif args.snapshot_dir is None:
            raise BundleError('snapshot/restore require --snapshot-dir')
        elif args.operation == "snapshot":
            result = snapshot(args.runtime_root, args.manifest, args.snapshot_dir)
        else:
            result = restore(args.runtime_root, args.manifest, args.snapshot_dir, args.ownership_manifest)
        sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
        return 0
    except (BundleError, OSError, KeyError, TypeError, ValueError) as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"error": str(exc)}) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

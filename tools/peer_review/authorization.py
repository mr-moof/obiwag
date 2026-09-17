"""Fail-closed standing authorization for public peer-review handoffs."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import canonical_json_bytes, sha256_bytes
from .packet import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES,
    PacketError,
    _evidence_path,
    _excluded,
    _git_diff,
    _git_paths,
    _is_reparse_or_symlink,
    _matches_scope,
    _peer_request,
    _read_text_file,
    load_request,
    payload_scope_sha256,
)
from .runner import resolve_provider


TRUSTED_ROOT = Path(os.environ.get("OBI_TRUSTED_ROOT", str(Path.home() / "source")))
MAX_STANDING_EXTERNAL_FILES = 8
MAX_STANDING_EXTERNAL_REPOSITORIES = 1
MAX_REASON_DETAILS = 16
MAX_REASON_DETAIL_CHARS = 512
MAX_SCOPE_PATHS = 32
DEFAULT_PREFLIGHT_TIMEOUT_SEC = 30

_SENSITIVE_FILENAMES = {
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "password.txt",
    "secrets.json",
    "token.txt",
}
_SENSITIVE_SUFFIXES = {
    ".jks",
    ".kdbx",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
}
_PRIVATE_KEY_BLOCK = re.compile(
    rb"-----BEGIN (?:DSA |EC |OPENSSH |RSA )?PRIVATE KEY-----\r?\n"
    rb"(?:[A-Za-z0-9+/=]{16,}\r?\n){2,}"
    rb"-----END (?:DSA |EC |OPENSSH |RSA )?PRIVATE KEY-----",
    re.IGNORECASE,
)
_TOKEN_PATTERNS = (
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
)
_SECRET_ASSIGNMENT = re.compile(
    rb"(?im)^\s*(?:export\s+)?[\"']?"
    rb"(?:[A-Za-z0-9_.-]+[_-])?"
    rb"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    rb"secret[_-]?(?:access[_-]?key|key)|password|passwd|token)"
    rb"[\"']?\s*[:=]\s*[\"']?([^\s\"'#,}\]()]{8,})"
)
_PLACEHOLDER_WORDS = (
    b"changeme",
    b"deadbeef",
    b"dummy",
    b"example",
    b"fake",
    b"placeholder",
    b"redacted",
    b"supersecret",
)


@dataclass(frozen=True)
class AuthorizationReason:
    code: str
    detail: str


@dataclass(frozen=True)
class AuthorizationDecision:
    status: str
    repo_root: Path
    trusted_root: Path
    primary_platform: str
    provider: str
    request_sha256: str
    payload_scope_sha256: str
    approval_scope_sha256: str
    include_paths: tuple[str, ...]
    repository_files: int
    external_files: int
    estimated_files: int
    estimated_bytes: int
    reasons: tuple[AuthorizationReason, ...]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(reason.code for reason in self.reasons))

    def as_dict(self, *, operation: str = "preflight") -> dict[str, Any]:
        visible_reasons = self.reasons[:MAX_REASON_DETAILS]
        visible_paths = self.include_paths[:MAX_SCOPE_PATHS]
        return {
            "operation": operation,
            "authorization_status": self.status,
            "approval_required": self.status == "approval_required",
            "repo_root": str(self.repo_root),
            "trusted_root": str(self.trusted_root),
            "primary_platform": self.primary_platform,
            "provider": self.provider,
            "request_sha256": self.request_sha256,
            "payload_scope_sha256": self.payload_scope_sha256,
            "approval_scope_sha256": self.approval_scope_sha256,
            "purpose": "read_only_peer_review",
            "scope": {
                "include_paths": list(visible_paths),
                "include_paths_omitted": len(self.include_paths) - len(visible_paths),
                "all_tracked_and_unignored_text": not self.include_paths,
                "repository_files": self.repository_files,
                "external_files": self.external_files,
                "estimated_files": self.estimated_files,
                "estimated_bytes": self.estimated_bytes,
            },
            "reason_codes": list(self.reason_codes),
            "reasons": [
                {
                    "code": reason.code,
                    "detail": reason.detail[:MAX_REASON_DETAIL_CHARS],
                }
                for reason in visible_reasons
            ],
            "reasons_omitted": len(self.reasons) - len(visible_reasons),
        }


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _sensitive_path(path: Path) -> bool:
    name = path.name.casefold()
    if name == ".env" or (
        name.startswith(".env.")
        and not name.endswith((".example", ".sample", ".template"))
    ):
        return True
    return name in _SENSITIVE_FILENAMES or path.suffix.casefold() in _SENSITIVE_SUFFIXES


def _placeholder_fixture(path: Path, data: bytes) -> bool:
    parts = {part.casefold() for part in path.parts}
    fixture_parts = {"example", "examples", "fixture", "fixtures", "sample", "samples", "test", "tests"}
    if parts & fixture_parts:
        return True
    lowered_name = path.name.casefold()
    if any(marker in lowered_name for marker in (".example", ".fixture", ".sample", ".template")):
        return True
    lowered_data = data.lower()
    return any(word in lowered_data for word in _PLACEHOLDER_WORDS)


def _secret_material(data: bytes) -> str | None:
    if _PRIVATE_KEY_BLOCK.search(data):
        return "private_key"
    for pattern in _TOKEN_PATTERNS:
        match = pattern.search(data)
        if match and not any(word in match.group(0).lower() for word in _PLACEHOLDER_WORDS):
            return "token"
    for match in _SECRET_ASSIGNMENT.finditer(data):
        candidate = match.group(1).lower()
        if not any(word in candidate for word in _PLACEHOLDER_WORDS):
            return "credential_assignment"
    return None


def _git_root(path: Path) -> tuple[Path | None, bool]:
    """Resolve a containing worktree without spawning an unbounded git probe."""
    working = path if path.is_dir() else path.parent
    try:
        for candidate in (working, *working.parents):
            if (candidate / ".git").exists():
                return candidate.resolve(), False
    except OSError:
        return None, True
    return None, False


def classify_review(
    *,
    repo_root: Path,
    request_file: Path,
    provider: str,
    platform: str | None,
    trusted_root: Path | None = None,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    timeout_sec: int = DEFAULT_PREFLIGHT_TIMEOUT_SEC,
) -> AuthorizationDecision:
    """Classify whether the public handoff fits standing authorization."""
    if timeout_sec < 1 or timeout_sec > 120:
        raise ValueError("preflight timeout_sec must be between 1 and 120")
    deadline = time.monotonic() + timeout_sec
    repo_root = repo_root.resolve()
    trusted_root = (trusted_root or TRUSTED_ROOT).resolve()
    if not (repo_root / ".git").exists():
        raise PacketError(f"not a git repository root: {repo_root}")
    request = load_request(request_file.resolve())
    request_bytes = canonical_json_bytes(request)
    request_sha256 = sha256_bytes(request_bytes)
    provider_request_sha256 = sha256_bytes(
        canonical_json_bytes(_peer_request(repo_root, request))
    )
    selected = resolve_provider(provider, platform)
    primary = (platform or os.environ.get("OBI_PLATFORM", "")).casefold()
    if primary == "claude-code":
        primary = "claude"

    reasons: list[AuthorizationReason] = []
    seen_reasons: set[tuple[str, str]] = set()

    def add_reason(code: str, detail: str) -> None:
        value = (code, detail)
        if value not in seen_reasons:
            seen_reasons.add(value)
            reasons.append(AuthorizationReason(code, detail))

    def budget_available() -> bool:
        if time.monotonic() < deadline:
            return True
        add_reason("preflight_incomplete", f"inspection exceeded {timeout_sec} seconds")
        return False

    def remaining_timeout() -> float:
        return max(0.1, min(20.0, deadline - time.monotonic()))

    request_material = _secret_material(request_bytes)
    if request_material:
        add_reason("secret_material", f"request metadata: {request_material}")

    if provider.casefold() != "auto":
        add_reason("custom_provider", f"explicit provider {selected}")

    repo_is_trusted = _is_beneath(repo_root, trusted_root)
    if not repo_is_trusted:
        add_reason("repo_outside_trusted_root", str(repo_root))

    repository_files = 0
    external_files = 0
    estimated_files = 0
    estimated_bytes = 0
    external_paths: list[Path] = []
    scope_entries: list[dict[str, Any]] = []

    def inspect_data(
        path: Path,
        label: str,
        *,
        external: bool,
        require_trusted_path: bool,
        payload_path: str,
        payload_kind: str,
    ) -> bool:
        nonlocal external_files, estimated_bytes, estimated_files
        if not budget_available():
            return False
        resolved = path.resolve()
        if external:
            external_files += 1
            external_paths.append(resolved)
        if require_trusted_path and not _is_beneath(resolved, trusted_root):
            add_reason("path_outside_trusted_root", label)
        sensitive_path = _sensitive_path(resolved)
        entry: dict[str, Any] = {
            "label": label,
            "path": str(resolved),
            "payload_path": payload_path,
            "payload_kind": payload_kind,
        }
        if not resolved.exists() or not resolved.is_file():
            if sensitive_path:
                add_reason("sensitive_path", label)
            entry["state"] = "missing_or_not_file"
            scope_entries.append(entry)
            return False
        if _is_reparse_or_symlink(resolved):
            if sensitive_path:
                add_reason("sensitive_path", label)
            entry["state"] = "link_or_reparse"
            scope_entries.append(entry)
            return False
        data, rejected = _read_text_file(resolved, max_file_bytes)
        if data is None:
            if sensitive_path:
                add_reason("sensitive_path", label)
            entry.update(state=str(rejected), bytes=resolved.stat().st_size)
            scope_entries.append(entry)
            if external:
                add_reason("capsule_limit", f"{label}: {rejected}")
            return False
        entry.update(state="included", bytes=len(data), sha256=sha256_bytes(data))
        scope_entries.append(entry)
        estimated_files += 1
        estimated_bytes += len(data)
        material = _secret_material(data)
        if material:
            add_reason("secret_material", f"{label}: {material}")
        elif sensitive_path and not _placeholder_fixture(resolved, data):
            add_reason("sensitive_path", label)
        return True

    try:
        repository_paths = _git_paths(repo_root, timeout_sec=remaining_timeout())
    except PacketError as exc:
        add_reason("preflight_incomplete", str(exc))
        repository_paths = []

    for relative in repository_paths:
        if not budget_available():
            break
        if _excluded(relative) or not _matches_scope(relative, request["include_paths"]):
            continue
        source = repo_root / Path(relative)
        if inspect_data(
            source,
            f"repository {relative}",
            external=False,
            require_trusted_path=False,
            payload_path=relative,
            payload_kind="repository",
        ):
            repository_files += 1
        if estimated_files > max_files or estimated_bytes > max_total_bytes:
            add_reason(
                "capsule_limit",
                f"estimated scope exceeds {max_files} files/{max_total_bytes} bytes",
            )
            break

    if (
        estimated_files <= max_files
        and estimated_bytes <= max_total_bytes
        and budget_available()
    ):
        try:
            diff = _git_diff(
                repo_root,
                request["include_paths"],
                timeout_sec=remaining_timeout(),
            )
        except PacketError as exc:
            add_reason("preflight_incomplete", str(exc))
            diff = b""
        if diff:
            estimated_files += 1
            estimated_bytes += len(diff)
            scope_entries.append(
                {
                    "label": "CHANGES.diff",
                    "path": "CHANGES.diff",
                    "payload_path": "CHANGES.diff",
                    "payload_kind": "diff",
                    "state": "included",
                    "bytes": len(diff),
                    "sha256": sha256_bytes(diff),
                }
            )
            if len(diff) > max_file_bytes:
                add_reason("capsule_limit", f"diff exceeds {max_file_bytes} bytes")
            material = _secret_material(diff)
            if material:
                add_reason("secret_material", f"CHANGES.diff: {material}")

    for item in request["evidence"]:
        if not budget_available():
            break
        label = str(item["label"])
        source = _evidence_path(repo_root, item["source"], require_repo=True)
        deployed = _evidence_path(repo_root, item["deployed"], require_repo=False)
        inspect_data(
            source,
            f"evidence {label} source",
            external=False,
            require_trusted_path=False,
            payload_path=f"evidence/{label}/source/{source.name}",
            payload_kind="evidence_source",
        )
        inspect_data(
            deployed,
            f"evidence {label} deployed ({deployed})",
            external=not _is_beneath(deployed, repo_root),
            require_trusted_path=True,
            payload_path=f"evidence/{label}/deployed/{deployed.name}",
            payload_kind="evidence_deployed",
        )

    for item in request["attachments"]:
        if not budget_available():
            break
        label = str(item["label"])
        attachment = _evidence_path(repo_root, item["path"], require_repo=False)
        if not attachment.exists() or not attachment.is_file() or _is_reparse_or_symlink(attachment):
            raise PacketError(f"attachment not found or unsafe: {label}")
        inspect_data(
            attachment,
            f"attachment {label} ({attachment})",
            external=not _is_beneath(attachment, repo_root),
            require_trusted_path=True,
            payload_path=f"attachments/{label}/{attachment.name}",
            payload_kind="attachment",
        )

    if external_files > MAX_STANDING_EXTERNAL_FILES:
        add_reason(
            "bulk_scope",
            f"{external_files} external files exceeds standing limit "
            f"{MAX_STANDING_EXTERNAL_FILES}",
        )

    external_repositories: set[Path] = set()
    for path in external_paths:
        if not budget_available():
            break
        root, indeterminate = _git_root(path)
        if indeterminate:
            add_reason("undetermined_external_repository", str(path))
            continue
        if root is not None and root != repo_root:
            external_repositories.add(root)
            if len(external_repositories) > MAX_STANDING_EXTERNAL_REPOSITORIES:
                break
    if len(external_repositories) > MAX_STANDING_EXTERNAL_REPOSITORIES:
        add_reason(
            "multi_repository_scope",
            f"{len(external_repositories)} external repositories exceeds standing limit "
            f"{MAX_STANDING_EXTERNAL_REPOSITORIES}",
        )

    if estimated_files > max_files or estimated_bytes > max_total_bytes:
        add_reason(
            "capsule_limit",
            f"estimated {estimated_files} files/{estimated_bytes} bytes exceeds "
            f"{max_files} files/{max_total_bytes} bytes",
        )

    payload_entries = [
        {
            "path": str(entry["payload_path"]),
            "kind": str(entry["payload_kind"]),
            "sha256": str(entry["sha256"]),
            "bytes": int(entry["bytes"]),
        }
        for entry in scope_entries
        if entry.get("state") == "included"
    ]
    payload_hash = payload_scope_sha256(
        repo_root=repo_root,
        provider=selected,
        request_sha256=provider_request_sha256,
        entries=payload_entries,
    )
    status = "standing_approved" if not reasons else "approval_required"
    approval_scope_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "repo_root": str(repo_root),
                "trusted_root": str(trusted_root),
                "primary_platform": primary,
                "provider": selected,
                "request_sha256": request_sha256,
                "payload_scope_sha256": payload_hash,
                "reason_codes": list(dict.fromkeys(reason.code for reason in reasons)),
                "scope_entries": sorted(
                    scope_entries,
                    key=lambda entry: (str(entry["label"]), str(entry["path"])),
                ),
            }
        )
    )
    return AuthorizationDecision(
        status=status,
        repo_root=repo_root,
        trusted_root=trusted_root,
        primary_platform=primary,
        provider=selected,
        request_sha256=request_sha256,
        payload_scope_sha256=payload_hash,
        approval_scope_sha256=approval_scope_sha256,
        include_paths=tuple(request["include_paths"]),
        repository_files=repository_files,
        external_files=external_files,
        estimated_files=estimated_files,
        estimated_bytes=estimated_bytes,
        reasons=tuple(reasons),
    )

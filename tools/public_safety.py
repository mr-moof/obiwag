#!/usr/bin/env python3
"""Audit a Git tree or index before publishing a repository."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence


MAX_BLOB_BYTES = 1_048_576
REGULAR_MODES = {"100644", "100755"}
BINARY_SUFFIXES = {
    ".7z", ".bin", ".class", ".db", ".dll", ".dylib", ".exe", ".gz",
    ".jar", ".mdb", ".pdb", ".pyc", ".rar", ".so", ".sqlite",
    ".sqlite3", ".tar", ".war", ".zip",
}
KEY_SUFFIXES = {".der", ".jks", ".key", ".keystore", ".p12", ".pfx", ".pem"}
PRIVATE_NAMES = {
    ".netrc", "_netrc", ".npmrc", ".pypirc", "credentials", "credentials.json", "id_dsa",
    "id_ecdsa", "id_ed25519", "id_rsa", "secret.json", "secrets.json",
}
CACHE_PARTS = {".cache", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
LOCAL_FILES = {".ds_store", "thumbs.db"}
PRIVATE_HOST_SUFFIXES = (".corp", ".internal", ".lan", ".local", ".private")
PLACEHOLDERS = ("example", "placeholder", "changeme", "replace", "dummy", "sample")

TOKEN_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|token)"
    r"\s*[:=]\s*[\"']?([A-Za-z0-9_./+=-]{16,})"
)
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN " + r"(?:RSA |EC |OPENSSH |DSA )?" + r"PRIVATE KEY-----"
)
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
URL_RE = re.compile(r"(?i)\b(?:https?|ssh)://(?:[^/@\s]+@)?([^/:\s]+)")
WINDOWS_USER_PATH_RE = re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s\"'`)]+")
POSIX_USER_PATH_RE = re.compile(r"(?<!\w)/(?:home|Users)/[^/\s\"'`)]+")
EXAMPLE_USERS = {"user", "username", "your_username", "you", "test", "foo", "...", "<username>", "<user>"}


class AuditError(ValueError):
    """Raised for invalid audit input or an unreadable Git source."""


@dataclass(frozen=True)
class Entry:
    path: str
    mode: str
    object_id: str
    size: Optional[int] = None


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    line: int = 0


def _git(repo: Path, args: Sequence[str], stdin: Optional[bytes] = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AuditError(message[-1] if message else "Git command failed")
    return result.stdout


def _repository_root(repo: Path) -> Path:
    if not repo.exists() or not repo.is_dir():
        raise AuditError("repository path must be an existing directory")
    root = _git(repo, ["rev-parse", "--show-toplevel"]).decode("utf-8", "strict").strip()
    return Path(root)


def _ref_entries(repo: Path, ref: str) -> list[Entry]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@~^{}:+-]*", ref):
        raise AuditError("ref contains unsupported characters")
    tree = _git(repo, ["rev-parse", "--verify", f"{ref}^{{tree}}"])
    tree_id = tree.decode("ascii", "strict").strip()
    raw = _git(repo, ["ls-tree", "-r", "-z", "-l", tree_id])
    entries = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id, raw_size = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", "strict")
        size = int(raw_size) if raw_size.isdigit() else None
        if object_type not in {"blob", "commit"}:
            raise AuditError("Git tree contains an unsupported object type")
        entries.append(Entry(path, mode, object_id, size))
    return entries


def _staged_entries(repo: Path) -> tuple[list[Entry], list[Finding]]:
    raw = _git(repo, ["ls-files", "--stage", "-z"])
    entries, findings = [], []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", "strict")
        if stage != "0":
            findings.append(Finding(path, "index_conflict"))
        else:
            entries.append(Entry(path, mode, object_id))
    return entries, findings


def _object_metadata(repo: Path, entries: Sequence[Entry]) -> dict[str, tuple[str, int]]:
    object_ids = list(dict.fromkeys(entry.object_id for entry in entries))
    if not object_ids:
        return {}
    raw = _git(repo, ["cat-file", "--batch-check"],
               ("\n".join(object_ids) + "\n").encode("ascii"))
    metadata = {}
    for line in raw.decode("ascii", "strict").splitlines():
        object_id, object_type, size = line.split()
        metadata[object_id] = (object_type, int(size))
    return metadata


def _blobs(repo: Path, object_ids: Iterable[str]) -> dict[str, bytes]:
    ids = list(dict.fromkeys(object_ids))
    if not ids:
        return {}
    raw = _git(repo, ["cat-file", "--batch"], ("\n".join(ids) + "\n").encode("ascii"))
    blobs, position = {}, 0
    for expected_id in ids:
        header_end = raw.index(b"\n", position)
        object_id, object_type, raw_size = raw[position:header_end].decode("ascii").split()
        size = int(raw_size)
        start, end = header_end + 1, header_end + 1 + size
        if object_id != expected_id or object_type != "blob" or raw[end:end + 1] != b"\n":
            raise AuditError("Git returned malformed blob data")
        blobs[object_id] = raw[start:end]
        position = end + 1
    return blobs


def _path_rules(path: str) -> set[str]:
    parts = path.replace("\\", "/").split("/")
    lowered = [part.casefold() for part in parts]
    name = lowered[-1]
    suffix = Path(name).suffix
    rules = set()
    if name.startswith(".env"):
        rules.add("environment_file")
    if name in PRIVATE_NAMES or suffix in KEY_SUFFIXES or name.startswith("credentials."):
        rules.add("credential_file")
    if name in LOCAL_FILES or name.endswith((".local.json", ".local.toml", ".local.yaml", ".local.yml")):
        rules.add("user_local_settings")
    if ".idea" in lowered or ".vscode" in lowered and name == "settings.json":
        rules.add("user_local_settings")
    if any(part in CACHE_PARTS for part in lowered):
        rules.add("cache_artifact")
    if "graphify-out" in lowered:
        rules.add("generated_graph")
    if "archive" in lowered or "archives" in lowered:
        rules.add("archive_artifact")
    if ".obi" in lowered:
        obi_index = lowered.index(".obi")
        allowed = lowered[obi_index + 1:] in [["readme.md"], [".gitkeep"]]
        if not allowed:
            rules.add("generated_state")
    if suffix in BINARY_SUFFIXES:
        rules.add("binary_artifact")
    return rules


def _is_private_ipv4(value: str) -> bool:
    octets = [int(part) for part in value.split(".")]
    if len(octets) != 4 or any(part > 255 for part in octets):
        return False
    first, second = octets[:2]
    return first == 10 or first == 192 and second == 168 or first == 172 and 16 <= second <= 31


def _is_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return any(word in lowered for word in PLACEHOLDERS) or any(char in value for char in "<>{}$")


def _content_findings(path: str, data: bytes, deny_terms: Sequence[str]) -> list[Finding]:
    if b"\0" in data:
        return [Finding(path, "binary_content")]
    try:
        text = data.decode("utf-8-sig", "strict")
    except UnicodeDecodeError:
        return [Finding(path, "unknown_encoding")]
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if PRIVATE_KEY_RE.search(line):
            findings.append(Finding(path, "private_key_literal", line_number))
        for match in TOKEN_RE.finditer(line):
            if not _is_placeholder(match.group(1)):
                findings.append(Finding(path, "credential_literal", line_number))
                break
        if any(_is_private_ipv4(value) for value in IPV4_RE.findall(line)):
            findings.append(Finding(path, "private_address", line_number))
        if any(host.casefold().endswith(PRIVATE_HOST_SUFFIXES) for host in URL_RE.findall(line)):
            findings.append(Finding(path, "private_endpoint", line_number))
        user_paths = [*WINDOWS_USER_PATH_RE.finditer(line), *POSIX_USER_PATH_RE.finditer(line)]
        if any(re.split(r"[\\/]+", match.group())[-1].casefold() not in EXAMPLE_USERS for match in user_paths):
            findings.append(Finding(path, "user_machine_path", line_number))
        folded = line.casefold()
        if any(term.casefold() in folded for term in deny_terms):
            findings.append(Finding(path, "denylist_term", line_number))
    return findings


def load_denylist(path: Optional[Path]) -> list[str]:
    if path is None:
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError("denylist must be readable UTF-8 JSON") from exc
    if not isinstance(value, list) or any(not isinstance(term, str) or not term.strip() for term in value):
        raise AuditError("denylist must be a JSON array of non-empty strings")
    return [term.strip() for term in value]


def audit(repo: Path, *, ref: Optional[str] = None, staged: bool = False,
          deny_terms: Sequence[str] = ()) -> list[Finding]:
    if (ref is None) == (not staged):
        raise AuditError("select exactly one of ref or staged")
    root = _repository_root(repo)
    if staged:
        entries, findings = _staged_entries(root)
    else:
        entries, findings = _ref_entries(root, ref or "") , []
    metadata = _object_metadata(root, entries)
    readable = []
    for entry in entries:
        findings.extend(Finding(entry.path, rule) for rule in _path_rules(entry.path))
        object_type, size = metadata.get(entry.object_id, ("missing", 0))
        if entry.mode not in REGULAR_MODES or object_type != "blob":
            findings.append(Finding(entry.path, "unsupported_git_mode"))
        elif size > MAX_BLOB_BYTES:
            findings.append(Finding(entry.path, "blob_too_large"))
        else:
            readable.append(entry)
    blob_data = _blobs(root, (entry.object_id for entry in readable))
    for entry in readable:
        findings.extend(_content_findings(entry.path, blob_data[entry.object_id], deny_terms))
    return sorted(set(findings), key=lambda item: (item.path.casefold(), item.path, item.line, item.rule))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ref")
    source.add_argument("--staged", action="store_true")
    parser.add_argument("--denylist-file", type=Path)
    args = parser.parse_args(argv)
    try:
        findings = audit(args.repo, ref=args.ref, staged=args.staged,
                         deny_terms=load_denylist(args.denylist_file))
    except AuditError as exc:
        parser.error(str(exc))
    for finding in findings:
        print(json.dumps({"path": finding.path, "rule": finding.rule, "line": finding.line},
                         separators=(",", ":"), sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

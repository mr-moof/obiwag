"""Focused tests for the public Git export auditor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLS_DIR))

from public_safety import AuditError, MAX_BLOB_BYTES, audit, load_denylist


def git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=True,
    )
    return result.stdout.decode("ascii").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "--quiet")
    git(path, "config", "user.name", "Audit Test")
    git(path, "config", "user.email", "audit@example.invalid")
    (path / "README.md").write_text("Safe public fixture.\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "--quiet", "-m", "fixture")
    return path


def rules(findings):
    return {(finding.path, finding.rule, finding.line) for finding in findings}


def test_ref_and_staged_ignore_untracked_worktree_files(repo: Path):
    (repo / ".env.production").write_text("untracked\n", encoding="utf-8")

    assert audit(repo, ref="HEAD") == []
    assert audit(repo, staged=True) == []

    git(repo, "add", ".env.production")
    assert (".env.production", "environment_file", 0) in rules(audit(repo, staged=True))
    assert audit(repo, ref="HEAD") == []


def test_forbidden_names_generated_state_and_binary_suffixes(repo: Path):
    paths = ["cache.sqlite", ".obi/state.json", ".vscode/settings.json", "identity.pem"]
    for relative in paths:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n", encoding="utf-8")
    git(repo, "add", *paths)

    found = rules(audit(repo, staged=True))
    assert ("cache.sqlite", "binary_artifact", 0) in found
    assert (".obi/state.json", "generated_state", 0) in found
    assert (".vscode/settings.json", "user_local_settings", 0) in found
    assert ("identity.pem", "credential_file", 0) in found


def test_content_rules_report_lines_without_exposing_values(repo: Path):
    token = "Q" * 24 + "7"
    private_address = ".".join(("10", "23", "45", "67"))
    private_endpoint = "https://" + "build" + ".internal/path"
    key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    local_path = "C:" + "\\Users\\" + "fixture-user\\settings"
    text = "\n".join((f"token={token}", private_address, private_endpoint, key_marker, local_path))
    (repo / "settings.txt").write_text(text, encoding="utf-8")
    git(repo, "add", "settings.txt")

    findings = audit(repo, staged=True)
    serialized = "\n".join(json.dumps(finding.__dict__) for finding in findings)
    assert token not in serialized
    assert rules(findings) >= {
        ("settings.txt", "credential_literal", 1),
        ("settings.txt", "private_address", 2),
        ("settings.txt", "private_endpoint", 3),
        ("settings.txt", "private_key_literal", 4),
        ("settings.txt", "user_machine_path", 5),
    }


def test_external_denylist_is_validated_and_redacted(repo: Path, tmp_path: Path):
    private_term = "private-" + "fixture-term"
    denylist = tmp_path / "deny.json"
    denylist.write_text(json.dumps([private_term]), encoding="utf-8")
    (repo / "notes.txt").write_text(f"contains {private_term}\n", encoding="utf-8")
    git(repo, "add", "notes.txt")

    findings = audit(repo, staged=True, deny_terms=load_denylist(denylist))
    assert rules(findings) == {("notes.txt", "denylist_term", 1)}
    assert private_term not in repr(findings)

    denylist.write_text('{"term": "wrong shape"}', encoding="utf-8")
    with pytest.raises(AuditError, match="JSON array"):
        load_denylist(denylist)


def test_symlink_and_gitlink_modes_are_rejected(repo: Path):
    blob = git(repo, "hash-object", "-w", "--stdin", stdin=b"README.md")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},link")
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},nested")

    found = rules(audit(repo, staged=True))
    assert ("link", "unsupported_git_mode", 0) in found
    assert ("nested", "unsupported_git_mode", 0) in found


def test_unknown_encoding_nul_and_oversized_blobs_are_rejected(repo: Path):
    cases = {
        "encoded.txt": bytes((255, 254, 65)),
        "binary-null.txt": b"left\0right",
        "large.txt": b"x" * (MAX_BLOB_BYTES + 1),
    }
    for path, content in cases.items():
        object_id = git(repo, "hash-object", "-w", "--stdin", stdin=content)
        git(repo, "update-index", "--add", "--cacheinfo", f"100644,{object_id},{path}")

    found = rules(audit(repo, staged=True))
    assert ("encoded.txt", "unknown_encoding", 0) in found
    assert ("binary-null.txt", "binary_content", 0) in found
    assert ("large.txt", "blob_too_large", 0) in found


def test_cli_rejects_missing_source_selection(repo: Path):
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "public_safety.py"), "--repo", str(repo)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""

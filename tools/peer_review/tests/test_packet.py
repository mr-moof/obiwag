from __future__ import annotations

import json
import os
import subprocess
import hashlib
from pathlib import Path

import pytest

from tools.peer_review.io_utils import canonical_json_bytes, sha256_bytes
from tools.peer_review.packet import (
    PacketError,
    _peer_request,
    build_capsule,
    load_request,
    payload_scope_sha256,
)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo with space"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.email", "peer@example.invalid")
    git(repo, "config", "user.name", "Peer Test")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")
    (repo / "policy.md").write_text("# local policy\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / ".obi" / "review").mkdir(parents=True)
    (repo / ".obi" / "review" / "old.json").write_text("{}", encoding="utf-8")
    (repo / "graphify-out").mkdir()
    (repo / "graphify-out" / "GRAPH_REPORT.md").write_text("large graph", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "snapshot")
    (repo / "src" / "app.py").write_text("print('new')\n", encoding="utf-8")
    (repo / "src" / "unicode-π.txt").write_text("evidence π\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("secret ignored", encoding="utf-8")
    (repo / "binary.dat").write_bytes(b"a\0b")
    return repo


def request(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "objective": "Find correctness and reliability defects.",
        "acceptance_criteria": ["Evidence is local and mechanically checkable."],
        "focus": [],
        "include_paths": [],
        "evidence": [],
        "attachments": [],
    }
    value.update(updates)
    return value


def test_load_request_rejects_unknown_fields_and_traversal(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({**request(), "mystery": True}), encoding="utf-8")
    with pytest.raises(PacketError, match="unknown request fields"):
        load_request(path)

    path.write_text(json.dumps(request(include_paths=["../other-repo"])), encoding="utf-8")
    with pytest.raises(PacketError, match="unsafe relative path"):
        load_request(path)


def test_capsule_snapshots_working_tree_and_excludes_unsafe_content(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "run-001", runtime_root=tmp_path / "runtime")

    assert capsule.root.parent.parent == (tmp_path / "runtime").resolve()
    assert not capsule.root.is_relative_to(repo)
    assert (capsule.repo / "src" / "app.py").read_text(encoding="utf-8") == "print('new')\n"
    assert (capsule.repo / "src" / "unicode-π.txt").exists()
    assert not (capsule.repo / "ignored.txt").exists()
    assert not (capsule.repo / "binary.dat").exists()
    assert not (capsule.repo / ".obi" / "review").exists()
    assert not (capsule.repo / "graphify-out").exists()
    assert "print('new')" in (capsule.root / "CHANGES.diff").read_text(encoding="utf-8")

    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    entries = {item["path"]: item for item in manifest["files"]}
    app = entries["src/app.py"]
    source_bytes = (repo / "src" / "app.py").read_bytes()
    assert app["sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert app["bytes"] == len(source_bytes)
    assert app["lines"] == 1
    excluded = {item["path"]: item["reason"] for item in manifest["excluded"]}
    assert excluded["binary.dat"] == "binary"
    assert excluded[".obi/review/old.json"] == "scope"
    assert excluded["graphify-out/GRAPH_REPORT.md"] == "scope"


def test_same_run_id_in_different_repositories_has_distinct_capsules(tmp_path: Path) -> None:
    left_parent = tmp_path / "left"
    right_parent = tmp_path / "right"
    left_parent.mkdir()
    right_parent.mkdir()
    left = make_repo(left_parent)
    right = make_repo(right_parent)
    runtime = tmp_path / "runtime"

    left_capsule = build_capsule(left, request(), "shared-id", runtime_root=runtime)
    right_capsule = build_capsule(right, request(), "shared-id", runtime_root=runtime)

    assert left_capsule.root != right_capsule.root
    assert left_capsule.root.name == right_capsule.root.name == "shared-id"


def test_include_paths_narrows_snapshot_and_diff(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(
        repo,
        request(include_paths=["src/**"]),
        "run-002",
        runtime_root=tmp_path / "runtime",
    )
    assert (capsule.repo / "src" / "app.py").exists()
    assert not (capsule.repo / "policy.md").exists()
    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    repo_paths = {item["path"] for item in manifest["files"] if item["kind"] == "repository"}
    assert repo_paths == {"src/app.py", "src/unicode-π.txt"}


def test_explicit_deployed_evidence_is_copied_and_compared(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    deployed = tmp_path / "installed" / "app.py"
    deployed.parent.mkdir()
    deployed.write_text("print('stale')\n", encoding="utf-8")
    capsule = build_capsule(
        repo,
        request(
            evidence=[
                {"label": "app-deploy", "source": "src/app.py", "deployed": str(deployed)}
            ]
        ),
        "run-003",
        runtime_root=tmp_path / "runtime",
    )
    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    comparison = manifest["evidence_comparisons"][0]
    assert comparison["source_present"] is True
    assert comparison["deployed_present"] is True
    assert comparison["hashes_match"] is False
    assert str(deployed.parent) not in capsule.request_path.read_text(encoding="utf-8")
    assert str(deployed.parent) not in capsule.manifest_path.read_text(encoding="utf-8")
    assert (capsule.root / "evidence" / "app-deploy" / "source" / "app.py").exists()
    assert (capsule.root / "evidence" / "app-deploy" / "deployed" / "app.py").exists()


def test_capsule_limit_is_explicit_and_failed_build_is_removed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    runtime = tmp_path / "runtime"
    with pytest.raises(PacketError, match="capsule limit exceeded"):
        build_capsule(repo, request(), "too-large", runtime_root=runtime, max_total_bytes=8)
    assert not (runtime / "too-large").exists()


def test_capsule_rejects_payload_drift_after_authorization(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    request_value = request(include_paths=["src/**"])
    baseline = build_capsule(
        repo,
        request_value,
        "payload-baseline",
        runtime_root=tmp_path / "runtime",
    )
    manifest = json.loads(baseline.manifest_path.read_text(encoding="utf-8"))
    request_hash = sha256_bytes(canonical_json_bytes(_peer_request(repo, request_value)))
    expected = payload_scope_sha256(
        repo_root=repo,
        provider="claude",
        request_sha256=request_hash,
        entries=manifest["files"],
    )

    (repo / "src" / "app.py").write_text("print('drifted')\n", encoding="utf-8")
    with pytest.raises(PacketError, match="payload changed after authorization"):
        build_capsule(
            repo,
            request_value,
            "payload-drift",
            runtime_root=tmp_path / "runtime",
            provider="claude",
            expected_payload_scope_sha256=expected,
        )


def test_explicit_external_attachment_is_copied_without_browsing_its_parent(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    plan_dir = tmp_path / "outside plans"
    plan_dir.mkdir()
    plan = plan_dir / "plan.md"
    plan.write_text("# Exact plan snapshot\n", encoding="utf-8")
    (plan_dir / "not-requested.md").write_text("must stay out", encoding="utf-8")
    capsule = build_capsule(
        repo,
        request(attachments=[{"label": "plan", "path": str(plan)}]),
        "run-attachment",
        runtime_root=tmp_path / "runtime",
    )
    assert (capsule.root / "attachments" / "plan" / "plan.md").exists()
    assert not list(capsule.root.rglob("not-requested.md"))
    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    assert any(item["kind"] == "attachment" for item in manifest["files"])
    assert str(plan_dir) not in capsule.request_path.read_text(encoding="utf-8")
    assert str(plan_dir) not in capsule.manifest_path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse behavior")
def test_symlink_is_never_followed_when_creation_is_permitted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = repo / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    capsule = build_capsule(repo, request(), "run-link", runtime_root=tmp_path / "runtime")
    assert not (capsule.repo / "link.txt").exists()

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from tools.peer_review import authorization
from tools.peer_review.authorization import (
    AuthorizationDecision,
    AuthorizationReason,
    classify_review,
)
from tools.peer_review.packet import build_capsule, load_request


ROOT = Path(__file__).resolve().parents[3]


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def make_repo(parent: Path) -> Path:
    repo = parent / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "--quiet")
    git(repo, "config", "user.email", "peer@example.invalid")
    git(repo, "config", "user.name", "Peer Test")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('safe')\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "snapshot")
    return repo


def write_request(repo: Path, **updates: object) -> Path:
    request: dict[str, object] = {
        "schema_version": 1,
        "objective": "Review correctness and reliability.",
        "acceptance_criteria": ["Findings cite local evidence."],
        "focus": [],
        "include_paths": [],
        "evidence": [],
        "attachments": [],
    }
    request.update(updates)
    path = repo / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def classify(repo: Path, request: Path, trusted_root: Path, **updates: object):
    arguments: dict[str, object] = {
        "repo_root": repo,
        "request_file": request,
        "provider": "auto",
        "platform": "codex",
        "trusted_root": trusted_root,
    }
    arguments.update(updates)
    return classify_review(**arguments)


def test_standard_opposite_provider_scope_is_standing_approved(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    decision = classify(repo, write_request(repo), trusted)

    assert decision.status == "standing_approved"
    assert decision.provider == "claude"
    assert decision.primary_platform == "codex"
    assert decision.repository_files >= 1
    assert decision.reason_codes == ()
    assert len(decision.approval_scope_sha256) == 64

    capsule = build_capsule(
        repo,
        load_request(write_request(repo)),
        "authorized-payload",
        runtime_root=tmp_path / "runtime",
        provider="claude",
        expected_payload_scope_sha256=decision.payload_scope_sha256,
    )
    assert capsule.included_files == decision.estimated_files


def test_out_of_root_repo_or_attachment_requires_approval(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside_repo = make_repo(tmp_path / "outside-repo-parent")
    (outside_repo / "src" / "leak.txt").write_text(
        "token=" + "glpat-" + "abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8"
    )
    outside_decision = classify(outside_repo, write_request(outside_repo), trusted)
    assert "repo_outside_trusted_root" in outside_decision.reason_codes
    assert "secret_material" in outside_decision.reason_codes
    assert outside_decision.repository_files >= 1

    repo = make_repo(trusted / "inside-parent")
    attachment = tmp_path / "outside-plan.md"
    attachment.write_text("# Plan\n", encoding="utf-8")
    request = write_request(
        repo,
        attachments=[{"label": "plan", "path": str(attachment)}],
    )
    attachment_decision = classify(repo, request, trusted)
    assert "path_outside_trusted_root" in attachment_decision.reason_codes
    assert attachment_decision.status == "approval_required"


def test_custom_provider_and_capsule_overflow_require_approval(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    request = write_request(repo)

    custom = classify(repo, request, trusted, provider="claude")
    assert "custom_provider" in custom.reason_codes

    oversized = classify(repo, request, trusted, max_total_bytes=1)
    assert "capsule_limit" in oversized.reason_codes


def test_sensitive_path_or_secret_material_requires_approval(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    (repo / ".env").write_text("SAFE_PLACEHOLDER=true\n", encoding="utf-8")
    placeholder_path = classify(repo, write_request(repo), trusted)
    assert "sensitive_path" not in placeholder_path.reason_codes

    (repo / ".env").write_text("VALUE=abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8")
    sensitive = classify(repo, write_request(repo), trusted)
    assert "sensitive_path" in sensitive.reason_codes

    (repo / ".env").unlink()
    (repo / "src" / "fixture.txt").write_text(
        "token=" + "glpat-" + "DEADBEEFCAFE0123456789\n", encoding="utf-8"
    )
    placeholder = classify(repo, write_request(repo), trusted)
    assert "secret_material" not in placeholder.reason_codes

    (repo / "src" / "config.txt").write_text(
        "access_token=" + "glpat-" + "abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8"
    )
    secret = classify(repo, write_request(repo), trusted)
    assert "secret_material" in secret.reason_codes

    (repo / "src" / "config.txt").write_text(
        "password=" + "abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8"
    )
    assignment = classify(repo, write_request(repo), trusted)
    assert any("credential_assignment" in reason.detail for reason in assignment.reasons)


@pytest.mark.parametrize(
    "secret_template",
    [
        '"apiKey": "{value}"',
        "DB_PASSWORD={value}",
        "export API_KEY={value}",
        "AWS_SECRET_ACCESS_KEY={value}",
    ],
)
def test_common_quoted_and_prefixed_credentials_require_approval(
    tmp_path: Path, secret_template: str
) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    secret_line = secret_template.format(value="abcdefghijklmnopqrstuvwxyz012345")
    (repo / "src" / "config.txt").write_text(secret_line + "\n", encoding="utf-8")

    decision = classify(repo, write_request(repo), trusted)

    assert "secret_material" in decision.reason_codes


def test_secret_in_request_metadata_requires_approval(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    request = write_request(
        repo,
        objective="Review leaked token " + "glpat-" + "abcdefghijklmnopqrstuvwxyz",
    )

    decision = classify(repo, request, trusted)

    assert decision.status == "approval_required"
    assert any(reason.detail == "request metadata: token" for reason in decision.reasons)


def test_preflight_reason_details_are_bounded(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    for index in range(20):
        (repo / f".env.{index}").write_text(
            "VALUE=abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8"
        )

    output = classify(repo, write_request(repo), trusted).as_dict()

    assert output["reason_codes"] == ["sensitive_path"]
    assert len(output["reasons"]) == 16
    assert output["reasons_omitted"] == 4


def test_preflight_scope_paths_and_reason_detail_are_bounded(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    include_paths = [f"src/path-{index}.py" for index in range(40)]
    scoped = classify(repo, write_request(repo, include_paths=include_paths), trusted).as_dict()

    assert len(scoped["scope"]["include_paths"]) == 32
    assert scoped["scope"]["include_paths_omitted"] == 8

    decision = AuthorizationDecision(
        status="approval_required",
        repo_root=repo,
        trusted_root=trusted,
        primary_platform="codex",
        provider="claude",
        request_sha256="0" * 64,
        payload_scope_sha256="2" * 64,
        approval_scope_sha256="1" * 64,
        include_paths=(),
        repository_files=0,
        external_files=0,
        estimated_files=0,
        estimated_bytes=0,
        reasons=(AuthorizationReason("sensitive_path", "x" * 1000),),
    ).as_dict()
    assert len(decision["reasons"][0]["detail"]) == 512


def test_approval_scope_hash_changes_with_payload_content(tmp_path: Path) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)
    request = write_request(repo)
    before = classify(repo, request, trusted)

    (repo / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    after = classify(repo, request, trusted)

    assert before.request_sha256 == after.request_sha256
    assert before.approval_scope_sha256 != after.approval_scope_sha256


def test_preflight_timeout_fails_closed(tmp_path: Path, monkeypatch) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted)

    def slow_paths(_repo: Path, *, timeout_sec: float):
        time.sleep(1.05)
        return []

    monkeypatch.setattr(authorization, "_git_paths", slow_paths)
    decision = classify_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="auto",
        platform="codex",
        trusted_root=trusted,
        timeout_sec=1,
    )

    assert decision.status == "approval_required"
    assert "preflight_incomplete" in decision.reason_codes


def test_indeterminate_external_repository_fails_closed(tmp_path: Path, monkeypatch) -> None:
    trusted = tmp_path / "trusted"
    repo = make_repo(trusted / "parent")
    attachment = trusted / "external" / "plan.md"
    attachment.parent.mkdir()
    attachment.write_text("# plan\n", encoding="utf-8")
    monkeypatch.setattr(authorization, "_git_root", lambda _path: (None, True))

    decision = classify(
        repo,
        write_request(repo, attachments=[{"label": "plan", "path": str(attachment)}]),
        trusted,
    )

    assert "undetermined_external_repository" in decision.reason_codes


def test_contracts_share_trusted_scope_and_bounded_output_markers() -> None:
    policy = (ROOT / "policies" / "peer-review.md").read_text(encoding="utf-8")
    codex = (ROOT / "platforms" / "codex" / "AGENTS.md").read_text(encoding="utf-8")
    orchestration = (ROOT / "orchestration" / "obi-auto.md").read_text(encoding="utf-8")

    for content in (policy, codex, orchestration):
        assert "OBI_TRUSTED_ROOT" in content
        assert "standing_approved" in content
        assert "approval_required" in content
        assert "ApprovalScopeSha256" in content
        assert "accepted:false" in content
        assert "secret" in content.lower()
        assert "custom provider" in content.lower()

    assert "GRAPH_REPORT.md" in codex
    assert "never dump" in codex.lower()
    assert "small excerpt" in codex.lower()

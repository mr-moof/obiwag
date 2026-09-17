from __future__ import annotations

import json

import pytest

from tools.peer_review import cli
from tools.peer_review.authorization import AuthorizationDecision, AuthorizationReason


def decision(tmp_path, *, status: str = "standing_approved") -> AuthorizationDecision:
    reasons = ()
    if status == "approval_required":
        reasons = (AuthorizationReason("custom_provider", "explicit provider claude"),)
    return AuthorizationDecision(
        status=status,
        repo_root=tmp_path,
        trusted_root=tmp_path,
        primary_platform="codex",
        provider="claude",
        request_sha256="0" * 64,
        payload_scope_sha256="2" * 64,
        approval_scope_sha256="1" * 64,
        include_paths=(),
        repository_files=1,
        external_files=0,
        estimated_files=1,
        estimated_bytes=10,
        reasons=reasons,
    )


@pytest.mark.parametrize("operation", ["run", "start"])
def test_public_provider_operations_forward_explicit_platform(
    operation: str, tmp_path, monkeypatch, capsys
) -> None:
    captured = {}

    def fake_review(**kwargs):
        captured.update(kwargs)
        return {"operation": operation, "accepted": operation == "start"}

    monkeypatch.setattr(cli, f"{operation}_review", fake_review)
    monkeypatch.setattr(cli, "_authorization", lambda _args: decision(tmp_path))
    request = tmp_path / "request.json"
    exit_code = cli.main(
        [
            operation,
            "--provider",
            "auto",
            "--platform",
            "codex",
            "--repo-root",
            str(tmp_path),
            "--request-file",
            str(request),
            "--timeout-sec",
            "10",
        ]
    )

    assert exit_code == 0
    assert captured["provider"] == "auto"
    assert captured["platform"] == "codex"
    assert captured["expected_request_sha256"] == "0" * 64
    assert captured["expected_payload_scope_sha256"] == "2" * 64
    output = capsys.readouterr().out.strip()
    assert '"authorization_status":"standing_approved"' in output


def test_preflight_returns_compact_decision_without_starting_provider(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(cli, "_authorization", lambda _args: decision(tmp_path))

    exit_code = cli.main(
        [
            "preflight",
            "--provider",
            "auto",
            "--platform",
            "codex",
            "--repo-root",
            str(tmp_path),
            "--request-file",
            str(tmp_path / "request.json"),
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"authorization_status":"standing_approved"' in output
    assert json.loads(output)["approval_scope_sha256"] == "1" * 64


def test_approval_required_stops_before_provider_unless_explicitly_approved(
    tmp_path, monkeypatch, capsys
) -> None:
    called = False

    def fake_review(**_kwargs):
        nonlocal called
        called = True
        return {"operation": "run"}

    monkeypatch.setattr(cli, "run_review", fake_review)
    monkeypatch.setattr(
        cli, "_authorization", lambda _args: decision(tmp_path, status="approval_required")
    )
    base = [
        "run",
        "--provider",
        "auto",
        "--platform",
        "codex",
        "--repo-root",
        str(tmp_path),
        "--request-file",
        str(tmp_path / "request.json"),
        "--timeout-sec",
        "10",
    ]

    assert cli.main(base) == 0
    blocked = capsys.readouterr().out
    assert called is False
    assert '"authorization_status":"approval_required"' in blocked
    assert '"accepted":false' in blocked

    assert cli.main([*base, "--authorization", "approved"]) == 0
    missing_hash = capsys.readouterr().out
    assert called is False
    assert '"approval_validation_error"' in missing_hash

    assert (
        cli.main(
            [
                *base,
                "--authorization",
                "approved",
                "--approval-scope-sha256",
                "2" * 64,
            ]
        )
        == 0
    )
    wrong_hash = capsys.readouterr().out
    assert called is False
    assert '"approval_validation_error"' in wrong_hash

    assert (
        cli.main(
            [
                *base,
                "--authorization",
                "approved",
                "--approval-scope-sha256",
                "1" * 64,
            ]
        )
        == 0
    )
    approved = capsys.readouterr().out
    assert called is True
    assert '"authorization_status":"approved"' in approved
    assert '"authorization_reason_codes":["custom_provider"]' in approved


def test_explicit_approved_mode_on_standing_scope_requires_hash_and_stays_distinct(
    tmp_path, monkeypatch, capsys
) -> None:
    called = False

    def fake_review(**_kwargs):
        nonlocal called
        called = True
        return {"operation": "run"}

    monkeypatch.setattr(cli, "run_review", fake_review)
    monkeypatch.setattr(cli, "_authorization", lambda _args: decision(tmp_path))
    base = [
        "run",
        "--provider",
        "auto",
        "--platform",
        "codex",
        "--repo-root",
        str(tmp_path),
        "--request-file",
        str(tmp_path / "request.json"),
        "--timeout-sec",
        "10",
        "--authorization",
        "approved",
    ]

    assert cli.main(base) == 0
    assert called is False
    assert '"accepted":false' in capsys.readouterr().out

    assert cli.main([*base, "--approval-scope-sha256", "1" * 64]) == 0
    assert called is True
    assert '"authorization_status":"standing_approved"' in capsys.readouterr().out

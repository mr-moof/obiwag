from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from tools.peer_review.adapters import (
    RESULT_CLAUDE_JSON,
    RESULT_PROVIDER_FILE,
    AdapterSpec,
    ProviderUnavailable,
)
from tools.peer_review import runner
from tools.peer_review.io_utils import json_file_bytes
from tools.peer_review.packet import PacketError
from tools.peer_review.runner import (
    RESULT_MAX_BYTES,
    StatusStore,
    artifact_paths,
    bounded_result,
    resolve_provider,
    run_review,
)

from .test_packet import make_repo, request
from .test_validation import model_result


def write_request(repo: Path) -> Path:
    path = repo / "request.json"
    path.write_text(json.dumps(request()), encoding="utf-8")
    return path


def fake_builder(
    tmp_path: Path,
    behavior: str,
    provider: str = "codex",
    result_mode: str = RESULT_PROVIDER_FILE,
):
    def build(capsule, _schema, raw):
        script = tmp_path / f"provider-{provider}-{behavior}.py"
        if behavior == "valid":
            value = model_result()
            value["provider"] = provider
            if result_mode == RESULT_CLAUDE_JSON:
                envelope = {"is_error": False, "structured_output": value}
                body = f"import json; print(json.dumps({envelope!r}),flush=True)\n"
            else:
                body = (
                    "import json,pathlib\n"
                    f"pathlib.Path(r'{raw}').write_text(json.dumps({value!r}),encoding='utf-8')\n"
                    "print('{\"event\":\"done\"}',flush=True)\n"
                )
        elif behavior == "malformed":
            if result_mode == RESULT_CLAUDE_JSON:
                body = "print('not json',flush=True)\n"
            else:
                body = f"import pathlib; pathlib.Path(r'{raw}').write_text('not json'); print('done',flush=True)\n"
        elif behavior == "timeout":
            body = "import time; print('started',flush=True); time.sleep(30)\n"
        else:
            body = "import sys,time; sys.stdout.write('x'*5000000); sys.stdout.flush(); time.sleep(30)\n"
        script.write_text(body, encoding="utf-8")
        return AdapterSpec(
            provider,
            [sys.executable, str(script)],
            dict(os.environ),
            "fake-1",
            result_mode,
        )
    return build


def test_request_hash_mismatch_stops_before_run_artifacts(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    with pytest.raises(ValueError, match="request changed after authorization"):
        run_review(
            repo_root=repo,
            request_file=write_request(repo),
            provider="codex",
            timeout_sec=10,
            expected_request_sha256="0" * 64,
        )

    assert not (repo / ".obi" / "review" / "runs").exists()


def test_completed_valid_run_writes_only_primary_consumer_artifacts(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        timeout_sec=10,
        run_id="runner-valid",
        runtime_root=tmp_path / "runtime",
        adapter_builder=fake_builder(tmp_path, "valid"),
    )
    assert output["transport_status"] == "completed"
    assert output["peer_verdict"] == "fail"
    assert output["validation_status"] == "valid"
    status = json.loads(Path(output["status_file"]).read_text(encoding="utf-8"))
    result = json.loads(Path(output["result_file"]).read_text(encoding="utf-8"))
    assert status["terminal"] is True
    assert status["review_verdict"] == "fail"
    status_schema = json.loads(
        (Path(__file__).resolve().parents[2] / "schemas" / "peer-review-status.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(status_schema["required"]) == set(status)
    assert status["transport_status"] in status_schema["properties"]["transport_status"]["enum"]
    assert result["findings"][0]["id"] == "PR-001"
    summary = Path(output["summary_file"]).read_text(encoding="utf-8")
    assert len(summary.splitlines()) <= 200
    assert len(summary.encode("utf-8")) <= 64 * 1024
    assert not (tmp_path / "runtime" / "runner-valid").exists()


def test_malformed_result_does_not_change_completed_transport(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        timeout_sec=10,
        run_id="runner-malformed",
        runtime_root=tmp_path / "runtime",
        adapter_builder=fake_builder(tmp_path, "malformed"),
    )
    assert output["transport_status"] == "completed"
    assert output["peer_verdict"] is None
    assert output["validation_status"] == "invalid"


def test_timeout_is_terminal_inconclusive_and_not_retried(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        # Leave enough headroom for capsule preparation on loaded Windows hosts;
        # the separate preparation-timeout test owns that terminal branch.
        timeout_sec=10,
        run_id="runner-timeout",
        runtime_root=tmp_path / "runtime",
        adapter_builder=fake_builder(tmp_path, "timeout"),
    )
    assert output["transport_status"] == "timed_out"
    assert output["peer_verdict"] is None
    status = json.loads(Path(output["status_file"]).read_text(encoding="utf-8"))
    assert status["kill_verified"] is True
    assert status["validation_status"] == "not_run"


def test_capsule_write_followed_by_timeout_is_still_capsule_modified(tmp_path: Path) -> None:
    # Peer finding COD-002 (run 20260901T221915Z): the integrity comparison must run
    # on every terminal path, not only after a completed transport.
    repo = make_repo(tmp_path)

    def build(capsule, _schema, raw):
        script = tmp_path / "provider-tamper-timeout.py"
        target = capsule.root / "peer-scratch.txt"
        script.write_text(
            "import pathlib,time\n"
            f"pathlib.Path(r'{target}').write_text('written by the peer', encoding='utf-8')\n"
            "print('started',flush=True); time.sleep(30)\n",
            encoding="utf-8",
        )
        return AdapterSpec("codex", [sys.executable, str(script)], dict(os.environ), "fake-1", RESULT_PROVIDER_FILE)

    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        timeout_sec=10,
        run_id="runner-tamper-timeout",
        runtime_root=tmp_path / "runtime",
        adapter_builder=build,
    )
    assert output["transport_status"] == "timed_out"
    assert output["validation_status"] == "invalid"
    assert output["peer_verdict"] is None
    status = json.loads(Path(output["status_file"]).read_text(encoding="utf-8"))
    assert "capsule_modified" in status["detail"]
    assert "peer-scratch.txt (added)" in status["detail"]


def test_preparation_time_counts_against_the_accepted_deadline(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    def slow_builder(capsule, schema, raw):
        time.sleep(1.1)
        return fake_builder(tmp_path, "valid")(capsule, schema, raw)

    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        timeout_sec=1,
        run_id="runner-preparation-timeout",
        runtime_root=tmp_path / "runtime",
        adapter_builder=slow_builder,
    )
    assert output["transport_status"] == "timed_out"
    status = json.loads(Path(output["status_file"]).read_text(encoding="utf-8"))
    assert status["pid"] is None
    assert "preparation exhausted" in status["detail"]


def test_provider_unavailable_is_durable_and_advisory(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)

    def missing(*_):
        raise ProviderUnavailable("provider missing")

    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        timeout_sec=10,
        run_id="runner-missing",
        runtime_root=tmp_path / "runtime",
        adapter_builder=missing,
    )
    assert output["transport_status"] == "unavailable"
    assert output["validation_status"] == "not_run"
    assert Path(output["result_file"]).exists()
    assert Path(output["summary_file"]).exists()


def test_invalid_request_fails_before_a_run_is_accepted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    invalid = repo / "invalid-request.json"
    invalid.write_text('{"schema_version":1}', encoding="utf-8")
    with pytest.raises(PacketError, match="objective"):
        run_review(
            repo_root=repo,
            request_file=invalid,
            provider="codex",
            timeout_sec=10,
            run_id="runner-invalid-request",
            runtime_root=tmp_path / "runtime-invalid",
            adapter_builder=fake_builder(tmp_path, "valid"),
        )
    assert not (repo / ".obi" / "review" / "runs" / "runner-invalid-request").exists()


def test_raw_output_limit_is_terminal_and_never_parsed_as_a_verdict(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="codex",
        timeout_sec=10,
        run_id="runner-output-limit",
        runtime_root=tmp_path / "runtime",
        adapter_builder=fake_builder(tmp_path, "noisy"),
    )
    assert output["transport_status"] == "output_limit"
    assert output["peer_verdict"] is None
    status = json.loads(Path(output["status_file"]).read_text(encoding="utf-8"))
    assert status["terminal"] is True
    assert status["kill_verified"] is True
    assert status["events_bytes"] > 4 * 1024 * 1024


def test_auto_routing_selects_only_the_opposite_provider() -> None:
    assert resolve_provider("auto", "claude") == "codex"
    assert resolve_provider("auto", "codex") == "claude"


def test_same_finding_fixture_validates_in_both_provider_directions(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    request_file = write_request(repo)
    outputs = {}
    for provider, result_mode in (
        ("codex", RESULT_PROVIDER_FILE),
        ("claude", RESULT_CLAUDE_JSON),
    ):
        outputs[provider] = run_review(
            repo_root=repo,
            request_file=request_file,
            provider=provider,
            timeout_sec=10,
            run_id=f"runner-bidirectional-{provider}",
            runtime_root=tmp_path / f"runtime-{provider}",
            adapter_builder=fake_builder(tmp_path, "valid", provider, result_mode),
        )
    results = {
        provider: json.loads(Path(output["result_file"]).read_text(encoding="utf-8"))
        for provider, output in outputs.items()
    }
    assert outputs["codex"]["validation_status"] == "valid"
    assert outputs["claude"]["validation_status"] == "valid"
    assert results["codex"]["findings"][0]["summary"] == results["claude"]["findings"][0]["summary"]


def test_malformed_claude_envelope_is_completed_but_invalid(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = run_review(
        repo_root=repo,
        request_file=write_request(repo),
        provider="claude",
        timeout_sec=10,
        run_id="runner-claude-malformed",
        runtime_root=tmp_path / "runtime-claude-malformed",
        adapter_builder=fake_builder(
            tmp_path, "malformed", "claude", RESULT_CLAUDE_JSON
        ),
    )
    assert output["transport_status"] == "completed"
    assert output["validation_status"] == "invalid"
    result = json.loads(Path(output["result_file"]).read_text(encoding="utf-8"))
    assert any("Claude JSON stream has malformed event" in item for item in result["limitations"])


def test_validated_result_is_compacted_below_its_consumer_read_cap() -> None:
    findings = [
        {
            "id": f"PR-{index:03d}",
            "severity": "medium",
            "category": "test-gap",
            "summary": f"finding {index}",
            "rationale": "r" * 5000,
            "recommendation": "fix it",
            "evidence": [
                {"path": "src/app.py", "line_start": 1, "line_end": 1}
            ],
        }
        for index in range(100)
    ]
    oversized = {
        "schema_version": 1,
        "provider": "claude",
        "peer_verdict": "fail",
        "validation_status": "valid",
        "scope_complete": True,
        "findings": findings,
        "rejected_findings": [
            {"finding": {"padding": "x" * 20000}, "validation_errors": ["bad"]}
        ],
        "limitations": ["diagnostic " + ("z" * 10000)],
    }
    stored = bounded_result(oversized)
    assert len(json_file_bytes(stored, compact=True)) <= RESULT_MAX_BYTES
    assert stored["findings"]
    assert len(stored["findings"]) < len(findings)
    assert stored["rejected_findings"] == []
    assert stored["validation_status"] == "partial"
    assert stored["scope_complete"] is False
    assert "storage cap" in stored["limitations"][0].lower()


def test_progress_status_publication_failure_is_deferred_not_raised(
    tmp_path: Path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, "runner-progress-write-fault")
    store = StatusStore(
        Path(artifacts["status"]),
        run_id="runner-progress-write-fault",
        provider="codex",
        timeout_sec=60,
        artifacts=artifacts,
    )
    original = runner.atomic_write_json
    failed = False

    def fail_once(path: Path, value, **kwargs) -> None:
        nonlocal failed
        if path == store.path and not failed:
            failed = True
            raise PermissionError(5, "transient status contention")
        original(path, value, **kwargs)

    monkeypatch.setattr(runner, "atomic_write_json", fail_once)
    store.progress(123, "windows_job", 1, 0, 0, 0.1, "output", 0.1)
    store.write()
    durable = json.loads(store.path.read_text(encoding="utf-8"))
    assert failed is True
    assert "status publication degraded" in durable["detail"]


def test_heartbeat_retries_after_one_status_publication_failure(
    tmp_path: Path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    _, artifacts = artifact_paths(repo, "runner-heartbeat-write-fault")
    store = StatusStore(
        Path(artifacts["status"]),
        run_id="runner-heartbeat-write-fault",
        provider="codex",
        timeout_sec=60,
        artifacts=artifacts,
    )
    original = runner.atomic_write_json
    attempts = 0

    def fail_once(path: Path, value, **kwargs) -> None:
        nonlocal attempts
        if path == store.path:
            attempts += 1
            if attempts == 1:
                raise PermissionError(5, "transient heartbeat contention")
        original(path, value, **kwargs)

    monkeypatch.setattr(runner, "atomic_write_json", fail_once)
    store.start_heartbeat(interval_sec=0.01)
    deadline = time.monotonic() + 1
    while attempts < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    store.stop_heartbeat()
    assert attempts >= 2
    durable = json.loads(store.path.read_text(encoding="utf-8"))
    assert "status publication degraded" in durable["detail"]

from __future__ import annotations

import json
import stat
from pathlib import Path

from tools.peer_review.validation import (
    capsule_modified_paths,
    unusable_execution_reason,
    validate_result,
)

from .test_packet import make_repo, request
from tools.peer_review.packet import build_capsule


def model_result(path: str = "src/app.py", line: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "provider": "codex",
        "verdict": "fail",
        "scope_complete": True,
        "citations": [
            {"id": "C-001", "path": path, "line_start": line, "line_end": line}
        ],
        "findings": [
            {
                "id": "PR-001",
                "severity": "high",
                "category": "regression-risk",
                "summary": "Behavior changed without a guard",
                "evidence_ids": ["C-001"],
                "rationale": "The changed branch is unconditional.",
                "recommendation": "Add the missing guard.",
            }
        ],
        "limitations": [],
    }


def write_raw(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_valid_result_preserves_peer_verdict_and_accepts_citation(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-1", runtime_root=tmp_path / "runtime")
    raw = tmp_path / "result.json"
    write_raw(raw, model_result())
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    assert result["peer_verdict"] == "fail"
    assert result["validation_status"] == "valid"
    assert [item["id"] for item in result["findings"]] == ["PR-001"]
    assert result["findings"][0]["evidence"] == [
        {"path": "src/app.py", "line_start": 1, "line_end": 1}
    ]
    assert result["rejected_findings"] == []


def test_off_manifest_citation_is_rejected_without_rewriting_verdict(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-2", runtime_root=tmp_path / "runtime")
    raw = tmp_path / "result.json"
    write_raw(raw, model_result("../another-repo/secret.py"))
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    assert result["peer_verdict"] == "fail"
    assert result["validation_status"] == "invalid"
    assert result["findings"] == []
    assert "unsafe" in result["rejected_findings"][0]["validation_errors"][0]


def test_out_of_bounds_and_tampered_snapshot_are_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-3", runtime_root=tmp_path / "runtime")
    snapshot = capsule.repo / "src" / "app.py"
    snapshot.chmod(stat.S_IREAD | stat.S_IWRITE)
    snapshot.write_text("tampered\n", encoding="utf-8")
    raw = tmp_path / "result.json"
    write_raw(raw, model_result(line=99))
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    errors = result["rejected_findings"][0]["validation_errors"]
    assert any("out of bounds" in item for item in errors)
    assert any("hash mismatch" in item for item in errors)


def test_malformed_json_is_inconclusive_validation_data(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-4", runtime_root=tmp_path / "runtime")
    raw = tmp_path / "result.json"
    raw.write_text("not json", encoding="utf-8")
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    assert result["peer_verdict"] is None
    assert result["validation_status"] == "invalid"
    assert "malformed peer result" in result["limitations"][0]


def test_malformed_manifest_is_reported_as_invalid_validation_data(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-bad-manifest", runtime_root=tmp_path / "runtime")
    capsule.manifest_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    capsule.manifest_path.write_text("not json", encoding="utf-8")
    raw = tmp_path / "result.json"
    write_raw(raw, model_result())

    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")

    assert result["validation_status"] == "invalid"
    assert "peer manifest is unreadable" in result["limitations"][0]


def test_provider_mismatch_rejects_result_but_preserves_valid_raw_verdict(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-5", runtime_root=tmp_path / "runtime")
    raw = tmp_path / "result.json"
    value = model_result()
    value["provider"] = "claude"
    write_raw(raw, value)
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    assert result["peer_verdict"] == "fail"
    assert result["validation_status"] == "invalid"
    assert "provider must be codex" in result["limitations"][0]


def test_missing_citation_id_rejects_only_the_referencing_finding(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-6", runtime_root=tmp_path / "runtime")
    raw = tmp_path / "result.json"
    value = model_result()
    value["findings"][0]["evidence_ids"] = ["C-404"]
    write_raw(raw, value)
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    assert result["peer_verdict"] == "fail"
    assert result["validation_status"] == "invalid"
    assert result["findings"] == []
    assert "not defined" in " ".join(result["rejected_findings"][0]["validation_errors"])


def test_duplicate_citation_id_is_ambiguous_and_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "validation-7", runtime_root=tmp_path / "runtime")
    raw = tmp_path / "result.json"
    value = model_result()
    value["citations"].append(dict(value["citations"][0]))
    write_raw(raw, value)
    result = validate_result(raw, capsule.manifest_path, capsule.root, "codex")
    assert result["validation_status"] == "invalid"
    errors = " ".join(result["rejected_findings"][0]["validation_errors"])
    assert "duplicate citation id" in errors


def test_refused_tool_calls_make_a_completed_review_unusable() -> None:
    # K15: codex exec under the managed profile rejects every shell call and still
    # returns verdict inconclusive with validation_status valid.
    clean_pass = {
        "peer_verdict": "inconclusive",
        "validation_status": "valid",
        "findings": [],
        "limitations": [],
    }
    transcript = "ERROR: command execution approval is not supported in exec mode"
    reason = unusable_execution_reason(clean_pass, transcript)
    assert reason and "refused" in reason


def test_inconclusive_with_a_tool_limitation_is_unusable() -> None:
    result = {
        "peer_verdict": "inconclusive",
        "validation_status": "valid",
        "findings": [],
        "limitations": ["Could not run any command: approval was denied"],
    }
    reason = unusable_execution_reason(result, "")
    assert reason and "inconclusive after a tool failure" in reason


def test_an_untouched_capsule_reports_no_modified_paths(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "integrity-1", runtime_root=tmp_path / "runtime")
    assert capsule_modified_paths(capsule.manifest_path, capsule.root) == []


def test_a_peer_write_inside_the_capsule_is_detected(tmp_path: Path) -> None:
    # --approve-for-me does not enforce read-only, so the hash comparison is the
    # only real write protection.
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "integrity-2", runtime_root=tmp_path / "runtime")
    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    entry = manifest["files"][0]
    snapshot = capsule.root / Path(entry["snapshot_path"])
    # The builder marks snapshots read-only; a peer with write approval can clear
    # that bit, which is exactly the case the hash comparison has to catch.
    snapshot.chmod(stat.S_IWRITE | stat.S_IREAD)
    snapshot.write_text("tampered by the peer\n", encoding="utf-8")
    assert capsule_modified_paths(capsule.manifest_path, capsule.root) == [entry["path"]]


def test_a_real_review_is_not_flagged_unusable() -> None:
    result = {
        "peer_verdict": "pass",
        "validation_status": "valid",
        "findings": [],
        "limitations": ["Did not review the generated docs"],
    }
    assert unusable_execution_reason(result, "read 12 files\nexec ok\n") is None


def test_marker_text_in_command_output_is_not_a_refusal() -> None:
    # Regression (run 20260901T221915Z): the peer read adapters.py/validation.py,
    # whose source contains the refusal markers, so the markers appeared in the
    # events stream as command OUTPUT and a real `fail` review was discarded as
    # "refused". Structural evidence (>=1 successful command) must win, and the
    # markers are only ever matched against stderr.
    inconclusive = {
        "peer_verdict": "inconclusive",
        "validation_status": "valid",
        "findings": [],
        "limitations": [],
    }
    stderr_with_marker = "command execution approval is not supported in exec mode"
    assert unusable_execution_reason(inconclusive, stderr_with_marker, successful_commands=1) is None
    assert unusable_execution_reason(inconclusive, "", successful_commands=20) is None
    # With zero successful commands the stderr marker still classifies as refused.
    assert unusable_execution_reason(inconclusive, stderr_with_marker, successful_commands=0)


def test_capsule_inventory_detects_manifest_rewrite_and_unlisted_files(tmp_path: Path) -> None:
    # Peer finding PR-001: the manifest lives inside the writable capsule, so the
    # integrity baseline must be captured outside it before launch and must cover
    # every file, not only manifest-listed snapshots.
    from tools.peer_review.validation import capsule_changes, capsule_inventory

    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "integrity-3", runtime_root=tmp_path / "runtime")
    before = capsule_inventory(capsule.root)
    assert "scope-manifest.json" in before
    assert capsule_changes(before, capsule_inventory(capsule.root)) == []

    # A peer that rewrites a snapshot AND patches the manifest hash to match.
    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    entry = manifest["files"][0]
    snapshot = capsule.root / Path(entry["snapshot_path"])
    snapshot.chmod(stat.S_IWRITE | stat.S_IREAD)
    snapshot.write_text("tampered\n", encoding="utf-8")
    entry["sha256"] = "0" * 64
    capsule.manifest_path.chmod(stat.S_IWRITE | stat.S_IREAD)
    capsule.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    # A peer that drops an unlisted file into the capsule.
    (capsule.root / "notes.txt").write_text("scratch\n", encoding="utf-8")

    changed = capsule_changes(before, capsule_inventory(capsule.root))
    assert "scope-manifest.json" in changed
    assert Path(entry["snapshot_path"]).as_posix() in changed
    assert "notes.txt (added)" in changed


def test_count_successful_commands_reads_structured_events(tmp_path: Path) -> None:
    from tools.peer_review.runner import _count_successful_commands

    events = tmp_path / "events.jsonl"
    lines = [
        '{"type":"thread.started","thread_id":"t"}',
        '{"type":"item.completed","item":{"id":"1","type":"command_execution","status":"completed","exit_code":0,"aggregated_output":"Rejected( appears in OUTPUT only"}}',
        '{"type":"item.completed","item":{"id":"2","type":"command_execution","status":"completed","exit_code":2}}',
        '{"type":"item.completed","item":{"id":"3","type":"agent_message","text":"approval request failed"}}',
        "not json at all",
        '{"type":"item.completed","item":{"id":"4","type":"command_execution","status":"completed","exit_code":0}}',
    ]
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert _count_successful_commands(events) == 2
    # Bounded scan: a cap smaller than the file stops early instead of reading it all.
    assert _count_successful_commands(events, max_bytes=10) == 0
    # A missing file is zero successes, never an exception.
    assert _count_successful_commands(tmp_path / "absent.jsonl") == 0

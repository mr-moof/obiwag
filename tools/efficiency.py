"""Deterministic efficiency facade for Obi phase routing and handoffs.

CLI: python tools/efficiency.py OPERATION --project-root ROOT --runtime-root ROOT
     --phase-table FILE --input FILE [--output FILE]
OPERATION is route, handoff, transition, learning, or validate.  ``--input -`` reads stdin.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

try:
    from tools.efficiency_learning import collect_learning_evidence, learning_eligibility
    from tools.autonomous_recovery import RecoveryError, read_ledger, record_recovery
    from tools.native_phase_state import load_codex_policy, read_state, validate_timeline
    from tools.peer_review.io_utils import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
except ModuleNotFoundError:  # direct ``python tools/efficiency.py`` execution
    from efficiency_learning import collect_learning_evidence, learning_eligibility
    from autonomous_recovery import RecoveryError, read_ledger, record_recovery
    from native_phase_state import load_codex_policy, read_state, validate_timeline
    from peer_review.io_utils import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file


class EfficiencyError(ValueError):
    """A fail-closed contract validation error."""


def _root(path: Path, label: str) -> Path:
    value = path.resolve()
    if not value.is_dir():
        raise EfficiencyError(f"{label} is not a directory: {value}")
    return value


def load_contract(project_root: Path, runtime_root: Path, phase_table: Path) -> dict[str, Any]:
    _root(project_root, "project_root")
    _root(runtime_root, "runtime_root")
    path = phase_table.resolve()
    if not path.is_file():
        raise EfficiencyError(f"phase_table is not a file: {path}")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EfficiencyError(f"phase_table is unreadable or malformed: {exc}") from exc
    validate_policy(contract)
    return contract


def validate_policy(contract: dict[str, Any]) -> None:
    """Validate the shared policy before any runtime consumer uses it."""
    try:
        policy = contract["efficiency"]
        if policy["schema_version"] != 1:
            raise ValueError("unsupported efficiency schema")
        for tier in ("routine", "strong"):
            for platform in ("claude", "codex"):
                route = policy["routing"]["tiers"][tier][platform]
                if not all(isinstance(route[key], str) and route[key].strip() for key in ("model", "effort")):
                    raise ValueError("empty routing model or effort")
        budgets = policy["handoff_budgets"]
        for value in [policy["controlled_prompt_bytes"], budgets["total_bytes"], *budgets["sections"].values()]:
            if type(value) is not int or value <= 0:
                raise ValueError("budgets must be positive integers")
        if set(budgets["sections"]) != {"narrative", "references", "excerpts", "read_batches"}:
            raise ValueError("unexpected budget sections")
    except (KeyError, TypeError, ValueError) as exc:
        raise EfficiencyError(f"invalid efficiency policy: {exc}") from exc


def route_effort(contract: dict[str, Any], task_facts: dict[str, Any]) -> dict[str, Any]:
    policy = contract["efficiency"]["routing"]
    platform = task_facts.get("platform")
    if platform not in policy["tiers"]["strong"]:
        raise EfficiencyError("platform must be claude or codex")
    expected = {
        "familiarity": "familiar",
        "scope": "bounded",
        "security_impact": "none",
        "api_impact": "none",
        "integration_novelty": "known",
        "evidence_gaps": False,
        "tests_available": True,
    }
    reasons = [f"{key}={task_facts.get(key, 'unknown')}" for key, value in expected.items()
               if type(task_facts.get(key)) is not type(value) or task_facts.get(key) != value]
    if task_facts.get("rigor") not in ("standard", "normal"):
        reasons.append(f"rigor={task_facts.get('rigor', 'unknown')}")
    tier = "strong" if reasons else "routine"
    selected = policy["tiers"][tier][platform]
    return {
        "schema_version": 1,
        "tier": tier,
        "platform": platform,
        "model": selected["model"],
        "effort": selected["effort"],
        "reasons": reasons or ["all routine-route facts are explicit and bounded"],
        "evidence": {key: task_facts.get(key, "unknown") for key in expected},
    }


def _resolve(root_name: str, relative: str, roots: dict[str, Path]) -> Path:
    if root_name not in roots:
        raise EfficiencyError(f"unknown source root: {root_name}")
    candidate = (roots[root_name] / relative).resolve()
    try:
        candidate.relative_to(roots[root_name])
    except ValueError as exc:
        raise EfficiencyError(f"path escapes {root_name} root: {relative}") from exc
    return candidate


def read_independent_batch(
    reads: list[dict[str, Any]], roots: dict[str, Path], max_workers: int = 4
) -> list[dict[str, Any]]:
    """Read independent files concurrently while preserving input order and per-read errors."""
    if not 1 <= max_workers <= 8:
        raise EfficiencyError("max_workers must be between 1 and 8")

    def one(spec: dict[str, Any]) -> dict[str, Any]:
        identity = spec.get("id")
        source = {"root": spec.get("root"), "path": spec.get("path")}
        try:
            path = _resolve(str(source["root"]), str(source["path"]), roots)
            value = path.read_bytes()
            limit = spec.get("max_excerpt_bytes", 2048)
            if type(limit) is not int or not 0 <= limit <= 8192:
                raise EfficiencyError("max_excerpt_bytes must be 0..8192")
            result = {"bytes": len(value), "sha256": sha256_bytes(value)}
            if len(value) <= limit:
                try:
                    result["text"] = value.decode("utf-8")
                except UnicodeError:
                    result["retrieval_required"] = True
            else:
                result["retrieval_required"] = True
            return {"id": identity, "source": source,
                    "result": result,
                    "error": None, "unknown": False}
        except Exception as exc:  # one failed independent read must not hide sibling results
            return {"id": identity, "source": source, "result": None,
                    "error": {"type": type(exc).__name__, "message": str(exc)}, "unknown": True}

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(reads)))) as pool:
        return list(pool.map(one, reads))


def _section_bytes(packet: dict[str, Any]) -> dict[str, int]:
    narrative = {key: packet[key] for key in ("acceptance_criteria", "settled_facts", "unknowns")}
    references = [{key: value for key, value in item.items() if key != "excerpt"}
                  for item in packet["source_artifacts"]]
    excerpts = [item["excerpt"] for item in packet["source_artifacts"] if "excerpt" in item]
    return {"narrative": len(canonical_json_bytes(narrative)),
            "references": len(canonical_json_bytes(references)),
            "excerpts": len(canonical_json_bytes(excerpts)),
            "read_batches": len(canonical_json_bytes(packet["independent_read_batches"]))}


def _set_accounting(packet: dict[str, Any], budgets: dict[str, Any]) -> None:
    packet["byte_accounting"] = {"budgets": budgets, "sections": _section_bytes(packet), "total": 0}
    while True:
        size = len(canonical_json_bytes(packet)) + 1  # serialized JSON's final newline
        if packet["byte_accounting"]["total"] == size:
            break
        packet["byte_accounting"]["total"] = size


def build_handoff(
    contract: dict[str, Any], payload: dict[str, Any], project_root: Path, runtime_root: Path,
    source_root: Path | None = None,
) -> dict[str, Any]:
    project = _root(project_root, "project_root")
    runtime = _root(runtime_root, "runtime_root")
    if not isinstance(payload.get("run_id"), str) or not payload["run_id"].strip():
        raise EfficiencyError("handoff requires run_id")
    if type(payload.get("from_phase")) is not int or not 0 <= payload["from_phase"] <= 10:
        raise EfficiencyError("invalid from_phase")
    if type(payload.get("to_phase")) is not int or not 1 <= payload["to_phase"] <= 10:
        raise EfficiencyError("invalid to_phase")
    roots = {"project": project, "runtime": runtime}
    if source_root is not None:
        roots['source'] = _root(source_root, 'source_root')
    budgets = contract["efficiency"]["handoff_budgets"]
    artifacts: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for index, source in enumerate(payload.get("source_artifacts", [])):
        root_name, relative = str(source.get("root")), str(source.get("path"))
        path = _resolve(root_name, relative, roots)
        if not path.is_file():
            raise EfficiencyError(f"source artifact is not a file: {root_name}:{relative}")
        digest = sha256_file(path)
        item = {"id": source.get("id", f"source-{index + 1}"), "root": root_name,
                "path": relative, "sha256": digest, "bytes": path.stat().st_size,
                "relevance": str(source.get("relevance", "")),
                "required": bool(source.get("required", True)),
                "retrieval": {"root": root_name, "path": relative, "sha256": digest}}
        if digest in seen:
            item["duplicate_of"] = seen[digest]
        else:
            seen[digest] = str(item["id"])
            if source.get("include_excerpt", True) and item["bytes"] <= budgets["sections"]["excerpts"]:
                try:
                    item["excerpt"] = path.read_text(encoding="utf-8")
                except UnicodeError:
                    item["excerpt_unavailable"] = "non-utf8 source; use retrieval reference"
            elif source.get("include_excerpt", True):
                item["excerpt_unavailable"] = "source exceeds excerpt budget; use retrieval reference"
        artifacts.append(item)

    batches = []
    for batch in payload.get("independent_read_batches", []):
        batches.append({"name": batch.get("name"), "results": read_independent_batch(
            list(batch.get("reads", [])), roots, int(batch.get("max_workers", 4)))})
    packet = {
        "schema_version": 1,
        "run_id": payload.get("run_id"),
        "from_phase": payload.get("from_phase"),
        "to_phase": payload.get("to_phase"),
        "roots": {name: str(path) for name, path in roots.items()},
        "acceptance_criteria": list(payload.get("acceptance_criteria", [])),
        "settled_facts": list(payload.get("settled_facts", [])),
        "unknowns": list(payload.get("unknowns", [])),
        "source_artifacts": artifacts,
        "independent_read_batches": batches,
        "overflow_actions": [],
    }

    def over() -> bool:
        _set_accounting(packet, budgets)
        sections = packet["byte_accounting"]["sections"]
        section_limits = budgets["sections"]
        return (packet["byte_accounting"]["total"] > budgets["total_bytes"] or
                any(sections[name] > section_limits[name] for name in section_limits))

    candidates = ([item for item in reversed(artifacts) if "excerpt" in item and not item["required"]] +
                  [item for item in reversed(artifacts) if "excerpt" in item and item["required"]])
    while over() and candidates:
        item = candidates.pop(0)
        del item["excerpt"]
        packet["overflow_actions"].append({"source_id": item["id"],
                                             "action": "replace excerpt with hashed retrieval reference"})
    _set_accounting(packet, budgets)
    sections = packet["byte_accounting"]["sections"]
    if packet["byte_accounting"]["total"] > budgets["total_bytes"]:
        raise EfficiencyError("handoff metadata exceeds total byte budget")
    for name, limit in budgets["sections"].items():
        if sections[name] > limit:
            raise EfficiencyError(f"handoff {name} section exceeds byte budget")
    return packet


def _signal_matches(spec: dict[str, Any], signal: str) -> bool:
    mode = spec.get("match_mode", "literal")
    expected = spec.get("value", spec.get("on_signal"))
    if not isinstance(expected, str):
        raise EfficiencyError("signal specification has no value")
    if mode == "literal":
        return signal == expected
    if mode == "prefix":
        return signal.startswith(expected)
    if mode == "regex":
        return re.fullmatch(spec["pattern"], signal) is not None
    raise EfficiencyError(f"unsupported signal match mode: {mode}")


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if not match:
        raise EfficiencyError("integration report has no parseable frontmatter")
    result: dict[str, str] = {}
    section = ""
    for raw in match.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, separator, value = raw.strip().partition(":")
        if not separator:
            raise EfficiencyError("malformed integration report frontmatter")
        if indent == 0 and not value.strip():
            section = key
        else:
            result[f"{section}.{key}" if indent else key] = value.strip()
    return result


def _git(project: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
    if completed.returncode:
        raise EfficiencyError(f"git {' '.join(args)} failed with exit {completed.returncode}")
    return completed.stdout.replace(b"\r\n", b"\n")


def verify_integrate_noop(project_root: Path, report_relative: str = ".obi/integration-report.md") -> dict[str, Any]:
    project = _root(project_root, "project_root")
    report = _resolve("project", report_relative, {"project": project})
    fields = _frontmatter(report)
    count_keys = ["review_counts.critical_faults", "review_counts.required_fixes",
                  "review_counts.optional_improvements", "review_counts.disputed_findings",
                  "triage_counts.accepted", "triage_counts.rejected", "triage_counts.disputed"]
    if fields.get("artifact") != "integration-report" or fields.get("no_op_guard") != "PASS":
        raise EfficiencyError("integration report does not declare a complete no-op receipt")
    try:
        if any(int(fields[key]) != 0 for key in count_keys):
            raise EfficiencyError("integration no-op receipt contains nonzero counts")
    except (KeyError, ValueError) as exc:
        raise EfficiencyError("integration no-op receipt has missing or malformed counts") from exc
    if fields.get("git_evidence.digest_algorithm") != "sha256/git-status-porcelain-v1-lf":
        raise EfficiencyError("integration no-op receipt has unsupported digest algorithm")
    head_before = fields.get("git_evidence.head_before", "")
    head_after = fields.get("git_evidence.head_after", "")
    before = fields.get("git_evidence.worktree_digest_before", "")
    after = fields.get("git_evidence.worktree_digest_after", "")
    if not re.fullmatch(r"[0-9a-f]{40}", head_before) or head_before != head_after:
        raise EfficiencyError("integration no-op HEAD evidence is invalid or changed")
    if not re.fullmatch(r"[0-9a-f]{64}", before) or before != after:
        raise EfficiencyError("integration no-op worktree evidence is invalid or changed")
    current_head = _git(project, "rev-parse", "HEAD").decode("ascii").strip()
    current_digest = sha256_bytes(_git(project, "status", "--porcelain=v1", "--untracked-files=all"))
    if current_head != head_after or current_digest != after:
        raise EfficiencyError("independent git probes do not match integration receipt")
    return {"verified": True, "report": str(report), "head": current_head,
            "worktree_digest": current_digest}


def _authoritative_recovery(contract_path: Path, request: dict[str, Any], project: Path) -> dict[str, Any]:
    phase = int(request["phase"])
    native_path = None
    if request.get("native_state_path"):
        native_path = _resolve("project", request["native_state_path"], {"project": project.resolve()})
        policy = load_codex_policy(phase, contract_path)
        state = read_state(native_path)
        errors = validate_timeline(state, policy)
        if errors:
            raise EfficiencyError("native recovery timeline invalid: " + "; ".join(errors))
    ledger = _resolve("project", request["state_path"], {"project": project.resolve()})
    # Resume/replay the same decision without appending a second recovery action.
    for previous in read_ledger(ledger, request["run_id"]):
        same_evidence = (previous.get("evidence", {}).get("summary") == str(request.get("evidence", "")).strip()
                         if native_path is None else previous.get("evidence", {}).get("state_path") == str(native_path))
        if (previous.get("trigger") == request["event"] and previous.get("phase") == phase
                and previous.get("provider") == request.get("provider", "codex-native") and same_evidence):
            return previous
    ledger_path, decision = record_recovery(
        event=request["event"], run_id=request["run_id"], phase=phase,
        provider=request.get("provider", "codex-native"), evidence=request.get("evidence"),
        native_state_path=native_path, phase_table_path=contract_path,
        state_path=ledger,
    )
    records = read_ledger(ledger_path, request["run_id"])
    if not records or records[-1] != decision:
        raise EfficiencyError("authoritative recovery ledger replay disagrees with decision")
    return decision


def select_transition(
    contract: dict[str, Any], lane: str, phase: int, signal: str, handoff: dict[str, Any],
    project_root: Path, phase_table: Path
) -> dict[str, Any]:
    phase_spec = next((item for item in contract["phases"] if item["n"] == phase), None)
    if phase == 0 and lane == 'max':
        phase_spec = {'n': 0, 'primary_signal': {'value': 'PHASE 0 COMPLETE', 'match_mode': 'literal'},
                      'default_strategy': 'inline', 'verdict_in_body': False}
    if phase_spec is None or lane not in contract["lanes"]:
        raise EfficiencyError("unknown phase or lane")
    accepted = [phase_spec["primary_signal"], *phase_spec.get("special_signals", [])]
    if not any(_signal_matches(item, signal) for item in accepted):
        raise EfficiencyError(f"signal is not accepted for phase {phase}: {signal}")
    disposition = "advance"
    if signal.startswith("HARD STOP:"):
        disposition = "stop"
    elif signal.startswith(("NEEDS_CONTEXT", "NEEDS USER INPUT:", "AUTHOR BLOCKED:", "3-STRIKE LIMIT")):
        disposition = "resolve_blocker"
    elif signal.startswith("RELEASE GATE FAILED"):
        disposition = "repair_phase"
    elif signal == "COMPLETE_WITH_CONCERNS":
        disposition = "inspect_concerns"
    elif phase_spec.get("verdict_in_body"):
        try:
            report = _resolve('project', handoff['verdict_report'], {'project': project_root.resolve()})
            if not re.search(r'^Verdict:\s*PASS\s*$', report.read_text(encoding='utf-8-sig'), re.MULTILINE):
                disposition = 'inspect_verdict'
        except (KeyError, OSError, ValueError):
            disposition = 'inspect_verdict'
    if phase == 10 and signal.startswith("LEARNING SKIPPED:"):
        try:
            current = collect_learning_evidence(handoff.get('learning_evidence', {}), project_root)
        except ValueError as exc:
            raise EfficiencyError(f'Learning skip lacks observed eligibility evidence: {exc}') from exc
        if learning_eligibility(current)["action"] != "skip":
            raise EfficiencyError("Learning skip lacks complete eligibility evidence")
    skips: set[int] = set()
    guard = None
    effective_signal = signal
    for transition in phase_spec.get("transitions", []):
        if _signal_matches(transition, signal):
            if phase == 5:
                try:
                    guard = verify_integrate_noop(project_root, handoff.get("integration_report", ".obi/integration-report.md"))
                except (EfficiencyError, OSError) as exc:
                    guard = {"verified": False, "error": str(exc)}
                    effective_signal = phase_spec["primary_signal"]["value"]
                    break
            skips.update(int(value) for value in transition["skip"])
    phases = list(contract["lanes"][lane]["phases"])
    if phase not in phases:
        raise EfficiencyError(f"phase {phase} is not in lane {lane}")
    remaining = [value for value in phases[phases.index(phase) + 1:] if value not in skips]
    result = {"schema_version": 1, "phase": phase, "signal": effective_signal,
              "default_strategy": phase_spec["default_strategy"], "skipped_phases": sorted(skips),
              "next_phase": (remaining[0] if remaining else None) if disposition == "advance" else phase,
              "disposition": disposition, "guard": guard}
    if phase == 4:
        result['review_verdict'] = 'FAIL' if signal.startswith('REVIEW COMPLETE: FAIL') else 'PASS'
        result['required_action'] = 'integrate_findings' if result['review_verdict'] == 'FAIL' else 'triage_review'
    if handoff.get("recovery_request") is not None:
        request = handoff['recovery_request']
        if request.get('phase') != phase or (handoff.get('run_id') is not None and request.get('run_id') != handoff['run_id']):
            raise EfficiencyError('recovery request does not match transition run/phase')
        result["recovery_decision"] = _authoritative_recovery(phase_table, request, project_root)
        if result['recovery_decision']['disposition'] == 'terminal':
            result['disposition'] = 'stop'
            result['next_phase'] = None
    result["action_id"] = sha256_bytes(canonical_json_bytes(result))
    return result


def validate_handoff(packet: dict[str, Any], project_root: Path, runtime_root: Path,
                     contract: dict[str, Any], source_root: Path | None = None) -> dict[str, Any]:
    errors: list[str] = []
    roots = {"project": _root(project_root, "project_root"), "runtime": _root(runtime_root, "runtime_root")}
    if source_root is not None:
        roots['source'] = _root(source_root, 'source_root')
    required = {"schema_version", "run_id", "from_phase", "to_phase", "roots", "acceptance_criteria",
                "settled_facts", "unknowns", "source_artifacts", "independent_read_batches",
                "overflow_actions", "byte_accounting"}
    if set(packet) != required:
        errors.append("handoff has missing or unexpected fields")
    if not isinstance(packet.get("run_id"), str) or not packet["run_id"].strip():
        errors.append("missing run_id")
    for name, minimum in (("from_phase", 0), ("to_phase", 1)):
        if type(packet.get(name)) is not int or not minimum <= packet[name] <= 10:
            errors.append(f"invalid {name}")
    for name in ("acceptance_criteria", "settled_facts", "unknowns", "source_artifacts",
                 "independent_read_batches", "overflow_actions"):
        if not isinstance(packet.get(name), list):
            errors.append(f"{name} must be an array")
    if errors:
        return {"valid": False, "errors": errors}
    if packet.get("schema_version") != 1:
        errors.append("unsupported handoff schema_version")
    if packet.get("roots") != {key: str(value) for key, value in roots.items()}:
        errors.append("handoff roots do not match explicit roots")
    ids = set()
    for item in packet.get("source_artifacts", []):
        try:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise EfficiencyError("source requires a nonempty string id")
            if item["id"] in ids:
                raise EfficiencyError("duplicate source id")
            ids.add(item["id"])
            path = _resolve(item["root"], item["path"], roots)
            if sha256_file(path) != item["sha256"]:
                errors.append(f"stale source hash: {item.get('id')}")
            retrieval = item.get("retrieval", {})
            if retrieval != {"root": item["root"], "path": item["path"], "sha256": item["sha256"]}:
                errors.append(f"unreachable retrieval reference: {item.get('id')}")
            if "excerpt" in item and item["excerpt"] != path.read_text(encoding="utf-8"):
                errors.append(f"excerpt does not match source: {item['id']}")
        except (KeyError, TypeError, OSError, ValueError) as exc:
            errors.append(f"invalid source artifact: {exc}")
    for batch in packet.get("independent_read_batches", []):
        for item in batch.get("results", []):
            if set(("result", "error", "unknown")) - set(item):
                errors.append(f"incomplete independent read result: {item.get('id')}")
            if item.get("unknown") is False and (item.get("result") is None or item.get("error") is not None):
                errors.append(f"contradictory independent read result: {item.get('id')}")
            if item.get('unknown') is True and (item.get('result') is not None or not item.get('error')):
                errors.append(f"contradictory failed read: {item.get('id')}")
            if item.get('unknown') is False:
                try:
                    source = item['source']
                    content = _resolve(source['root'], source['path'], roots).read_bytes()
                    observed = item['result']
                    if sha256_bytes(content) != observed['sha256'] or len(content) != observed['bytes']:
                        errors.append(f"stale independent read: {item.get('id')}")
                    if 'text' in observed and observed['text'] != content.decode('utf-8'):
                        errors.append(f"forged independent read text: {item.get('id')}")
                except (KeyError, OSError, TypeError, ValueError) as exc:
                    errors.append(f"invalid independent read: {exc}")
    expected = json.loads(json.dumps(packet))
    _set_accounting(expected, contract["efficiency"]["handoff_budgets"])
    if packet.get("byte_accounting") != expected.get("byte_accounting"):
        errors.append("handoff byte accounting is stale or caller asserted")
    account = expected["byte_accounting"]
    if account["total"] > account["budgets"]["total_bytes"]:
        errors.append("handoff exceeds total byte budget")
    for name, limit in account["budgets"]["sections"].items():
        if account["sections"][name] > limit:
            errors.append(f"handoff exceeds {name} byte budget")
    return {"valid": not errors, "errors": errors}


def inventory_inputs(contract: dict[str, Any], value: dict[str, Any], project: Path, runtime: Path,
                     source_root: Path | None = None) -> dict[str, Any]:
    """Account for every controlled system/prompt component before dispatch."""
    roots = {"project": _root(project, "project_root"), "runtime": _root(runtime, "runtime_root")}
    if source_root is not None:
        roots['source'] = _root(source_root, 'source_root')
    components = value.get("components")
    if not isinstance(components, list) or not components:
        raise EfficiencyError("inventory requires nonempty components")
    records = []
    for component in components:
        path = _resolve(component["root"], component["path"], roots)
        records.append({**component, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    total = sum(record["bytes"] for record in records)
    limit = contract["efficiency"]["controlled_prompt_bytes"]
    if total > limit:
        raise EfficiencyError(f"controlled prompt exceeds budget: {total} > {limit}; reduce excerpts or split scope")
    return {"schema_version": 1, "components": records, "controlled_input_bytes": total,
            "limit_bytes": limit, "provider_input_tokens": None, "uncontrolled_context": "unknown"}


def _read_input(path: str) -> dict[str, Any]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise EfficiencyError("input must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("route", "handoff", "transition", "learning", "validate", "inventory"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, help="Explicit Obi source checkout for source contracts")
    parser.add_argument("--phase-table", type=Path, required=True)
    parser.add_argument("--input", required=True, help="JSON file, or - for stdin")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract = load_contract(args.project_root, args.runtime_root, args.phase_table)
        value = _read_input(args.input)
        if args.operation == "route":
            result = route_effort(contract, value)
        elif args.operation == "handoff":
            result = build_handoff(contract, value, args.project_root, args.runtime_root, args.source_root)
        elif args.operation == "transition":
            result = select_transition(contract, value["lane"], int(value["phase"]), value["signal"],
                                       value.get("handoff", {}), args.project_root, args.phase_table)
        elif args.operation == "learning":
            evidence = collect_learning_evidence(value, args.project_root)
            result = {**learning_eligibility(evidence), "evidence": evidence}
        elif args.operation == "inventory":
            result = inventory_inputs(contract, value, args.project_root, args.runtime_root, args.source_root)
        else:
            result = validate_handoff(value, args.project_root, args.runtime_root, contract, args.source_root)
            if not result["valid"]:
                raise EfficiencyError("; ".join(result["errors"]))
        if args.output:
            atomic_write_json(args.output, result, compact=True)
        else:
            sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
        return 0
    except (EfficiencyError, RecoveryError, KeyError, TypeError, ValueError, OSError) as exc:
        sys.stderr.buffer.write(canonical_json_bytes({"error": str(exc)}) + b"\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os
import json
from pathlib import Path

import pytest

from tools.peer_review.adapters import (
    CLAUDE_MODEL,
    CODEX_MODEL,
    RESULT_CLAUDE_JSON,
    AdapterSpec,
    ProviderResultError,
    claude_adapter,
    codex_adapter,
    materialize_result,
    provider_environment,
)
from tools.peer_review.packet import build_capsule

from .test_packet import make_repo, request
from .test_validation import model_result


def test_codex_command_ignores_user_and_project_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "adapter-1", runtime_root=tmp_path / "runtime")
    fake = tmp_path / ("codex.cmd" if os.name == "nt" else "codex")
    fake.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="ascii")
    monkeypatch.setenv("PATH", str(tmp_path))
    spec = codex_adapter(
        capsule,
        schema_path=tmp_path / "schema.json",
        raw_result_path=tmp_path / "raw.json",
    )
    joined = " ".join(spec.command)
    assert "--ignore-user-config" in spec.command
    assert "--ignore-rules" in spec.command
    assert "--ephemeral" in spec.command
    assert "--output-schema" in spec.command
    assert "--output-last-message" in spec.command
    assert "--profile" not in spec.command
    # The configured managed requirements reject approval_policy="never" and exec mode
    # then refuses every tool call, so the peer reads nothing (K15). --approve-for-me
    # is the only working exec configuration and is exclusive with -s.
    assert "approval_policy=\"never\"" not in spec.command
    assert "--approve-for-me" in spec.command
    assert "-s" not in spec.command
    assert "sandbox_mode=\"read-only\"" in spec.command
    assert "model_reasoning_effort=\"high\"" in spec.command
    assert "web_search=\"disabled\"" in spec.command
    assert CODEX_MODEL in spec.command
    assert str(capsule.root) in spec.command
    assert str(repo) not in joined


def test_provider_environment_drops_unrelated_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OBI_TEST_SECRET", "do-not-forward")
    monkeypatch.setenv("GITLAB_TOKEN", "do-not-forward")
    environment = provider_environment()
    assert "OBI_TEST_SECRET" not in environment
    assert "GITLAB_TOKEN" not in environment
    assert environment.get("PATH") == os.environ.get("PATH")


def test_provider_environment_matches_windows_names_case_insensitively(monkeypatch) -> None:
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    environment = {key.upper(): value for key, value in provider_environment().items()}
    assert environment["SYSTEMROOT"] == r"C:\Windows"
    assert environment["COMSPEC"] == r"C:\Windows\System32\cmd.exe"


def test_claude_command_disables_customizations_network_tools_and_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    repo = make_repo(tmp_path)
    capsule = build_capsule(repo, request(), "adapter-claude", runtime_root=tmp_path / "runtime")
    fake = tmp_path / ("claude.cmd" if os.name == "nt" else "claude")
    fake.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n", encoding="ascii")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))

    spec = claude_adapter(
        capsule,
        schema_path=schema_path,
        raw_result_path=tmp_path / "raw.json",
    )

    assert spec.provider == "claude"
    assert spec.result_mode == RESULT_CLAUDE_JSON
    assert "--safe-mode" in spec.command
    assert "--tools" in spec.command
    assert spec.command[spec.command.index("--tools") + 1] == "Read,Grep,Glob"
    assert "--strict-mcp-config" in spec.command
    mcp_path = Path(spec.command[spec.command.index("--mcp-config") + 1])
    assert json.loads(mcp_path.read_text(encoding="utf-8")) == {"mcpServers": {}}
    assert "--disable-slash-commands" in spec.command
    assert spec.command[spec.command.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in spec.command
    assert spec.command[spec.command.index("--setting-sources") + 1] == ""
    assert "--no-chrome" in spec.command
    assert spec.command[spec.command.index("--model") + 1] == CLAUDE_MODEL
    assert spec.command[spec.command.index("--effort") + 1] == "high"
    assert spec.command[spec.command.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in spec.command
    assert json.loads(spec.command[spec.command.index("--json-schema") + 1]) == schema
    assert "--add-dir" not in spec.command
    assert "--max-turns" not in spec.command
    assert str(repo) not in " ".join(spec.command)


def test_provider_environment_keeps_claude_auth_but_not_arbitrary_tokens(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "required-by-provider")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path("C:/safe-config")))
    monkeypatch.setenv("RANDOM_SERVICE_TOKEN", "do-not-forward")
    environment = provider_environment()
    assert environment["ANTHROPIC_API_KEY"] == "required-by-provider"
    assert "CLAUDE_CONFIG_DIR" in environment
    assert "RANDOM_SERVICE_TOKEN" not in environment


def test_claude_structured_output_envelope_materializes_common_raw_result(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    raw = tmp_path / "raw.json"
    value = model_result()
    value["provider"] = "claude"
    events.write_text(
        json.dumps({"is_error": False, "structured_output": value}), encoding="utf-8"
    )
    spec = AdapterSpec("claude", [], {}, "fake", RESULT_CLAUDE_JSON)
    materialize_result(spec, events, raw)
    assert json.loads(raw.read_text(encoding="utf-8")) == value


def test_claude_envelope_without_structured_result_fails_closed(tmp_path: Path) -> None:
    events = tmp_path / "events.json"
    events.write_text(json.dumps({"is_error": False, "result": "prose"}), encoding="utf-8")
    spec = AdapterSpec("claude", [], {}, "fake", RESULT_CLAUDE_JSON)
    with pytest.raises(ProviderResultError, match="terminal result event"):
        materialize_result(spec, events, tmp_path / "raw.json")

"""Provider-specific commands behind a semantic, non-extensible boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .io_utils import atomic_write_text, read_json_file
from .packet import Capsule

ADAPTER_VERSION = "1"
CODEX_MODEL = "gpt-5.6-sol"
CLAUDE_MODEL = "fable"
RESULT_PROVIDER_FILE = "provider_file"
RESULT_CLAUDE_JSON = "claude_stream_json_stdout"
EVENT_STREAM_MAX_BYTES = 32 * 1024 * 1024


class ProviderUnavailable(RuntimeError):
    pass


class ProviderResultError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdapterSpec:
    provider: str
    command: Sequence[str]
    environment: Mapping[str, str]
    cli_version: str
    result_mode: str = RESULT_PROVIDER_FILE


def _command_prefix(name: str) -> list[str]:
    resolved = shutil.which(name)
    if not resolved:
        raise ProviderUnavailable(f"{name} is not installed or not on PATH")
    suffix = Path(resolved).suffix.lower()
    if os.name == "nt" and suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("ComSpec") or str(
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
        )
        return [comspec, "/d", "/s", "/c", resolved]
    if os.name == "nt" and suffix == ".ps1":
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        return [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", resolved]
    return [resolved]


def _version(prefix: Sequence[str]) -> str:
    try:
        proc = subprocess.run(
            [*prefix, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=10,
        )
        return proc.stdout.decode("utf-8", errors="replace").strip()[:200] or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def provider_environment() -> dict[str, str]:
    # Keep authentication/transport variables required by the provider process,
    # while dropping arbitrary harness state. Model-generated tools remain under
    # the provider's own no-network/read-only capability policy.
    allowed = {
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "WINDIR",
        "ComSpec",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "CODEX_HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_GIT_BASH_PATH",
        "CLAUDE_CODE_USE_POWERSHELL_TOOL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    }
    # Environment names are case-insensitive on Windows, but Python preserves
    # the casing supplied by the parent process. This host exposes SYSTEMROOT
    # and COMSPEC in uppercase; exact matching silently removed both and made
    # npm-generated provider shims exit before emitting diagnostics.
    allowed_folded = {key.upper() for key in allowed}
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed_folded
    }


def build_prompt(capsule: Capsule, provider: str) -> str:
    request = read_json_file(capsule.request_path, max_bytes=256 * 1024)
    criteria = "\n".join(f"- {item}" for item in request["acceptance_criteria"])
    focus = "\n".join(f"- {item}" for item in request.get("focus", [])) or "- No additional focus"
    return f"""You are the {provider} peer in an adversarial code review.

The only review surface is this capsule. Read REVIEW_INSTRUCTIONS.md,
request.json, scope-manifest.json, CHANGES.diff when present, and files under
repo/, evidence/, or attachments/. Do not access the network, MCP, remote
repositories, parent directories, absolute paths, or another checkout.

This review is READ-ONLY. Do not create, edit, move, or delete any file,
including inside the capsule, and do not run any command that writes. Your
sandbox may permit a write; the harness hashes every capsule file before and
after the run and discards the entire review as `capsule_modified` if anything
changed, so a single write destroys your own output.

Objective:
{request['objective']}

Acceptance criteria:
{criteria}

Focus:
{focus}

Return exactly one object matching the enforced schema. Set provider to
"{provider}". Define each exact manifest path and in-bounds line range once in
the top-level citations array, then reference one or more citation IDs from each
finding's evidence_ids. An empty citations/findings array is a valid pass.
Prefer no finding over an unsupported claim. Converge within the harness
deadline.
"""


def codex_adapter(
    capsule: Capsule,
    *,
    schema_path: Path,
    raw_result_path: Path,
) -> AdapterSpec:
    prefix = _command_prefix("codex")
    command = [
        *prefix,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(raw_result_path),
        "-C",
        str(capsule.root),
        "--skip-git-repo-check",
        # The configured managed Codex requirements forbid approval_policy="never" and
        # downgrade it to UnlessTrusted; in exec mode every shell call is then
        # rejected ("command execution approval is not supported in exec mode")
        # and the review silently completes having read nothing. --approve-for-me
        # is the only exec configuration that lets the peer read the capsule, and
        # it is mutually exclusive with -s. sandbox_mode is advisory only under
        # it -- actual write protection is the capsule hash check in the runner.
        "--approve-for-me",
        "-m",
        CODEX_MODEL,
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        'web_search="disabled"',
        "-",
    ]
    return AdapterSpec("codex", command, provider_environment(), _version(prefix))


def claude_adapter(
    capsule: Capsule,
    *,
    schema_path: Path,
    raw_result_path: Path,
) -> AdapterSpec:
    del raw_result_path  # Claude emits its structured result in the JSON stdout envelope.
    prefix = _command_prefix("claude")
    schema = read_json_file(schema_path, max_bytes=1024 * 1024)
    schema_arg = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    empty_mcp = capsule.root / "empty-mcp.json"
    empty_mcp.write_text('{"mcpServers":{}}\n', encoding="utf-8")
    command = [
        *prefix,
        "--safe-mode",
        "-p",
        "--tools",
        "Read,Grep,Glob",
        "--strict-mcp-config",
        "--mcp-config",
        str(empty_mcp),
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--no-chrome",
        "--model",
        CLAUDE_MODEL,
        "--effort",
        "high",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        schema_arg,
    ]
    return AdapterSpec(
        "claude",
        command,
        provider_environment(),
        _version(prefix),
        RESULT_CLAUDE_JSON,
    )


def materialize_result(spec: AdapterSpec, events_path: Path, raw_result_path: Path) -> None:
    """Write the provider's schema object to the common raw-result path."""
    if spec.result_mode == RESULT_PROVIDER_FILE:
        if not raw_result_path.is_file():
            raise ProviderResultError("provider completed without a result file")
        return
    if spec.result_mode != RESULT_CLAUDE_JSON:
        raise ProviderResultError(f"unknown adapter result mode: {spec.result_mode}")

    try:
        if events_path.stat().st_size > EVENT_STREAM_MAX_BYTES:
            raise ProviderResultError("Claude JSON stream exceeds the broker byte limit")
        stream = events_path.open(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ProviderResultError(f"Claude JSON stream is unreadable: {exc}") from exc
    envelope = None
    try:
        with stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ProviderResultError(
                        f"Claude JSON stream has malformed event at line {line_number}: {exc}"
                    ) from exc
                if not isinstance(event, dict):
                    raise ProviderResultError(
                        f"Claude JSON stream event {line_number} is not an object"
                    )
                if event.get("type") == "result" or "structured_output" in event:
                    envelope = event
    except UnicodeError as exc:
        raise ProviderResultError(f"Claude JSON stream is unreadable: {exc}") from exc
    if envelope is None:
        raise ProviderResultError("Claude JSON stream has no terminal result event")
    if envelope.get("is_error") is True:
        raise ProviderResultError("Claude reported an error result")

    candidate = envelope.get("structured_output")
    if candidate is None:
        candidate = envelope.get("result")
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise ProviderResultError(
                    "Claude envelope has no structured_output object"
                ) from exc
    if not isinstance(candidate, dict):
        raise ProviderResultError("Claude envelope has no structured_output object")
    atomic_write_text(
        raw_result_path,
        json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n",
    )

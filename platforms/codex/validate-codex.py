#!/usr/bin/env python3
"""Codex platform configuration health check.

Validates that the Obi Wag Codex target is structurally complete and avoids
Claude-only assumptions in the Codex runtime contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CODEX_DIR = Path(__file__).resolve().parent

REQUIRED_SIGNALS = [
    "DISCOVERY COMPLETE",
    "AUTHOR COMPLETE",
    "SIMPLIFY COMPLETE",
    "REVIEW COMPLETE: PASS",
    "INTEGRATE COMPLETE",
    "RE-REVIEW COMPLETE",
    "README COMPLETE",
    "README REVIEW COMPLETE",
    "RELEASE GATE PASSED",
    "LEARNING CAPTURED",
]


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"[OK] {message}")

    def fail(self, message: str) -> None:
        print(f"[FAIL] {message}")
        self.failures.append(message)

    def warn(self, message: str) -> None:
        print(f"[WARN] {message}")
        self.warnings.append(message)


def check_file(check: Check, path: Path, label: str) -> str:
    if not path.is_file():
        check.fail(f"{label} missing: {path}")
        return ""
    check.ok(f"{label}: {path.name}")
    return path.read_text(encoding="utf-8", errors="ignore")


def check_agents_md(check: Check) -> None:
    content = check_file(check, CODEX_DIR / "AGENTS.md", "Codex AGENTS.md")
    if not content:
        return

    for marker in [
        "10-phase lifecycle",
        "zero-hallucination",
        "OBI_PLATFORM=codex",
        "slashless prompt aliases",
    ]:
        if marker.lower() in content.lower():
            check.ok(f"AGENTS.md contains {marker}")
        else:
            check.fail(f"AGENTS.md missing {marker}")

    for signal in REQUIRED_SIGNALS:
        if signal in content:
            check.ok(f"AGENTS.md contains signal {signal}")
        else:
            check.fail(f"AGENTS.md missing signal {signal}")

    forbidden = ["~/.claude/commands", "~/.github/agents"]
    for marker in forbidden:
        if marker in content:
            check.fail(f"AGENTS.md leaks platform-specific path {marker}")


def check_hooks_json(check: Check) -> None:
    hooks_path = CODEX_DIR / "hooks.json"
    raw = check_file(check, hooks_path, "Codex hooks.json")
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        check.fail(f"hooks.json invalid JSON: {exc}")
        return

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        check.fail("hooks.json missing top-level hooks object")
        return

    for hook_name in ["SessionStart", "PreToolUse", "PostToolUse", "Stop"]:
        entries = hooks.get(hook_name)
        if isinstance(entries, list) and entries:
            check.ok(f"hooks.json defines {hook_name}")
        else:
            check.fail(f"hooks.json missing {hook_name}")

    raw_lower = raw.lower()
    for marker in ["obi_platform=codex", "hook_wrapper_codex.cmd", "if not defined obi_home"]:
        if marker in raw_lower:
            check.ok(f"hooks.json contains {marker}")
        else:
            check.fail(f"hooks.json missing {marker}")

    if "!obi_home!" in raw_lower or "%obi_home%" in raw_lower:
        check.ok("hooks.json invokes OBI_HOME")
    else:
        check.fail("hooks.json does not invoke OBI_HOME")


def check_repo_support(check: Check) -> None:
    for path in [
        ROOT / "hooks" / "hook_wrapper_codex.cmd",
        ROOT / "hooks" / "core" / "paths.py",
        ROOT / "phases" / "README.md",
        ROOT / "policies" / "zero-hallucination.md",
    ]:
        if path.exists():
            check.ok(f"support file present: {path.relative_to(ROOT)}")
        else:
            check.fail(f"support file missing: {path.relative_to(ROOT)}")

    paths_py = (ROOT / "hooks" / "core" / "paths.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    if "OBI_PLATFORM" in paths_py and "'.codex'" in paths_py:
        check.ok("paths.py supports Codex root")
    else:
        check.fail("paths.py does not support Codex root")


def main() -> int:
    check = Check()
    print("=== Codex Platform Health Check ===")
    check_agents_md(check)
    check_hooks_json(check)
    check_repo_support(check)

    print()
    print(f"Checks failed: {len(check.failures)}")
    print(f"Warnings: {len(check.warnings)}")
    return 1 if check.failures else 0


if __name__ == "__main__":
    sys.exit(main())

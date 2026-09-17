"""Tests for tools/codex_catch_stats.py -- the Codex catch analytics CLI."""

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Make the tools/ directory importable.
TOOLS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOLS_DIR))

# hook_stats prepends hooks/ to sys.path on import; do the same here so the
# test process can import core modules directly for fixture setup.
HOOKS_DIR = TOOLS_DIR.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import codex_catch_stats  # noqa: E402


def _write_catches(path: Path, entries):
    """Write fixture JSONL entries to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _run_cli(argv, catches_path):
    """Run codex_catch_stats.main() with the given argv and a redirected catches path.

    Returns (exit_code, stdout_text).
    """
    buf = io.StringIO()
    with patch.object(codex_catch_stats, "get_catches_path", return_value=catches_path), \
         redirect_stdout(buf):
        code = codex_catch_stats.main(argv)
    return code, buf.getvalue()


def _make_catch(
    category="missed-edge-case",
    severity="high",
    disputed=False,
    dispute_resolution=None,
    ts=None,
    ref="TEST-1",
    phase="review",
    peer_provider=None,
):
    entry = {
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "repo": "test-repo",
        "ref": ref,
        "phase": phase,
        "category": category,
        "severity": severity,
        "summary": f"Test catch for {category}",
        "disputed": disputed,
        "dispute_resolution": dispute_resolution,
    }
    if peer_provider is not None:
        entry["peer_provider"] = peer_provider
    return entry


def _make_attempt(
    pass_id="pass-1",
    duration_ms=100,
    finding_count=0,
    accepted_count=0,
    ref="RUN-1",
    phase="plan",
    peer_provider="codex",
):
    return {
        "record_type": "attempt",
        "ts": datetime.now(timezone.utc).isoformat(),
        "repo": "test-repo",
        "ref": ref,
        "phase": phase,
        "peer_provider": peer_provider,
        "pass_id": pass_id,
        "duration_ms": duration_ms,
        "finding_count": finding_count,
        "accepted_count": accepted_count,
        "outcome": "zero_findings" if finding_count == 0 else "findings",
    }


class TestCatchStatsCli:
    def test_empty_file_table_output(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        code, out = _run_cli([], catches_path)
        assert code == 0
        assert "No Codex catches recorded" in out

    def test_missing_file_table_output(self, tmp_path):
        catches_path = tmp_path / "nonexistent" / "codex-catches.jsonl"
        code, out = _run_cli([], catches_path)
        assert code == 0
        assert "No Codex catches recorded" in out

    def test_empty_file_json_output(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        code, out = _run_cli(["--json"], catches_path)
        assert code == 0
        payload = json.loads(out)
        assert payload["total"] == 0
        assert payload["confirmed"] == 0

    def test_table_output_with_data(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(category="missed-edge-case", ref="T-1"),
            _make_catch(category="test-gap", ref="T-2"),
            _make_catch(category="security", severity="medium", ref="T-3"),
        ])
        code, out = _run_cli([], catches_path)
        assert code == 0
        assert "Total: 3" in out
        assert "Confirmed: 3" in out
        assert "missed-edge-case" in out
        assert "test-gap" in out
        assert "security" in out

    def test_json_output_shape(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="T-1"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        assert code == 0
        payload = json.loads(out)
        for key in (
            "total", "confirmed", "by_category", "by_severity",
            "by_phase", "disputed", "codex_right", "claude_right",
            "unresolved", "dispute_win_rate", "trend_days", "trend_count",
            "attempts", "zero_finding_attempts", "attempts_with_accepted",
            "attempt_acceptance_rate", "accepted_per_attempt", "pass_ids",
            "attempt_duration_ms",
        ):
            assert key in payload, f"missing key {key}"

    def test_claude_peer_entries_do_not_pollute_codex_analytics(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="OLD-CODEX"),
            _make_catch(ref="NEW-CODEX", peer_provider="codex"),
            _make_catch(ref="CLAUDE", peer_provider="claude"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        assert code == 0
        payload = json.loads(out)
        assert payload["total"] == 2

    def test_category_counts(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(category="missed-edge-case", ref="T-1"),
            _make_catch(category="missed-edge-case", ref="T-2"),
            _make_catch(category="test-gap", ref="T-3"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["by_category"]["missed-edge-case"] == 2
        assert payload["by_category"]["test-gap"] == 1

    def test_severity_counts(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(severity="high", ref="T-1"),
            _make_catch(severity="medium", ref="T-2"),
            _make_catch(severity="low", ref="T-3"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["by_severity"]["high"] == 1
        assert payload["by_severity"]["medium"] == 1
        assert payload["by_severity"]["low"] == 1

    def test_phase_breakdown(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(phase="review", ref="T-1"),
            _make_catch(phase="review", ref="T-2"),
            _make_catch(phase="plan", ref="T-3"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["by_phase"]["review"] == 2
        assert payload["by_phase"]["plan"] == 1

    def test_dispute_win_rate(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(disputed=True, dispute_resolution="codex-right", ref="T-1"),
            _make_catch(disputed=True, dispute_resolution="codex-right", ref="T-2"),
            _make_catch(disputed=True, dispute_resolution="claude-right", ref="T-3"),
            _make_catch(disputed=False, ref="T-4"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        # 2 codex-right out of 3 resolved = 0.67
        assert payload["dispute_win_rate"] == 0.67
        assert payload["codex_right"] == 2
        assert payload["claude_right"] == 1

    def test_trend_days_window(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        now = datetime.now(timezone.utc)
        _write_catches(catches_path, [
            _make_catch(ts=(now - timedelta(days=10)).isoformat(), ref="T-1"),
            _make_catch(ts=(now - timedelta(days=100)).isoformat(), ref="T-2"),
        ])
        # Default 90-day window: only the 10-day-old catch is in trend
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["trend_count"] == 1
        assert payload["trend_days"] == 90

        # Wider window
        code, out = _run_cli(["--json", "--days", "180"], catches_path)
        payload = json.loads(out)
        assert payload["trend_count"] == 2

    def test_deduplication_by_ref_category(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="T-1", category="test-gap", disputed=False),
            # Same ref+category, later entry should win
            _make_catch(ref="T-1", category="test-gap", disputed=True,
                        dispute_resolution="claude-right"),
        ])
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["total"] == 1
        # The later entry (disputed, claude-right) is NOT confirmed
        assert payload["confirmed"] == 0

    def test_malformed_line_tolerated(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        catches_path.parent.mkdir(parents=True, exist_ok=True)
        with open(catches_path, "w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps(_make_catch(ref="T-1")) + "\n")
            f.write("\n")  # blank line
            f.write("{truncated\n")
            f.write(json.dumps(_make_catch(ref="T-2")) + "\n")
        code, out = _run_cli(["--json"], catches_path)
        assert code == 0
        payload = json.loads(out)
        assert payload["total"] == 2

    def test_confirmed_excludes_claude_right(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="T-1", disputed=False),  # confirmed
            _make_catch(ref="T-2", disputed=True,
                        dispute_resolution="codex-right"),  # confirmed
            _make_catch(ref="T-3", disputed=True,
                        dispute_resolution="claude-right"),  # NOT confirmed
        ])
        code, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["total"] == 3
        assert payload["confirmed"] == 2

    def test_legacy_findings_remain_findings_without_attempt_rows(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [_make_catch(ref="LEGACY")])
        _, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["total"] == 1
        assert payload["attempts"] == 0
        assert payload["accepted_per_attempt"] is None

    def test_mixed_attempts_report_zero_findings_acceptance_and_duration(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [
            _make_catch(ref="LEGACY"),
            _make_attempt(pass_id="p-zero", duration_ms=100),
            _make_attempt(
                pass_id="p-findings", duration_ms=300,
                finding_count=2, accepted_count=1, ref="RUN-2",
            ),
            _make_attempt(pass_id="claude", peer_provider="claude"),
        ])
        _, out = _run_cli(["--json"], catches_path)
        payload = json.loads(out)
        assert payload["total"] == 1
        assert payload["attempts"] == 2
        assert payload["zero_finding_attempts"] == 1
        assert payload["attempts_with_accepted"] == 1
        assert payload["attempt_acceptance_rate"] == 0.5
        assert payload["accepted_per_attempt"] == 0.5
        assert payload["pass_ids"] == ["p-findings", "p-zero"]
        assert payload["attempt_duration_ms"] == {
            "count": 2,
            "median": 200.0,
            "total": 400,
        }

    def test_attempt_only_table_is_not_reported_as_empty(self, tmp_path):
        catches_path = tmp_path / ".obi" / "codex-catches.jsonl"
        _write_catches(catches_path, [_make_attempt()])
        code, out = _run_cli([], catches_path)
        assert code == 0
        assert "Attempts: 1" in out
        assert "Pass IDs: pass-1" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Tests for core.worker_guard (OPT-23 OBI_WORKER hook-isolation guard)."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOOKS_DIR))

from core.worker_guard import is_worker, exit_if_worker


class TestIsWorker:
    def test_false_when_unset(self, monkeypatch):
        monkeypatch.delenv('OBI_WORKER', raising=False)
        assert is_worker() is False

    @pytest.mark.parametrize('val', ['', '0', 'false', 'False', 'no', 'off', '  '])
    def test_false_for_falsey_values(self, monkeypatch, val):
        monkeypatch.setenv('OBI_WORKER', val)
        assert is_worker() is False

    @pytest.mark.parametrize('val', ['1', 'true', 'TRUE', 'yes', 'on'])
    def test_true_for_truthy_values(self, monkeypatch, val):
        monkeypatch.setenv('OBI_WORKER', val)
        assert is_worker() is True


class TestExitIfWorker:
    def test_exits_zero_and_emits_empty_json_in_worker(self, monkeypatch, capsys):
        monkeypatch.setenv('OBI_WORKER', '1')
        with pytest.raises(SystemExit) as exc:
            exit_if_worker()
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == '{}'

    def test_no_emit_when_emit_false(self, monkeypatch, capsys):
        monkeypatch.setenv('OBI_WORKER', '1')
        with pytest.raises(SystemExit):
            exit_if_worker(emit=False)
        assert capsys.readouterr().out == ''

    def test_noop_in_normal_session(self, monkeypatch, capsys):
        # Critical: a normal (non-worker) session must NOT be suppressed.
        monkeypatch.delenv('OBI_WORKER', raising=False)
        exit_if_worker()  # must return without raising
        assert capsys.readouterr().out == ''

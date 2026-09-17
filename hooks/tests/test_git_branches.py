"""Tests for remote default-branch resolution."""

import subprocess
import sys
from pathlib import Path


HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.git_branches import get_remote_default_branch


def test_remote_head_selects_main(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="origin/main\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_remote_default_branch(tmp_path) == "main"
    assert calls[0][0][-4:] == [
        "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"
    ]


def test_missing_remote_head_falls_back_to_main(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="missing")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_remote_default_branch(tmp_path) == "main"


def test_malformed_remote_head_falls_back_to_main(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="refs/heads/not-remote\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert get_remote_default_branch(tmp_path) == "main"

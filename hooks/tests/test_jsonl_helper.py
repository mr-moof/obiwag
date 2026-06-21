"""Unit tests for core.jsonl_helper."""

import json
import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports (matches sibling test files)
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.jsonl_helper import append_jsonl


class TestAppendJsonl:
    """Tests for append_jsonl."""

    def test_creates_file_if_missing(self, tmp_path):
        target = tmp_path / "new.jsonl"
        assert not target.exists()

        append_jsonl(target, {"a": 1})

        assert target.exists()
        assert target.read_text(encoding="utf-8") == '{"a": 1}\n'

    def test_creates_parent_dirs_if_missing(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "log.jsonl"
        assert not target.parent.exists()

        append_jsonl(target, {"k": "v"})

        assert target.exists()
        assert json.loads(target.read_text(encoding="utf-8").strip()) == {"k": "v"}

    def test_appends_without_rewriting_existing_lines(self, tmp_path):
        """Each call should add exactly one line. Existing content untouched."""
        target = tmp_path / "log.jsonl"
        target.write_text('{"existing": true}\n', encoding="utf-8")
        size_before = target.stat().st_size

        append_jsonl(target, {"n": 1})
        size_after_one = target.stat().st_size
        append_jsonl(target, {"n": 2})
        size_after_two = target.stat().st_size

        lines = target.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0]) == {"existing": True}
        assert json.loads(lines[1]) == {"n": 1}
        assert json.loads(lines[2]) == {"n": 2}

        # File strictly grows; we never truncate-and-rewrite.
        assert size_after_one > size_before
        assert size_after_two > size_after_one

    def test_accepts_str_and_pathlike(self, tmp_path):
        target = tmp_path / "log.jsonl"

        append_jsonl(str(target), {"form": "str"})
        append_jsonl(target, {"form": "path"})

        lines = target.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["form"] for line in lines] == ["str", "path"]

    def test_writes_single_trailing_newline(self, tmp_path):
        target = tmp_path / "log.jsonl"
        append_jsonl(target, {"x": 1})
        content = target.read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert not content.endswith("\n\n")

    def test_non_serializable_raises(self, tmp_path):
        target = tmp_path / "log.jsonl"
        with pytest.raises(TypeError):
            append_jsonl(target, {"bad": object()})

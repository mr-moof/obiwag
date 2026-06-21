"""Unit tests for drift_detector module."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


# Add parent directory to path for imports
HOOKS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(HOOKS_DIR))

from core.drift_detector import (
    file_hash,
    get_source_repo_path,
    detect_drift,
    format_drift_summary,
    load_manifest,
    _should_skip,
)


class TestFileHash:
    """Tests for file_hash function."""

    def test_hash_existing_file(self, tmp_path):
        """Hash of an existing file returns a hex string."""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = file_hash(str(f))
        assert result is not None
        assert len(result) == 64  # SHA256 hex length

    def test_hash_missing_file(self):
        """Hash of a nonexistent file returns None."""
        assert file_hash("/nonexistent/path/file.txt") is None

    def test_same_content_same_hash(self, tmp_path):
        """Two files with identical content produce the same hash."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("identical")
        f2.write_text("identical")
        assert file_hash(str(f1)) == file_hash(str(f2))

    def test_different_content_different_hash(self, tmp_path):
        """Two files with different content produce different hashes."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A")
        f2.write_text("content B")
        assert file_hash(str(f1)) != file_hash(str(f2))


class TestShouldSkip:
    """Tests for _should_skip helper."""

    def test_skip_pycache(self):
        assert _should_skip("__pycache__/foo.pyc") is True

    def test_skip_pyc_extension(self):
        assert _should_skip("module.pyc") is True

    def test_skip_obi_dir(self):
        assert _should_skip(".obi/state/foo.json") is True

    def test_allow_normal_file(self):
        assert _should_skip("commands/obi.md") is False

    def test_allow_python_file(self):
        assert _should_skip("core/drift_detector.py") is False

    def test_skip_claude_md(self):
        assert _should_skip("CLAUDE.md") is True


class TestGetSourceRepoPath:
    """Tests for get_source_repo_path function."""

    def test_env_var_override(self, tmp_path):
        """OBIWAG_SOURCE env var takes priority."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        with patch.dict(os.environ, {'OBIWAG_SOURCE': str(tmp_path)}):
            result = get_source_repo_path()
            assert result == str(tmp_path)

    def test_env_var_invalid_path(self, tmp_path):
        """Invalid OBIWAG_SOURCE falls through to standard location."""
        with patch.dict(os.environ, {'OBIWAG_SOURCE': '/nonexistent/path'}):
            # Will fall through to standard location check
            # Result depends on whether standard location exists
            result = get_source_repo_path()
            # Just verify it doesn't crash
            assert result is None or isinstance(result, str)

    def test_returns_none_when_no_repo(self):
        """Returns None when source repo can't be found."""
        with patch.dict(os.environ, {'OBIWAG_SOURCE': ''}, clear=False):
            with patch('core.drift_detector.os.path.isdir', return_value=False):
                result = get_source_repo_path()
                assert result is None


class TestLoadManifest:
    """Tests for load_manifest function."""

    def test_loads_valid_manifest(self, tmp_path):
        """Loads and returns mappings from a valid manifest."""
        obi_dir = tmp_path / ".obi"
        obi_dir.mkdir()
        manifest = {
            "version": "1.0",
            "deployed_at": "2026-02-18T00:00:00Z",
            "mappings": {
                "commands/discovery.md": "phases/01-discovery/command.md",
                "hooks/stop.py": "hooks/stop.py",
            }
        }
        (obi_dir / "deployment-manifest.json").write_text(json.dumps(manifest))

        with patch('core.drift_detector.get_deployed_root', return_value=str(tmp_path)):
            result = load_manifest()
            assert result is not None
            assert result["commands/discovery.md"] == "phases/01-discovery/command.md"

    def test_returns_none_when_missing(self, tmp_path):
        """Returns None when manifest file doesn't exist."""
        with patch('core.drift_detector.get_deployed_root', return_value=str(tmp_path)):
            assert load_manifest() is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        """Returns None when manifest is corrupt."""
        obi_dir = tmp_path / ".obi"
        obi_dir.mkdir()
        (obi_dir / "deployment-manifest.json").write_text("not json")

        with patch('core.drift_detector.get_deployed_root', return_value=str(tmp_path)):
            assert load_manifest() is None

    def test_returns_none_on_empty_mappings(self, tmp_path):
        """Returns None when mappings dict is empty."""
        obi_dir = tmp_path / ".obi"
        obi_dir.mkdir()
        manifest = {"version": "1.0", "mappings": {}}
        (obi_dir / "deployment-manifest.json").write_text(json.dumps(manifest))

        with patch('core.drift_detector.get_deployed_root', return_value=str(tmp_path)):
            assert load_manifest() is None


class TestDetectDrift:
    """Tests for detect_drift with mock filesystem."""

    def _write_manifest(self, deployed_root, mappings):
        """Helper to write a manifest file for tests."""
        obi_dir = Path(deployed_root) / ".obi"
        obi_dir.mkdir(parents=True, exist_ok=True)
        manifest = {"version": "1.0", "mappings": mappings}
        (obi_dir / "deployment-manifest.json").write_text(json.dumps(manifest))

    def test_identical_files_with_manifest(self, tmp_path):
        """No drift when files are identical (manifest mode)."""
        source = tmp_path / "source"
        deployed = tmp_path / "deployed"

        # Source uses flattened structure
        (source / "phases" / "01-discovery").mkdir(parents=True)
        (source / "phases" / "01-discovery" / "command.md").write_text("# Discovery")

        # Deployed uses commands/ dir
        (deployed / "commands").mkdir(parents=True)
        (deployed / "commands" / "discovery.md").write_text("# Discovery")

        self._write_manifest(str(deployed), {
            "commands/discovery.md": "phases/01-discovery/command.md"
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['total_drifted'] == 0
                assert result['checked'] == 1

    def test_modified_file_with_manifest(self, tmp_path):
        """Detects modified files via manifest mapping."""
        source = tmp_path / "source"
        deployed = tmp_path / "deployed"

        (source / "orchestration").mkdir(parents=True)
        (source / "orchestration" / "obi.md").write_text("# Original")

        (deployed / "commands").mkdir(parents=True)
        (deployed / "commands" / "obi.md").write_text("# Modified version")

        self._write_manifest(str(deployed), {
            "commands/obi.md": "orchestration/obi.md"
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['total_drifted'] == 1
                assert result['drifted'][0]['status'] == 'modified'
                assert result['drifted'][0]['relative'] == 'commands/obi.md'

    def test_source_missing_with_manifest(self, tmp_path):
        """Detects source-missing when manifest points to nonexistent source."""
        source = tmp_path / "source"
        source.mkdir()
        deployed = tmp_path / "deployed"

        (deployed / "commands").mkdir(parents=True)
        (deployed / "commands" / "new.md").write_text("# New")

        self._write_manifest(str(deployed), {
            "commands/new.md": "phases/99-new/command.md"
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['total_drifted'] == 1
                assert result['drifted'][0]['status'] == 'source_missing'

    def test_graceful_on_missing_source_repo(self):
        """Returns empty result when source repo doesn't exist."""
        with patch('core.drift_detector.get_source_repo_path', return_value=None):
            result = detect_drift()
            assert result['total_drifted'] == 0
            assert result['checked'] == 0
            assert result['source_path'] is None

    def test_legacy_fallback_without_manifest(self, tmp_path):
        """Falls back to legacy dir mappings when no manifest exists."""
        source = tmp_path / "source"
        deployed = tmp_path / "deployed"

        # hooks (in legacy mappings)
        (source / "hooks" / "core").mkdir(parents=True)
        (source / "hooks" / "core" / "x.py").write_text("x")
        (deployed / "hooks" / "core").mkdir(parents=True)
        (deployed / "hooks" / "core" / "x.py").write_text("x-modified")

        # No manifest file
        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['total_drifted'] == 1
                assert result['drifted'][0]['relative'] == 'hooks/core/x.py'

    def test_manifest_with_multiple_mappings(self, tmp_path):
        """Checks files across different mapping types via manifest."""
        source = tmp_path / "source"
        deployed = tmp_path / "deployed"

        # Phase command (flattened)
        (source / "phases" / "01-discovery").mkdir(parents=True)
        (source / "phases" / "01-discovery" / "command.md").write_text("disc")
        (deployed / "commands").mkdir(parents=True)
        (deployed / "commands" / "discovery.md").write_text("disc")

        # Hook (1:1)
        (source / "hooks" / "core").mkdir(parents=True)
        (source / "hooks" / "core" / "x.py").write_text("x")
        (deployed / "hooks" / "core").mkdir(parents=True)
        (deployed / "hooks" / "core" / "x.py").write_text("x-modified")

        self._write_manifest(str(deployed), {
            "commands/discovery.md": "phases/01-discovery/command.md",
            "hooks/core/x.py": "hooks/core/x.py",
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['checked'] == 2
                assert result['total_drifted'] == 1
                assert result['drifted'][0]['relative'] == 'hooks/core/x.py'

    def test_manifest_with_absolute_deployed_path(self, tmp_path):
        """Checks Codex runtime files deployed outside the Codex root."""
        source = tmp_path / "source"
        deployed = tmp_path / "deployed"
        runtime = tmp_path / "obi-tools"

        (source / "hooks" / "core").mkdir(parents=True)
        (source / "hooks" / "core" / "x.py").write_text("x")
        (runtime / "hooks" / "core").mkdir(parents=True)
        (runtime / "hooks" / "core" / "x.py").write_text("x-modified")

        runtime_file = runtime / "hooks" / "core" / "x.py"
        self._write_manifest(str(deployed), {
            str(runtime_file).replace("\\", "/"): "hooks/core/x.py",
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['checked'] == 1
                assert result['total_drifted'] == 1
                assert result['drifted'][0]['status'] == 'modified'

    def test_excludes_pycache(self, tmp_path):
        """Skips __pycache__ entries even in manifest."""
        source = tmp_path / "source"
        source.mkdir()
        deployed = tmp_path / "deployed"

        (deployed / "hooks" / "__pycache__").mkdir(parents=True)
        (deployed / "hooks" / "__pycache__" / "mod.cpython-311.pyc").write_bytes(b"compiled")

        self._write_manifest(str(deployed), {
            "hooks/__pycache__/mod.cpython-311.pyc": "hooks/__pycache__/mod.cpython-311.pyc",
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                assert result['total_drifted'] == 0

    def test_unmanifested_in_managed_skill_subdir(self, tmp_path):
        """Flags deployed-only file in a skill subdir that has a manifest entry."""
        source = tmp_path / "source"
        deployed = tmp_path / "deployed"

        # Source + deployed agree on SKILL.md
        (source / "skills" / "reviewing-code").mkdir(parents=True)
        (source / "skills" / "reviewing-code" / "SKILL.md").write_text("skill")
        (deployed / "skills" / "reviewing-code").mkdir(parents=True)
        (deployed / "skills" / "reviewing-code" / "SKILL.md").write_text("skill")
        # Extra deployed-only file in the same skill dir (the bug case)
        (deployed / "skills" / "reviewing-code" / "references").mkdir()
        (deployed / "skills" / "reviewing-code" / "references" / "extra.md").write_text("orphan")

        self._write_manifest(str(deployed), {
            "skills/reviewing-code/SKILL.md": "skills/reviewing-code/SKILL.md",
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                statuses = [d['status'] for d in result['drifted']]
                rels = [d['relative'] for d in result['drifted']]
                assert 'unmanifested' in statuses
                assert 'skills/reviewing-code/references/extra.md' in rels

    def test_unmanifested_skipped_in_externally_managed_skill(self, tmp_path):
        """Skips skill subdirs that have zero manifest entries (e.g. external-skill)."""
        source = tmp_path / "source"
        source.mkdir()
        deployed = tmp_path / "deployed"

        # Externally-managed skill has no manifest entry; its files must NOT be flagged
        (deployed / "skills" / "external-skill").mkdir(parents=True)
        (deployed / "skills" / "external-skill" / "SKILL.md").write_text("ext")
        (deployed / "skills" / "external-skill" / "extra.md").write_text("ext")

        # Manifest is non-empty (so load_manifest() returns it) but covers a different skill
        (deployed / "skills" / "other").mkdir(parents=True)
        (deployed / "skills" / "other" / "SKILL.md").write_text("other")
        (source / "skills" / "other").mkdir(parents=True)
        (source / "skills" / "other" / "SKILL.md").write_text("other")

        self._write_manifest(str(deployed), {
            "skills/other/SKILL.md": "skills/other/SKILL.md",
        })

        with patch('core.drift_detector.get_source_repo_path', return_value=str(source)):
            with patch('core.drift_detector.get_deployed_root', return_value=str(deployed)):
                result = detect_drift()
                rels = [d['relative'] for d in result['drifted']]
                assert not any(r.startswith('skills/external-skill/') for r in rels)
                assert result['total_drifted'] == 0


class TestFormatDriftSummary:
    """Tests for format_drift_summary."""

    def test_no_source_repo(self):
        """Shows message when source repo not found."""
        result = format_drift_summary({'source_path': None})
        assert "Source repo not found" in result

    def test_all_in_sync(self):
        """Shows all-clear when no drift."""
        result = format_drift_summary({
            'source_path': '/some/path',
            'checked': 42,
            'drifted': [],
        })
        assert "42 files in sync" in result

    def test_shows_drifted_files(self):
        """Shows table of drifted files."""
        result = format_drift_summary({
            'source_path': '/some/path',
            'checked': 10,
            'drifted': [
                {'relative': 'commands/obi.md', 'status': 'modified'},
                {'relative': 'hooks/core/new.py', 'status': 'source_missing'},
            ],
        })
        assert "2 of 10" in result
        assert "commands/obi.md" in result
        assert "Modified" in result
        assert "Deployed only" in result
        assert "/obi-collect" in result

    def test_shows_unmanifested_status(self):
        """Renders 'Unmanifested' label for unmanifested entries."""
        result = format_drift_summary({
            'source_path': '/some/path',
            'checked': 5,
            'drifted': [
                {'relative': 'skills/x/orphan.md', 'status': 'unmanifested'},
            ],
        })
        assert "Unmanifested" in result
        assert "skills/x/orphan.md" in result

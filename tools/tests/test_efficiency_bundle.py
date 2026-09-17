from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.efficiency_bundle import BundleError, restore, snapshot
from tools.peer_review.io_utils import sha256_file


def _write_manifest(path: Path, mappings: dict[str, str], hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": "2.0", "mappings": mappings, "deployed_hashes": hashes}),
        encoding="utf-8",
    )


def test_snapshot_and_restore_exact_manifest_scope(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    managed = runtime / "commands" / "author.md"
    second = runtime / "skills" / "verify" / "SKILL.md"
    unrelated = runtime / "config.json"
    user_backup = runtime / "commands" / "author.md.user-backup"
    for path, value in (
        (managed, "old author"),
        (second, "old verify"),
        (unrelated, "user config"),
        (user_backup, "earliest user copy"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    old_hashes = {"commands/author.md": sha256_file(managed), "skills/verify/SKILL.md": sha256_file(second)}
    mappings = {key: f"source/{key}" for key in old_hashes}
    manifest = runtime / ".obi" / "deployment-manifest.json"
    _write_manifest(manifest, mappings, old_hashes)
    old_manifest_hash = sha256_file(manifest)

    snapshot_dir = tmp_path / "named-snapshot"
    result = snapshot(runtime, manifest, snapshot_dir)
    assert result["files"] == 2
    metadata = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
    assert metadata["runtime_root"] == str(runtime.resolve())
    assert {entry["path"]: entry["sha256"] for entry in metadata["files"]} == old_hashes

    managed.write_text("candidate author", encoding="utf-8")
    second.write_text("candidate verify", encoding="utf-8")
    candidate_only = runtime / "commands" / "new.md"
    candidate_only.write_text("candidate only", encoding="utf-8")
    candidate_mappings = {**mappings, "commands/new.md": "source/commands/new.md"}
    candidate_hashes = {
        "commands/author.md": sha256_file(managed),
        "skills/verify/SKILL.md": sha256_file(second),
        "commands/new.md": sha256_file(candidate_only),
    }
    _write_manifest(manifest, candidate_mappings, candidate_hashes)

    restored = restore(runtime, manifest, snapshot_dir)
    assert restored["files_restored"] == 2
    assert restored["files_removed"] == 1
    assert sha256_file(managed) == old_hashes["commands/author.md"]
    assert sha256_file(second) == old_hashes["skills/verify/SKILL.md"]
    assert not candidate_only.exists()
    assert sha256_file(manifest) == old_manifest_hash
    assert unrelated.read_text(encoding="utf-8") == "user config"
    assert user_backup.read_text(encoding="utf-8") == "earliest user copy"


def test_restore_preflights_all_collisions_before_mutation(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    first = runtime / "managed" / "first.txt"
    second = runtime / "managed" / "second.txt"
    first.parent.mkdir(parents=True)
    first.write_text("old first", encoding="utf-8")
    second.write_text("old second", encoding="utf-8")
    mappings = {"managed/first.txt": "source/first", "managed/second.txt": "source/second"}
    manifest = runtime / ".obi" / "deployment-manifest.json"
    _write_manifest(manifest, mappings, {key: sha256_file(runtime / key) for key in mappings})
    snapshot_dir = tmp_path / "snapshot"
    snapshot(runtime, manifest, snapshot_dir)

    first.write_text("candidate first", encoding="utf-8")
    second.write_text("user edited second", encoding="utf-8")
    candidate_first_hash = sha256_file(first)
    _write_manifest(manifest, mappings, {"managed/first.txt": candidate_first_hash})

    with pytest.raises(BundleError, match="expected current candidate hash is absent"):
        restore(runtime, manifest, snapshot_dir)
    assert first.read_text(encoding="utf-8") == "candidate first"
    assert second.read_text(encoding="utf-8") == "user edited second"


@pytest.mark.parametrize('conflict', [None, 'hash', 'mapping', 'schema'])
def test_restore_reconciles_only_scoped_native_ownership(tmp_path: Path, conflict) -> None:
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    target = runtime / 'managed.txt'
    target.write_text('prior', encoding='utf-8')
    prior_hash = sha256_file(target)
    scope = runtime / '.obi/scope.json'
    mappings = {'managed.txt': 'source/managed.txt', 'new.txt': 'source/new.txt'}
    _write_manifest(scope, mappings, {'managed.txt': prior_hash})
    backup = tmp_path / 'backup'
    snapshot(runtime, scope, backup)
    target.write_text('candidate', encoding='utf-8')
    added = runtime / 'new.txt'
    added.write_text('added', encoding='utf-8')
    hashes = {name: sha256_file(runtime / name) for name in mappings}
    _write_manifest(scope, mappings, hashes)
    owner = runtime / '.obi/deployment-manifest.json'
    owner_value = {'version': '2.0', 'custom': {'preserve': True},
                   'mappings': {**mappings, 'unrelated': 'user/source'},
                   'deployed_hashes': {**hashes, 'unrelated': 'a' * 64}}
    if conflict == 'hash':
        owner_value['deployed_hashes']['managed.txt'] = 'b' * 64
    elif conflict == 'mapping':
        owner_value['mappings']['managed.txt'] = 'user/changed-source'
    elif conflict == 'schema':
        owner_value['version'] = '1.0'
    owner.write_text(json.dumps(owner_value), encoding='utf-8')
    before_owner = owner.read_bytes()
    if conflict:
        with pytest.raises(BundleError, match='ownership'):
            restore(runtime, scope, backup, owner)
        assert target.read_text(encoding='utf-8') == 'candidate'
        assert added.exists()
        assert owner.read_bytes() == before_owner
    else:
        restore(runtime, scope, backup, owner)
        restored = json.loads(owner.read_text(encoding='utf-8'))
        assert restored['deployed_hashes']['managed.txt'] == sha256_file(target) == prior_hash
        assert 'new.txt' not in restored['mappings'] and 'new.txt' not in restored['deployed_hashes']
        assert restored['mappings']['unrelated'] == 'user/source'
        assert restored['deployed_hashes']['unrelated'] == 'a' * 64
        assert restored['custom'] == {'preserve': True}
        restore(runtime, scope, backup, owner)

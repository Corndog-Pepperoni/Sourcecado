"""S5: one versioned migration registry across every durable Sourcecado store."""

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from coworker import migrations
from coworker.migrations import (
    BackupFailed,
    Migration,
    StoreStatus,
    VersionChannel,
)
from tests.state_fixtures import (
    PLANTED_CANARIES,
    build_current_state,
    build_legacy_state,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(root: Path) -> dict[str, str]:
    """Hash the state a store holds, not SQLite's bookkeeping about it.

    A database in WAL mode recreates its `-shm` and `-wal` sidecars whenever it
    is opened, including read-only: the journal mode lives in the file header,
    so merely reading one brings them back. Hashing those turns "this changed
    nothing" into "nobody opened this", which is a different and weaker claim.

    `-shm` is shared memory and never holds committed data, so it is always
    skipped. A `-wal` is skipped only while it is empty. A write parked in a
    WAL that has not been checkpointed still shows up, so a change that hid
    there rather than in the main file is still caught.
    """
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith("-shm"):
            continue
        if path.name.endswith("-wal") and path.stat().st_size == 0:
            continue
        digests[str(path.relative_to(root))] = _digest(path)
    return digests


def _plan_for(plan, store_id):
    return next(item for item in plan.stores if item.store_id == store_id)


# --- the registry itself -------------------------------------------------


def test_registry_names_every_active_durable_store():
    registered = {spec.store_id for spec in migrations.REGISTRY}
    assert registered == {
        "conversation_transcripts",
        "presentation_events",
        "memory_notes",
        "secrets",
        "mcp_config",
        "dotenv",
    }


def test_registry_reuses_the_version_constant_owned_by_each_store_module():
    from coworker.secrets import SCHEMA_VERSION as secrets_version

    expected = {
        "secrets": secrets_version,
    }
    for store_id, version in expected.items():
        assert migrations.spec_for(store_id).current_version == version


def test_every_migration_chain_is_contiguous_and_ends_at_the_current_version():
    for spec in migrations.REGISTRY:
        if spec.version_channel is VersionChannel.NONE:
            assert spec.migrations == ()
            continue
        expected = 0
        for step in spec.migrations:
            assert step.from_version == expected
            assert step.to_version == expected + 1
            expected = step.to_version
        assert expected == spec.current_version


# --- version reporting ---------------------------------------------------


def test_every_versioned_store_reports_a_version_after_adoption(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)

    for spec in migrations.REGISTRY:
        if spec.version_channel is VersionChannel.NONE:
            continue
        assert migrations.read_version(root, spec) == spec.current_version, spec.store_id


def test_an_absent_store_reports_no_version_and_needs_no_migration(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    plan = migrations.plan_migrations(root)

    for item in plan.stores:
        assert item.status is StoreStatus.ABSENT
        assert item.from_version is None
        assert item.steps == ()
    assert plan.pending == ()


# --- upgrades from the supported prior version ---------------------------


def test_legacy_jsonl_and_opaque_stores_adopt_a_version_in_the_manifest(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    assert migrations.apply_migrations(root).error is None

    manifest = json.loads((root / migrations.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    for store_id in (
        "conversation_transcripts",
        "presentation_events",
        "secrets",
        "mcp_config",
    ):
        assert manifest["stores"][store_id] == 1


# --- failing closed ------------------------------------------------------


def test_a_missing_migration_step_is_reported_rather_than_silently_skipped(
    tmp_path, monkeypatch
):
    root = build_current_state(tmp_path / "state")
    original = migrations.spec_for("conversation_transcripts")
    gapped = migrations.dataclasses.replace(original, current_version=3)
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(gapped if item.store_id == "conversation_transcripts" else item for item in migrations.REGISTRY),
    )
    before = _tree_digest(root)

    plan = migrations.plan_migrations(root)
    assert _plan_for(plan, "conversation_transcripts").status is StoreStatus.MIGRATION_PATH_MISSING
    assert plan.blocked is True
    assert migrations.apply_migrations(root).applied == ()
    assert _tree_digest(root) == before


# --- backups -------------------------------------------------------------


def test_migration_takes_a_timestamped_backup_before_changing_state(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    outcome = migrations.apply_migrations(root)

    assert outcome.backup_id is not None
    assert outcome.backup_id.startswith("doctor-")
    backup_dir = root / migrations.BACKUPS_DIR_NAME / outcome.backup_id
    assert backup_dir.is_dir()
    assert oct(backup_dir.stat().st_mode & 0o777) == "0o700"
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["reason"] == "migration"
    backed_up = {entry["store_id"] for entry in manifest["entries"]}
    assert "conversation_transcripts" in backed_up


def test_backups_can_be_listed_and_restored(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    note_path = root / "memory" / "1.md"
    original = note_path.read_bytes()

    backup = migrations.create_backup(root, ["memory_notes"], reason="manual")
    note_path.write_bytes(b"replaced")
    assert note_path.read_bytes() != original

    listed = migrations.list_backups(root)
    assert backup.backup_id in {item["backup_id"] for item in listed}
    assert all("store_id" in entry for item in listed for entry in item["entries"])

    result = migrations.restore_backup(root, backup.backup_id)
    assert result["restored"] == ["memory_notes"]
    assert note_path.read_bytes() == original


def test_restore_backs_up_the_current_state_before_overwriting_it(tmp_path):
    root = build_current_state(tmp_path / "state")
    migrations.apply_migrations(root)
    backup = migrations.create_backup(root, ["memory_notes"], reason="manual")
    replaced = b"replaced"
    (root / "memory" / "1.md").write_bytes(replaced)

    result = migrations.restore_backup(root, backup.backup_id)
    safety = result["safety_backup_id"]
    assert safety != backup.backup_id
    safety_copy = root / migrations.BACKUPS_DIR_NAME / safety / "memory" / "1.md"
    assert safety_copy.read_bytes() == replaced


def test_secret_bearing_stores_are_never_copied_into_a_backup(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    backup = migrations.create_backup(
        root, [spec.store_id for spec in migrations.REGISTRY], reason="manual"
    )

    assert not (backup.path / "secrets.json").exists()
    assert not (backup.path / ".env").exists()
    entries = {entry["store_id"]: entry for entry in backup.manifest["entries"]}
    assert entries["secrets"]["content_backed_up"] is False
    assert entries["dotenv"]["content_backed_up"] is False
    assert entries["conversation_transcripts"]["content_backed_up"] is True


def test_backup_manifest_carries_no_secret_and_no_absolute_path(tmp_path):
    root = build_current_state(tmp_path / "state", plant_secrets=True)
    backup = migrations.create_backup(
        root, [spec.store_id for spec in migrations.REGISTRY], reason="manual"
    )
    rendered = (backup.path / "manifest.json").read_text(encoding="utf-8")

    for canary in PLANTED_CANARIES:
        assert canary not in rendered
    assert str(root) not in rendered
    assert str(Path.home()) not in rendered


def test_a_failed_backup_aborts_the_migration_and_leaves_state_untouched(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    before = _tree_digest(root)
    blocker = root / migrations.BACKUPS_DIR_NAME
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(BackupFailed):
        migrations.create_backup(root, ["conversation_transcripts"], reason="manual")

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert outcome.applied == ()
    blocker.unlink()
    assert _tree_digest(root) == before


# --- failure, rollback, rerun -------------------------------------------


def _break_step(store_id, monkeypatch, apply):
    """Swap a store's first migration for a step that fails partway through.

    Later steps are kept as they are, so the chain still reaches the store's
    current version and the plan stays PENDING rather than becoming
    MIGRATION_PATH_MISSING. The first step fails, so they never run.
    """
    original = migrations.spec_for(store_id)
    failing = Migration(
        from_version=0,
        to_version=1,
        description="deliberately failing step",
        count=lambda _context: 1,
        apply=apply,
    )
    broken = migrations.dataclasses.replace(
        original,
        migrations=(failing,) + original.migrations[1:],
    )
    monkeypatch.setattr(
        migrations,
        "REGISTRY",
        tuple(
            broken if item.store_id == store_id else item
            for item in migrations.REGISTRY
        ),
    )


def test_a_file_migration_that_fails_halfway_is_restored_from_the_backup(
    tmp_path, monkeypatch
):
    """A file store has no transaction, so the backup is the only way back."""
    root = build_legacy_state(tmp_path / "state")
    path = root / "conversations" / "main.jsonl"
    before = path.read_bytes()

    def corrupt_then_explode(context):
        (context.path / "main.jsonl").write_text("{ half written", encoding="utf-8")
        raise RuntimeError("migration step failed halfway")

    _break_step("conversation_transcripts", monkeypatch, corrupt_then_explode)

    outcome = migrations.apply_migrations(root)
    assert outcome.error is not None
    assert outcome.rolled_back == ("conversation_transcripts",)

    assert path.read_bytes() == before
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_migrations_are_idempotent_when_rerun(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    first = migrations.apply_migrations(root)
    assert first.applied

    after_first = _tree_digest(root)
    second = migrations.apply_migrations(root)

    assert second.applied == ()
    assert second.backup_id is None
    assert second.error is None
    assert migrations.plan_migrations(root).pending == ()
    unchanged = {
        name: digest
        for name, digest in _tree_digest(root).items()
        if not name.startswith(migrations.BACKUPS_DIR_NAME)
    }
    assert unchanged == {
        name: digest
        for name, digest in after_first.items()
        if not name.startswith(migrations.BACKUPS_DIR_NAME)
    }


def test_migration_survives_a_process_restart_between_runs(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)

    # A second process reading the same directory sees the recorded versions and
    # has nothing left to do.
    assert migrations.plan_migrations(root).pending == ()
    for spec in migrations.REGISTRY:
        if spec.version_channel is VersionChannel.NONE:
            continue
        assert migrations.read_version(root, spec) == spec.current_version


def test_the_manifest_is_written_with_owner_only_permissions(tmp_path):
    root = build_legacy_state(tmp_path / "state")
    migrations.apply_migrations(root)
    manifest = root / migrations.MANIFEST_NAME

    assert oct(manifest.stat().st_mode & 0o777) == "0o600"


def test_backup_files_keep_owner_only_permissions(tmp_path):
    root = build_current_state(tmp_path / "state")
    backup = migrations.create_backup(root, ["memory_notes"], reason="manual")
    copied = [item for item in backup.path.rglob("*") if item.is_file() and item.name != "manifest.json"]

    assert copied
    assert all(oct(item.stat().st_mode & 0o777) == "0o600" for item in copied)
    assert oct((backup.path / "manifest.json").stat().st_mode & 0o777) == "0o600"
    assert oct(os.stat(root / migrations.BACKUPS_DIR_NAME).st_mode & 0o777) == "0o700"

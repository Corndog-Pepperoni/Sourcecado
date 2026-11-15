"""Criterion 6: the five exercises, run rather than described.

Clean install, upgrade from the prior preview, a failed update, and a rollback.
`docs/update-channel.md` describes these in prose; this file is what actually runs, and the doc points at it by name so
the two cannot drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import update_fixtures as fx  # noqa: E402
from coworker import doctor, migrations  # noqa: E402
from coworker.agent_run_repository import AgentRunRepository  # noqa: E402
from coworker.update_channel import UpdateStage, UpdateStatus, apply_update, rollback  # noqa: E402
from coworker.update_channel.apply import has_run_store  # noqa: E402
from state_fixtures import build_legacy_state  # noqa: E402


def _versions(root: Path) -> dict[str, int | None]:
    return {
        spec.store_id: migrations.read_version(root, spec)
        for spec in migrations.REGISTRY
        if migrations.store_present(root, spec)
    }


def _update(install, artifact, seed, public, **kwargs):
    kwargs.setdefault("health_check", lambda _i: True)
    opened = None
    if "runs" not in kwargs and has_run_store(install.state_root):
        opened = AgentRunRepository(install.state_root)
        kwargs["runs"] = opened
    kwargs.setdefault("runs", None)
    try:
        return apply_update(
            fx.document(artifact, seed, **kwargs.pop("manifest", {})),
            installation=install,
            artifact_path=artifact,
            trust=fx.trust(public),
            drain_timeout=0.0,
            sleep=lambda _: None,
            **kwargs,
        )
    finally:
        if opened is not None:
            try:
                opened.close()
            except Exception:
                pass


# --- exercise 1: clean install -------------------------------------------


def test_exercise_clean_install(tmp_path):
    """No previous application, no previous state. Nothing to back up or migrate."""
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.0")
    assert not install.bundle_path.exists()
    assert not install.state_root.exists()

    outcome = _update(
        install,
        artifact,
        seed,
        public,
        manifest={"minimum_upgradable_version": "0.0.0"},
    )

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert outcome.stage is UpdateStage.DONE
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
    assert outcome.backup_id is None
    assert outcome.migrated == ()
    assert not install.previous_path.exists()


# --- exercise 2: upgrade from the prior preview --------------------------


def test_exercise_upgrade_from_the_prior_preview(tmp_path):
    """State written by the previous preview build, brought forward and kept."""
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")

    outcome = _update(install, artifact, seed, public)

    assert outcome.status is UpdateStatus.APPLIED, outcome.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.2"
    assert fx.bundle_version(install.previous_path) == "0.0.1"
    assert "conversation_transcripts" in outcome.migrated
    assert outcome.backup_id is not None
    assert migrations.plan_migrations(install.state_root).pending == ()


# --- exercise 3: a failed update -----------------------------------------


def test_exercise_failed_update_restores_the_last_usable_version(tmp_path):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    versions_before = _versions(install.state_root)

    outcome = _update(install, artifact, seed, public, health_check=lambda _i: False)

    assert outcome.stage is UpdateStage.HEALTH, "the update must reach the launch check"
    assert outcome.status is UpdateStatus.ROLLED_BACK
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert not install.previous_path.exists()
    assert not install.staging_root.exists()
    assert _versions(install.state_root) == versions_before
    assert "0.0.1" in outcome.guidance

    # Doctor and the updater must not disagree about what is on this machine.
    report = doctor.diagnose(install.state_root)
    assert not report.blocked, [item.code for item in report.findings if item.blocking]
    assert {item.store_id: item.status for item in report.stores}["conversation_transcripts"] in {
        "pending",
        "current",
    }


# --- exercise 4: rollback -------------------------------------------------


def test_exercise_rollback_after_a_successful_update(tmp_path):
    seed, public = fx.keypair()
    artifact = fx.artifact(tmp_path, version="0.0.2")
    install = fx.installation(tmp_path, version="0.0.1")
    build_legacy_state(install.state_root)
    fx.app_tree(install.bundle_path.parent, version="0.0.1")
    versions_before = _versions(install.state_root)

    applied = _update(install, artifact, seed, public)
    assert applied.status is UpdateStatus.APPLIED, applied.reason
    assert _versions(install.state_root) != versions_before

    reverted = rollback(install, backup_id=applied.backup_id)

    assert reverted.status is UpdateStatus.ROLLED_BACK, reverted.reason
    assert fx.bundle_version(install.bundle_path) == "0.0.1"
    assert not install.previous_path.exists()
    assert _versions(install.state_root) == versions_before
    assert "conversation_transcripts" in reverted.restored
    assert not doctor.diagnose(install.state_root).blocked



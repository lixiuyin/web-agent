"""Versioned atomic checkpoint persistence tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from webagent.agent.checkpoint import (
    AgentCheckpoint,
    ArtifactRecord,
    CheckpointCompatibilityError,
    CheckpointCorruptError,
    CheckpointStore,
    PendingAction,
    checkpoint_fingerprint,
)
from webagent.agent.state import PlanningState

_SOURCE = "a" * 64


def _checkpoint(run_id: str = "run-1", *, history: tuple[dict, ...] = ()) -> AgentCheckpoint:
    return AgentCheckpoint(
        run_id=run_id,
        task_sha256=hashlib.sha256(b"Find the report").hexdigest(),
        next_step=4,
        elapsed_seconds=12.5,
        config_fingerprint=checkpoint_fingerprint({"model": "m", "headless": True}),
        source_fingerprint=_SOURCE,
        history=history,
        planning_state=PlanningState.create(
            "task bound by checkpoint task_sha256",
            ["checkpoint milestone 1", "checkpoint milestone 2"],
        ),
    )


def test_checkpoint_round_trip_checksum_and_secret_redaction(tmp_path: Path) -> None:
    path = tmp_path / "run" / "checkpoint.json"
    store = CheckpointStore(path)
    checkpoint = _checkpoint(history=({"api_key": "secret", "url": "https://x"},))

    assert store.save(checkpoint) == path.resolve()
    loaded = store.load(
        expected_task="Find the report",
        expected_config_fingerprint=checkpoint.config_fingerprint,
        expected_source_fingerprint=_SOURCE,
    )

    assert loaded.next_step == 4
    assert loaded.task_sha256
    assert loaded.history[0]["api_key"] == "[redacted]"
    assert loaded.history[0]["url"] == "https://x"
    assert "Find the report" not in path.read_text(encoding="utf-8")
    assert store.digest() is not None
    assert not list(path.parent.glob("*.tmp"))


def test_corrupt_primary_falls_back_to_last_known_good_backup(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.json")
    first = _checkpoint("run-1")
    second = _checkpoint("run-2")
    store.save(first)
    store.save(second)
    store.path.write_text("{corrupt", encoding="utf-8")

    loaded = store.load()

    assert loaded.run_id == "run-1"


def test_checksum_tampering_is_rejected_without_backup(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.json", keep_backup=False)
    store.save(_checkpoint())
    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    envelope["checkpoint"]["next_step"] = 99
    store.path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(CheckpointCorruptError, match="checksum"):
        store.load()
    with pytest.raises(CheckpointCorruptError, match="checksum"):
        store.digest()


def test_compatibility_mismatch_does_not_silently_resume_backup(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.save(_checkpoint())

    with pytest.raises(CheckpointCompatibilityError, match="task"):
        store.load(expected_task="Different task")
    with pytest.raises(CheckpointCompatibilityError, match="config_fingerprint"):
        store.load(expected_config_fingerprint="b" * 64)


def test_artifact_manifest_detects_changes_and_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    artifact = root / "report.pdf"
    artifact.write_bytes(b"pdf")
    record = ArtifactRecord.from_path(artifact, root=root)
    checkpoint = _checkpoint().model_copy(update={"artifacts": (record,)})
    store = CheckpointStore(root / "checkpoint.json")

    assert store.missing_artifacts(checkpoint, root=root) == []
    artifact.write_bytes(b"changed")
    assert store.missing_artifacts(checkpoint, root=root) == ["report.pdf"]

    with pytest.raises(ValueError, match="output root"):
        ArtifactRecord.from_path(tmp_path / "outside.txt", root=root)


def test_pending_external_action_is_hash_only_and_serializable(tmp_path: Path) -> None:
    action = PendingAction(
        tool_name="submit_order",
        parameters_sha256=checkpoint_fingerprint({"sku": "x", "quantity": 1}),
        external_effect="purchase",
        replay_policy="reconcile",
    )
    checkpoint = _checkpoint().model_copy(update={"pending_action": action})
    store = CheckpointStore(tmp_path / "checkpoint.json")

    store.save(checkpoint)
    loaded = store.load()

    assert loaded.pending_action == action
    assert "sku" not in store.path.read_text(encoding="utf-8")

"""Tests for lossless historical-output migration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from webagent.evaluation.migration import main, migrate_legacy_outputs, plan_legacy_migration


def test_migration_dry_run_does_not_move_and_apply_preserves_bytes(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    old_run = output_root / "old-run" / "artifacts"
    old_run.mkdir(parents=True)
    payload = b"original trace bytes\n"
    (old_run / "run.json").write_bytes(payload)
    (output_root / "runs").mkdir()
    (output_root / "studies").mkdir()
    (output_root / "campaigns").mkdir()

    plan = plan_legacy_migration(output_root, label="pre-layout")

    assert [entry.name for entry in plan.entries] == ["old-run"]
    assert (old_run / "run.json").read_bytes() == payload
    assert plan.files[0].sha256 == hashlib.sha256(payload).hexdigest()

    manifest_path = migrate_legacy_outputs(output_root, label="pre-layout")
    archived = output_root / "legacy" / "pre-layout" / "tree" / "old-run" / "artifacts"
    assert not (output_root / "old-run").exists()
    assert (archived / "run.json").read_bytes() == payload
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["file_count"] == 1
    assert manifest["total_bytes"] == len(payload)
    assert (output_root / "runs").is_dir()
    assert (output_root / "studies").is_dir()
    assert (output_root / "campaigns").is_dir()

    assert migrate_legacy_outputs(output_root, label="pre-layout") == manifest_path


def test_migration_rejects_symlinks_and_existing_target_conflicts(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("do not follow", encoding="utf-8")
    (output_root / "linked").symlink_to(outside)

    with pytest.raises(ValueError, match="symbolic links"):
        plan_legacy_migration(output_root, label="legacy")

    (output_root / "linked").unlink()
    (output_root / "old.txt").write_text("old", encoding="utf-8")
    target = output_root / "legacy" / "taken"
    target.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        migrate_legacy_outputs(output_root, label="taken")


def test_migration_rejects_unsafe_label(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="label"):
        plan_legacy_migration(tmp_path / "outputs", label="../escape")


def test_apply_cli_reports_persisted_inventory_on_idempotent_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    (output_root / "old.txt").write_bytes(b"old")

    arguments = [str(output_root), "--label", "archive", "--apply"]
    main(arguments)
    capsys.readouterr()
    main(arguments)

    output = capsys.readouterr().out
    assert "1 files (3 bytes)" in output

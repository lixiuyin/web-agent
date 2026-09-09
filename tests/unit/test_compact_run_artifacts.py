"""Safe compaction keeps both evidence paths and changes no bytes."""

import importlib.util
import json
import shutil
import sys
from pathlib import Path


def _compactor():
    path = Path(__file__).resolve().parents[2] / "scripts" / "compact_run_artifacts.py"
    spec = importlib.util.spec_from_file_location("compact_run_artifacts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.compact


def _run(root: Path, prefix: str, payload: str) -> Path:
    run = root / prefix / "runs/task"
    (run / "trajectory").mkdir(parents=True)
    (run / "trajectory/trace.json").write_text(
        json.dumps({"run_id": "same", "run_root": str(run)}), encoding="utf-8"
    )
    (run / "payload.txt").write_text(payload, encoding="utf-8")
    return run


def test_compaction_is_dry_run_by_default_and_only_links_identical_files(tmp_path) -> None:
    shard = _run(tmp_path, "shards/shard-00", "same")
    canonical = _run(tmp_path, ".", "same")
    shutil.copystat(shard / "payload.txt", canonical / "payload.txt")
    (canonical / "different.txt").write_text("canonical", encoding="utf-8")
    (shard / "different.txt").write_text("shard", encoding="utf-8")
    before = (shard / "payload.txt").stat().st_ino, (canonical / "payload.txt").stat().st_ino

    dry_run = _compactor()(tmp_path)

    assert dry_run.run_pairs == 1
    assert dry_run.reclaimable_bytes > 0
    assert (shard / "payload.txt").stat().st_ino == before[0]
    assert (canonical / "payload.txt").stat().st_ino == before[1]

    applied = _compactor()(tmp_path, apply=True)

    assert applied.linked_files >= 1
    assert (shard / "payload.txt").read_bytes() == (canonical / "payload.txt").read_bytes()
    assert (shard / "payload.txt").stat().st_ino == (canonical / "payload.txt").stat().st_ino
    assert (shard / "different.txt").stat().st_ino != (canonical / "different.txt").stat().st_ino

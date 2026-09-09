from __future__ import annotations

import importlib.util
import json
import sys
import tarfile
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/freeze_evaluation_evidence.py"
    spec = importlib.util.spec_from_file_location("freeze_evaluation_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


freeze = _load_module()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_run(root: Path, *, passed: bool, model: str, observations: bool = False) -> None:
    _write_json(root / "manifest.json", {"model": model})
    _write_json(root / "evaluation/task.json", {"passed": passed})
    _write_json(root / "trajectory/trace.json", {"steps": []})
    (root / "trajectory/events.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "trajectory/events.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "result").mkdir(parents=True, exist_ok=True)
    (root / "result/summary.txt").write_text("summary", encoding="utf-8")
    if observations:
        _write_json(root / "observations/step_001/pre.json", {"dom_summary": ""})
        _write_json(root / "evidence/tool-results/result.json", {"ok": True})
        (root / "observations/step_001/pre.png").write_bytes(b"png")


def test_freeze_bundle_selects_auditable_subset(tmp_path: Path) -> None:
    repository = tmp_path
    campaign = repository / "outputs/campaigns/current"
    strict = repository / "outputs/validation/current"
    _write_json(campaign / "campaign.json", {"campaign_id": "current"})
    batch = campaign / "batches/2026-09-09/batch-id/batch.json"
    reports = [f"studies/suite-{index}/results.json" for index in range(6)]
    _write_json(batch, {"report_paths": reports})
    _write_json(batch.parent / "analysis/portfolio.json", {"status": "insufficient"})
    for report in reports:
        _write_json(campaign / report, {"summary": {"task_count": 1}})
    failed = campaign / "studies/sandbox/runs/failed"
    _write_run(failed, passed=False, model="qwen/model", observations=True)
    for model in ("qwen/model", "z-ai/model"):
        label = model.replace("/", "-")
        run = campaign / f"studies/long-horizon/{label}/runs/mission"
        _write_run(run, passed=True, model=model)
        _write_json(run / "control/checkpoints/latest.json", {"step": 35})
    for label in ("qwen", "glm"):
        model_root = strict / label
        _write_json(model_root / "execution.json", {"status": "completed"})
        _write_json(model_root / "results.json", {"summary": {"passed_tasks": 1}})
        run = model_root / "runs/strict-task"
        _write_run(run, passed=True, model=label, observations=True)
        _write_json(run / "trajectory/verification.json", {"valid": True})

    output = repository / "outputs/published/2026-09-09"
    manifest = freeze.freeze_bundle(repository, campaign, strict, output)

    assert manifest["file_count"] > 20
    assert (output / "campaign/campaign.json").is_file()
    failure_archive = output / "archives/failed-trajectory-evidence.tar.gz"
    assert failure_archive.is_file()
    with tarfile.open(failure_archive) as archive:
        assert "failed-trajectory/failed/observations/step_001/pre.png" in archive.getnames()
    assert (output / "strict/qwen/runs/strict-task/trajectory/verification.json").is_file()
    assert (output / "long-horizon/qwen-model/mission/control/checkpoints/latest.json").is_file()
    assert all(item["byte_identical_to_source"] for item in manifest["files"])
    assert manifest["physical_evidence_file_count"] < manifest["file_count"]


def test_freeze_bundle_rejects_secret_material(tmp_path: Path) -> None:
    secret = tmp_path / "trace.json"
    secret.write_text('{"authorization":"Bearer abcdefghijklmnopqrstuvwxyz"}', encoding="utf-8")

    assert freeze._privacy_failures([secret])

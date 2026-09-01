"""Tests for tamper-evident current-date model-matrix ledger records."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from benchmarks.studies.open_web_longitudinal import load_slices
from benchmarks.studies.open_web_matrix import (
    _ordered_models,
    append_ledger,
    ledger_record_from_report,
    run_matrix,
)
from benchmarks.suites.open_web.runner import canonical_sha256


def test_model_order_is_deterministically_counterbalanced_by_date() -> None:
    models = ["a", "b"]

    first = _ordered_models(models, date(2026, 8, 30), "rotate-by-date")
    second = _ordered_models(models, date(2026, 8, 31), "rotate-by-date")

    assert first == list(reversed(second))
    assert _ordered_models(models, date(2026, 8, 30), "as-given") == models


def _report(tmp_path: Path, run_id: str = "run-1") -> tuple[dict[str, object], Path, Path]:
    task_ids = [f"task-{index:02d}" for index in range(30)]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "suite": "open-web-general-v2",
                "tasks": [{"id": task_id} for task_id in task_ids],
            }
        ),
        encoding="utf-8",
    )
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    study_manifest = tmp_path / "study.json"
    study_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "study_id": "open-web-study",
                "suite": "open-web-general-v2",
                "task_manifest_sha256": manifest_hash,
                "models": [{"provider": "openrouter", "model": "model-a"}],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    study_manifest_hash = hashlib.sha256(study_manifest.read_bytes()).hexdigest()
    config = {
        "manifest_sha256": manifest_hash,
        "stealth_mode": False,
        "provider": "openrouter",
        "study_manifest_sha256": study_manifest_hash,
    }
    report: dict[str, object] = {
        "schema_version": 3,
        "suite": "open-web-general-v2",
        "created_at": "2026-08-30T01:02:03+00:00",
        "metadata": {
            "run_id": run_id,
            "provider": "openrouter",
            "model": "model-a",
            "study_id": "open-web-study",
            "study_manifest": str(study_manifest.resolve()),
            "study_manifest_sha256": study_manifest_hash,
            "manifest": str(manifest.resolve()),
            "manifest_sha256": manifest_hash,
            "benchmark_config": config,
            "benchmark_config_sha256": canonical_sha256(config),
            "task_ids_sha256": canonical_sha256(task_ids),
            "agent_source_sha256": "s" * 64,
        },
        "summary": {
            "task_count": 30,
            "success_rate": 0.8,
            "answer_grounding_rate": 0.9,
            "false_completion_rate": 0.1,
            "action_validity_rate": 0.95,
            "timeout_rate": 0.02,
            "captcha_rate": 0.01,
            "blocked_rate": 0.01,
            "max_steps_rate": 0.03,
            "p95_duration_seconds": 42,
            "termination_reason_counts": {"completed": 29, "timeout": 1},
        },
        "tasks": [{"task_id": task_id} for task_id in task_ids],
    }
    report_path = tmp_path / "runs" / run_id / "results.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report, report_path, tmp_path / "ledger.jsonl"


def test_ledger_record_binds_date_task_set_metrics_and_report_hash(tmp_path: Path) -> None:
    report, report_path, ledger_path = _report(tmp_path)
    record = ledger_record_from_report(report, report_path=report_path, ledger_path=ledger_path)

    assert record["benchmark_date"] == "2026-08-30"
    assert record["task_count"] == 30
    assert record["provider"] == "openrouter"
    assert (
        record["study_manifest_sha256"]
        == hashlib.sha256((tmp_path / "study.json").read_bytes()).hexdigest()
    )
    assert len(record["task_ids"]) == 30
    assert record["timeout_rate"] == 0.02
    assert len(record["report_sha256"]) == 64


def test_canonical_nested_ledger_can_bind_execution_evidence(tmp_path: Path) -> None:
    report, report_path, _legacy_ledger = _report(tmp_path)
    ledger = tmp_path / "ledger" / "runs.jsonl"

    record = ledger_record_from_report(report, report_path=report_path, ledger_path=ledger)
    append_ledger(ledger, record)

    assert record["report_path"] == report_path.relative_to(tmp_path).as_posix()
    assert load_slices([ledger])[0]["run_id"] == "run-1"


def test_relocated_legacy_absolute_evidence_paths_resolve_by_root_suffix(
    tmp_path: Path,
) -> None:
    report, report_path, _legacy_ledger = _report(tmp_path)
    metadata = report["metadata"]
    assert isinstance(metadata, dict)
    metadata["manifest"] = f"/missing/original/{tmp_path.name}/manifest.json"
    metadata["study_manifest"] = f"/missing/original/{tmp_path.name}/study.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    ledger = tmp_path / "ledger" / "time-slices.jsonl"

    record = ledger_record_from_report(report, report_path=report_path, ledger_path=ledger)

    assert record["run_id"] == "run-1"


def test_new_time_slice_binds_benchmark_harness_source(tmp_path: Path) -> None:
    report, report_path, ledger = _report(tmp_path)
    metadata = report["metadata"]
    assert isinstance(metadata, dict)
    metadata["benchmark_source_sha256"] = "b" * 64
    report_path.write_text(json.dumps(report), encoding="utf-8")

    record = ledger_record_from_report(report, report_path=report_path, ledger_path=ledger)
    append_ledger(ledger, record)

    assert record["schema_version"] == 5
    assert load_slices([ledger])[0]["benchmark_source_sha256"] == "b" * 64


def test_ledger_refuses_unknown_or_duplicate_run_ids(tmp_path: Path) -> None:
    report, report_path, target = _report(tmp_path)
    record = ledger_record_from_report(report, report_path=report_path, ledger_path=target)

    append_ledger(target, record)
    with pytest.raises(ValueError, match="duplicate run_id"):
        append_ledger(target, record)
    unknown = {**record, "run_id": "unknown"}
    with pytest.raises(ValueError, match="known run_id"):
        append_ledger(target, unknown)

    persisted = [json.loads(line) for line in target.read_text().splitlines()]
    assert persisted == [record]


def test_load_slices_rejects_report_or_ledger_tampering(tmp_path: Path) -> None:
    report, report_path, ledger_path = _report(tmp_path)
    record = ledger_record_from_report(report, report_path=report_path, ledger_path=ledger_path)
    append_ledger(ledger_path, record)

    report["created_at"] = "2026-08-31T01:02:03+00:00"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="ledger/report evidence mismatch"):
        load_slices([ledger_path])

    report["created_at"] = "2026-08-30T01:02:03+00:00"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    record["benchmark_date"] = "2099-01-01"
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ledger/report evidence mismatch"):
        load_slices([ledger_path])


@pytest.mark.parametrize(
    "mutation",
    ["manifest", "config", "task_set", "study_manifest", "study_missing"],
)
def test_load_slices_recomputes_all_retained_evidence(tmp_path: Path, mutation: str) -> None:
    report, report_path, ledger_path = _report(tmp_path)
    record = ledger_record_from_report(report, report_path=report_path, ledger_path=ledger_path)
    append_ledger(ledger_path, record)

    metadata = report["metadata"]
    assert isinstance(metadata, dict)
    if mutation == "manifest":
        Path(str(metadata["manifest"])).write_text(
            '{"suite":"changed","tasks":[]}', encoding="utf-8"
        )
    elif mutation == "config":
        config = metadata["benchmark_config"]
        assert isinstance(config, dict)
        config["stealth_mode"] = True
        report_path.write_text(json.dumps(report), encoding="utf-8")
    elif mutation.startswith("study_"):
        study_path = Path(str(metadata["study_manifest"]))
        if mutation == "study_manifest":
            study_path.write_text('{"study_id":"changed"}', encoding="utf-8")
        else:
            study_path.unlink()
    else:
        tasks = report["tasks"]
        assert isinstance(tasks, list)
        tasks[0] = {"task_id": "substituted-task"}
        report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid report evidence"):
        load_slices([ledger_path])


def test_report_rejects_provider_drift_between_metadata_and_config(tmp_path: Path) -> None:
    report, report_path, ledger_path = _report(tmp_path)
    metadata = report["metadata"]
    assert isinstance(metadata, dict)
    metadata["provider"] = "different-provider"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="provider differs"):
        ledger_record_from_report(report, report_path=report_path, ledger_path=ledger_path)


def test_matrix_refuses_a_single_model_before_starting_subprocesses(tmp_path: Path) -> None:
    args = argparse.Namespace(
        models=["model-a"],
        provider="openrouter",
        manifest=Path("benchmarks/manifests/open_web_general.json"),
        output=tmp_path,
        shards=1,
        repetitions=1,
        max_steps_per_task=8,
        captcha_handling="fail",
        minimum_success_rate=0.0,
        maximum_false_completion_rate=1.0,
    )

    with pytest.raises(ValueError, match="two or three"):
        run_matrix(args)

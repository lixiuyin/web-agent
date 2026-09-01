"""Tests for longitudinal open-web benchmark summaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.studies.open_web_longitudinal import (
    evidence_record_from_report,
    load_slices,
    summarize_slices,
)
from benchmarks.suites.open_web.runner import canonical_sha256


def _record(model: str, day: str, *, run: int = 1, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "run_id": f"{model}-{day}-{run}",
        "created_at": f"{day}T00:00:00Z",
        "benchmark_date": day,
        "suite": "general-v2",
        "provider": "openrouter",
        "model": model,
        "manifest_sha256": "manifest",
        "benchmark_config_sha256": "config",
        "task_ids_sha256": "tasks",
        "task_count": 30,
        "success_rate": 0.8,
        "answer_grounding_rate": 0.9,
        "false_completion_rate": 0.05,
        "timeout_rate": 0.0,
        "captcha_rate": 0.0,
    }
    record.update(overrides)
    return record


def _verified_records(tmp_path: Path, records: list[dict[str, object]]) -> list[dict[str, object]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    task_ids = [f"task-{index:02d}" for index in range(30)]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "suite": "general-v2",
                "tasks": [{"id": task_id} for task_id in task_ids],
            }
        ),
        encoding="utf-8",
    )
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    known_models = sorted(
        {
            (str(source.get("provider", "openrouter")), str(source["model"]))
            for source in records
            if not source.get("omit_provider")
        }
    )
    study_paths: dict[str, tuple[Path, str]] = {}
    for variant in sorted({str(source.get("study_variant", "stable")) for source in records}):
        study_path = tmp_path / f"study-{variant}.json"
        study_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "study_id": "open-web-study",
                    "suite": "general-v2",
                    "task_manifest_sha256": manifest_hash,
                    "models": [
                        {"provider": provider, "model": model} for provider, model in known_models
                    ],
                    "variant": variant,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        study_paths[variant] = (
            study_path,
            hashlib.sha256(study_path.read_bytes()).hexdigest(),
        )
    ledger = tmp_path / "ledger.jsonl"
    ledger_records: list[dict[str, object]] = []
    for index, source in enumerate(records):
        provider = str(source.get("provider", "openrouter"))
        study_path, study_hash = study_paths[str(source.get("study_variant", "stable"))]
        omit_provider = bool(source.get("omit_provider"))
        omit_study_identity = bool(source.get("omit_study_identity")) or omit_provider
        config: dict[str, object] = {
            "manifest_sha256": manifest_hash,
            "stealth_mode": False,
        }
        metadata: dict[str, object] = {
            "run_id": source["run_id"],
            "model": source["model"],
            "manifest": str(manifest.resolve()),
            "manifest_sha256": manifest_hash,
            "task_ids_sha256": canonical_sha256(task_ids),
            "agent_source_sha256": source.get("agent_source_sha256", "a" * 64),
            "benchmark_source_sha256": source.get("benchmark_source_sha256", "b" * 64),
        }
        if not omit_provider:
            config["provider"] = provider
            metadata["provider"] = provider
        if not omit_study_identity:
            config["study_manifest_sha256"] = study_hash
            metadata.update(
                {
                    "study_id": "open-web-study",
                    "study_manifest": str(study_path.resolve()),
                    "study_manifest_sha256": study_hash,
                }
            )
        metadata["benchmark_config"] = config
        metadata["benchmark_config_sha256"] = canonical_sha256(config)
        report: dict[str, object] = {
            "schema_version": 3,
            "suite": "general-v2",
            "created_at": source["created_at"],
            "metadata": metadata,
            "summary": {
                "task_count": 30,
                "success_rate": source.get("success_rate", 0.0),
                "answer_grounding_rate": source.get("answer_grounding_rate", 0.0),
                "false_completion_rate": source.get("false_completion_rate", 0.0),
                "action_validity_rate": 1.0,
                "timeout_rate": source.get("timeout_rate", 0.0),
                "captcha_rate": source.get("captcha_rate", 0.0),
            },
            "tasks": [{"task_id": task_id} for task_id in task_ids],
        }
        report_path = tmp_path / "runs" / f"run-{index:02d}" / "results.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report), encoding="utf-8")
        ledger_records.append(
            evidence_record_from_report(report, report_path=report_path, history_path=ledger)
        )
    ledger.write_text(
        "".join(json.dumps(record) + "\n" for record in ledger_records), encoding="utf-8"
    )
    return load_slices([ledger])  # type: ignore[return-value]


def test_summarize_slices_tracks_dates_variance_and_manifest_drift() -> None:
    records = [
        {
            "created_at": "2026-08-29T00:00:00Z",
            "suite": "general",
            "model": "model-a",
            "manifest_sha256": "a",
            "success_rate": 0.5,
            "answer_grounding_rate": 0.75,
            "false_completion_rate": 0.25,
        },
        {
            "created_at": "2026-08-30T00:00:00Z",
            "suite": "general",
            "model": "model-a",
            "manifest_sha256": "b",
            "success_rate": 1.0,
            "answer_grounding_rate": 1.0,
            "false_completion_rate": 0.0,
        },
    ]

    summary = summarize_slices(records)["suites"]["general::model-a"]

    assert summary["distinct_dates"] == 2
    assert summary["mean_success_rate"] == 0.75
    assert summary["success_rate_population_stdev"] == 0.25
    assert summary["manifest_hashes"] == ["a", "b"]
    assert summary["model"] == "model-a"


def test_readiness_requires_two_models_three_common_verified_dates_and_thirty_tasks(
    tmp_path: Path,
) -> None:
    source = [
        _record(model, day)
        for model in ("model-a", "model-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]
    records = _verified_records(tmp_path, source)

    report = summarize_slices(records)

    assert report["ready"] is True
    assert report["readiness"]["general-v2"]["common_complete_dates"] == [
        "2026-08-28",
        "2026-08-29",
        "2026-08-30",
    ]


def test_same_day_repetitions_are_averaged_and_never_count_as_dates() -> None:
    records = [
        _record("model-a", "2026-08-29", run=1, success_rate=0.0),
        _record("model-a", "2026-08-29", run=2, success_rate=0.0),
        _record("model-a", "2026-08-30", run=1, success_rate=1.0),
    ]

    report = summarize_slices(records)  # type: ignore[arg-type]
    model = report["suites"]["general-v2::openrouter::model-a"]

    assert model["slice_count"] == 3
    assert model["distinct_dates"] == 2
    assert model["date_run_counts"]["2026-08-29"] == 2
    assert model["mean_success_rate"] == 0.5
    assert report["ready"] is False


def test_readiness_fails_closed_on_hash_drift_or_incomplete_cells() -> None:
    records = [
        _record(model, day)
        for model in ("model-a", "model-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]
    records[0]["manifest_sha256"] = "drift"
    records[-1]["task_count"] = 29

    readiness = summarize_slices(records)["readiness"]["general-v2"]  # type: ignore[arg-type]

    assert readiness["ready"] is False
    assert "openrouter::model-b@2026-08-30" in readiness["incomplete_cells"]
    assert any("manifest hash" in reason for reason in readiness["reasons"])


def test_readiness_fails_closed_on_source_hash_drift_or_missing_values(tmp_path: Path) -> None:
    source = [
        _record(model, day)
        for model in ("model-a", "model-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]
    source[0]["agent_source_sha256"] = "c" * 64
    source[-1]["benchmark_source_sha256"] = "unknown"

    records = _verified_records(tmp_path, source)
    readiness = summarize_slices(records)["readiness"]["general-v2"]

    assert readiness["ready"] is False
    assert any("agent source hash" in reason for reason in readiness["reasons"])
    assert any("benchmark source hash" in reason for reason in readiness["reasons"])


def test_readiness_rejects_more_than_three_models(tmp_path: Path) -> None:
    source = [
        _record(model, day)
        for model in ("model-a", "model-b", "model-c", "model-d")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]

    readiness = summarize_slices(_verified_records(tmp_path, source))["readiness"]["general-v2"]

    assert readiness["ready"] is False
    assert any("at most 3" in reason for reason in readiness["reasons"])


def test_readiness_fails_when_any_same_day_repetition_is_incomplete() -> None:
    records = [
        _record(model, day)
        for model in ("model-a", "model-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]
    records.append(_record("model-a", "2026-08-30", run=2, task_count=29))

    readiness = summarize_slices(records)["readiness"]["general-v2"]  # type: ignore[arg-type]

    assert readiness["ready"] is False
    assert "openrouter::model-a@2026-08-30" in readiness["incomplete_cells"]
    assert any("incomplete repetitions" in reason for reason in readiness["reasons"])


def test_same_model_on_different_providers_is_never_averaged_together(tmp_path: Path) -> None:
    source = [
        _record("shared-model", day, provider=provider)
        for provider in ("provider-a", "provider-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]

    report = summarize_slices(_verified_records(tmp_path, source))

    assert report["suites"]["general-v2::provider-a::shared-model"]["slice_count"] == 3
    assert report["suites"]["general-v2::provider-b::shared-model"]["slice_count"] == 3
    assert report["readiness"]["general-v2"]["models"] == [
        "provider-a::shared-model",
        "provider-b::shared-model",
    ]
    assert report["ready"] is False
    assert any(
        "benchmark config hash" in reason for reason in report["readiness"]["general-v2"]["reasons"]
    )


def test_readiness_fails_closed_when_provider_identity_is_missing(tmp_path: Path) -> None:
    source = [
        _record(model, day)
        for model in ("model-a", "model-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]
    source[0]["omit_provider"] = True

    readiness = summarize_slices(_verified_records(tmp_path, source))["readiness"]["general-v2"]

    assert readiness["ready"] is False
    assert any("unknown provider" in reason for reason in readiness["reasons"])


def test_readiness_binds_one_known_study_manifest(tmp_path: Path) -> None:
    source = [
        _record(model, day)
        for model in ("model-a", "model-b")
        for day in ("2026-08-28", "2026-08-29", "2026-08-30")
    ]
    source[0]["study_variant"] = "drifted"
    drifted = summarize_slices(_verified_records(tmp_path / "drift", source))["readiness"][
        "general-v2"
    ]

    source[0].pop("study_variant")
    source[0]["omit_study_identity"] = True
    missing = summarize_slices(_verified_records(tmp_path / "missing", source))["readiness"][
        "general-v2"
    ]

    assert drifted["ready"] is False
    assert missing["ready"] is False
    assert any("study manifest hash" in reason for reason in drifted["reasons"])
    assert any("study manifest hash" in reason for reason in missing["reasons"])

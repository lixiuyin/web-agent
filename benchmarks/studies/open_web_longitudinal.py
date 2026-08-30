"""Validate open-web evidence across real dates and multiple configured models."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.core import default_study_dir

_VERIFIED_EVIDENCE = object()


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _utc_date(created_at: str) -> str:
    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("benchmark report created_at must include a timezone")
    return timestamp.astimezone(UTC).date().isoformat()


def _report_path_for_record(report_path: Path, history_path: Path) -> str:
    resolved_report = report_path.resolve()
    evidence_root = _evidence_root(history_path)
    try:
        relative = resolved_report.relative_to(evidence_root)
    except ValueError as exc:
        raise ValueError("results.json must stay below the local ledger directory") from exc
    if resolved_report.name != "results.json":
        raise ValueError("benchmark evidence path must name results.json")
    return relative.as_posix()


def _evidence_root(history_path: Path) -> Path:
    """Resolve the study/execution root, while retaining legacy flat ledgers."""
    parent = history_path.resolve().parent
    return parent.parent if parent.name == "ledger" else parent


def _known_text(value: object) -> str:
    text = str(value or "").strip()
    return text if text and text != "unknown" else "unknown"


def _verified_study_identity(
    metadata: dict[str, Any],
    benchmark_config: dict[str, Any],
    *,
    history_path: Path,
    report_suite: str,
    report_model: str,
    task_manifest_sha256: str,
) -> tuple[str, str, str]:
    """Verify optional v1 study identity while accepting legacy non-study reports."""
    provider = _known_text(metadata.get("provider"))
    config_provider = _known_text(benchmark_config.get("provider"))
    if provider != config_provider:
        raise ValueError("benchmark provider differs between metadata and benchmark_config")

    study_id = _known_text(metadata.get("study_id"))
    study_hash = _known_text(metadata.get("study_manifest_sha256"))
    config_study_hash = _known_text(benchmark_config.get("study_manifest_sha256"))
    manifest_value = metadata.get("study_manifest")
    absent = study_id == study_hash == config_study_hash == "unknown" and (
        manifest_value is None or manifest_value == ""
    )
    if absent:
        return provider, "unknown", "unknown"
    if (
        "unknown" in {provider, study_id, study_hash, config_study_hash}
        or study_hash != config_study_hash
        or not isinstance(manifest_value, str)
        or not manifest_value
    ):
        raise ValueError("benchmark report contains incomplete study manifest identity")
    if re.fullmatch(r"[0-9a-f]{64}", study_hash) is None:
        raise ValueError("study_manifest_sha256 must be a lowercase SHA-256 digest")

    study_path = Path(manifest_value)
    if not study_path.is_absolute() or not study_path.is_file():
        raise ValueError("study manifest path must be an existing absolute file")
    try:
        study_path.resolve().relative_to(_evidence_root(history_path))
    except ValueError as exc:
        raise ValueError(
            "retained study manifest must stay below the ledger evidence root"
        ) from exc
    raw = study_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != study_hash:
        raise ValueError("study_manifest_sha256 does not match the retained study manifest")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("retained study manifest must contain an object")
    if _known_text(payload.get("study_id")) != study_id:
        raise ValueError("report study_id does not match the retained study manifest")
    if str(payload.get("suite", "unknown")) != report_suite:
        raise ValueError("report suite does not match the retained study manifest")
    if str(payload.get("task_manifest_sha256", "unknown")) != task_manifest_sha256:
        raise ValueError("task manifest hash does not match the retained study manifest")
    models = payload.get("models")
    if not isinstance(models, list) or (provider, report_model) not in {
        (_known_text(item.get("provider")), _known_text(item.get("model")))
        for item in models
        if isinstance(item, dict)
    }:
        raise ValueError("report provider/model is not preregistered by the study manifest")
    return provider, study_id, study_hash


def evidence_record_from_report(
    report: dict[str, Any],
    *,
    report_path: Path,
    history_path: Path,
) -> dict[str, Any]:
    """Bind one local ledger row to a retained, internally consistent results.json."""
    resolved_report = report_path.resolve()
    if not resolved_report.is_file():
        raise ValueError(f"benchmark results evidence does not exist: {resolved_report}")
    persisted = json.loads(resolved_report.read_text(encoding="utf-8"))
    if persisted != report:
        raise ValueError("in-memory report differs from retained results.json")

    metadata = report.get("metadata")
    summary = report.get("summary")
    tasks = report.get("tasks")
    if (
        not isinstance(metadata, dict)
        or not isinstance(summary, dict)
        or not isinstance(tasks, list)
    ):
        raise ValueError("benchmark report must contain metadata, summary, and tasks")

    manifest_value = metadata.get("manifest")
    if not isinstance(manifest_value, str) or not manifest_value:
        raise ValueError("benchmark report must record an absolute manifest path")
    manifest_path = Path(manifest_value)
    if not manifest_path.is_absolute() or not manifest_path.is_file():
        raise ValueError("benchmark report manifest path must be an existing absolute file")
    try:
        manifest_path.resolve().relative_to(_evidence_root(history_path))
    except ValueError as exc:
        raise ValueError("retained manifest must stay below the local ledger directory") from exc
    manifest_raw = manifest_path.read_bytes()
    computed_manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    manifest_hash = str(metadata.get("manifest_sha256", "unknown"))
    if computed_manifest_hash != manifest_hash:
        raise ValueError("manifest_sha256 does not match the retained manifest")

    benchmark_config = metadata.get("benchmark_config")
    if not isinstance(benchmark_config, dict):
        raise ValueError("benchmark report must include benchmark_config evidence")
    config_hash = str(metadata.get("benchmark_config_sha256", "unknown"))
    if _canonical_sha256(benchmark_config) != config_hash:
        raise ValueError("benchmark_config_sha256 does not match benchmark_config")
    if benchmark_config.get("manifest_sha256") != manifest_hash:
        raise ValueError("benchmark_config is not bound to the retained manifest")

    report_suite = str(report.get("suite", "unknown"))
    report_model = _known_text(metadata.get("model"))
    provider, study_id, study_manifest_hash = _verified_study_identity(
        metadata,
        benchmark_config,
        history_path=history_path,
        report_suite=report_suite,
        report_model=report_model,
        task_manifest_sha256=manifest_hash,
    )

    task_ids = sorted(str(task.get("task_id", "")) for task in tasks if isinstance(task, dict))
    if len(task_ids) != len(tasks) or any(not task_id for task_id in task_ids):
        raise ValueError("benchmark report contains a task without a task_id")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("benchmark report contains duplicate task IDs")
    task_ids_hash = _canonical_sha256(task_ids)
    if str(metadata.get("task_ids_sha256", "unknown")) != task_ids_hash:
        raise ValueError("task_ids_sha256 does not match report tasks")
    if int(summary.get("task_count", -1)) != len(task_ids):
        raise ValueError("summary task_count does not match report tasks")
    manifest_payload = json.loads(manifest_raw)
    manifest_tasks = manifest_payload.get("tasks") if isinstance(manifest_payload, dict) else None
    if not isinstance(manifest_tasks, list):
        raise ValueError("retained manifest must contain a tasks list")
    manifest_task_ids = sorted(
        str(task.get("id", "")) for task in manifest_tasks if isinstance(task, dict)
    )
    if (
        len(manifest_task_ids) != len(manifest_tasks)
        or any(not task_id for task_id in manifest_task_ids)
        or manifest_task_ids != task_ids
    ):
        raise ValueError("report task IDs do not match the retained manifest")
    manifest_suite = str(manifest_payload.get("suite", manifest_path.stem))
    if report_suite != manifest_suite:
        raise ValueError("report suite does not match the retained manifest")

    created_at = str(report.get("created_at", ""))
    benchmark_date = _utc_date(created_at)
    record = {
        "schema_version": 3,
        "evidence_kind": "local-report-bound-v1",
        "run_id": str(metadata.get("run_id", "unknown")),
        "created_at": created_at,
        "benchmark_date": benchmark_date,
        "suite": report_suite,
        "provider": provider,
        "model": report_model,
        "study_id": study_id,
        "study_manifest_sha256": study_manifest_hash,
        "manifest_sha256": manifest_hash,
        "benchmark_config_sha256": config_hash,
        "task_ids_sha256": task_ids_hash,
        "agent_source_sha256": str(metadata.get("agent_source_sha256", "unknown")),
        "report_path": _report_path_for_record(resolved_report, history_path),
        "report_sha256": _canonical_sha256(report),
        "task_count": len(task_ids),
        "task_ids": task_ids,
        "success_rate": float(summary.get("success_rate", 0.0)),
        "answer_grounding_rate": float(summary.get("answer_grounding_rate", 0.0)),
        "false_completion_rate": float(summary.get("false_completion_rate", 0.0)),
        "action_validity_rate": float(summary.get("action_validity_rate", 0.0)),
        "timeout_rate": float(summary.get("timeout_rate", 0.0)),
        "captcha_rate": float(summary.get("captcha_rate", 0.0)),
        "blocked_rate": float(summary.get("blocked_rate", 0.0)),
        "max_steps_rate": float(summary.get("max_steps_rate", 0.0)),
        "p95_duration_seconds": float(summary.get("p95_duration_seconds", 0.0)),
        "termination_reason_counts": summary.get("termination_reason_counts", {}),
    }
    if "benchmark_source_sha256" in metadata:
        record["schema_version"] = 4
        record["benchmark_source_sha256"] = str(metadata["benchmark_source_sha256"])
    if study_manifest_hash != "unknown":
        record["schema_version"] = 5
    return record


def verify_evidence_record(
    record: dict[str, Any], *, history_path: Path, line_number: int
) -> dict[str, Any]:
    report_value = record.get("report_path")
    if not isinstance(report_value, str) or not report_value:
        raise ValueError(f"{history_path}:{line_number}: missing report_path evidence")
    evidence_root = _evidence_root(history_path)
    report_path = (evidence_root / report_value).resolve()
    try:
        report_path.relative_to(evidence_root)
    except ValueError as exc:
        raise ValueError(
            f"{history_path}:{line_number}: report_path escapes the ledger directory"
        ) from exc
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("results.json must contain an object")
        expected = evidence_record_from_report(
            report,
            report_path=report_path,
            history_path=history_path,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{history_path}:{line_number}: invalid report evidence: {exc}") from exc
    mismatches = [key for key, value in expected.items() if record.get(key) != value]
    if mismatches:
        raise ValueError(
            f"{history_path}:{line_number}: ledger/report evidence mismatch: {mismatches}"
        )
    return {
        **record,
        "_evidence_token": _VERIFIED_EVIDENCE,
        "_verified_report_path": str(report_path),
    }


def load_slices(paths: Sequence[Path]) -> list[dict[str, Any]]:
    """Load only rows whose retained results.json and local evidence still agree."""
    records: list[dict[str, Any]] = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError(f"{path}:{line_number}: ledger row must be an object")
                record["history_file"] = str(path)
                records.append(
                    verify_evidence_record(record, history_path=path, line_number=line_number)
                )
    return records


def _benchmark_date(record: dict[str, Any]) -> str:
    return _utc_date(str(record["created_at"]))


def _date_means(values: list[dict[str, Any]], key: str) -> tuple[list[float], dict[str, int]]:
    by_date: defaultdict[str, list[float]] = defaultdict(list)
    for item in values:
        by_date[_benchmark_date(item)].append(float(item.get(key, 0.0)))
    return (
        [statistics.fmean(by_date[day]) for day in sorted(by_date)],
        {day: len(by_date[day]) for day in sorted(by_date)},
    )


def _endpoint_label(provider: str, model: str) -> str:
    return f"{provider}::{model}"


def _model_summary(
    suite: str,
    provider: str,
    model: str,
    values: list[dict[str, Any]],
) -> dict[str, Any]:
    success_rates, date_run_counts = _date_means(values, "success_rate")
    answer_rates, _ = _date_means(values, "answer_grounding_rate")
    false_completion_rates, _ = _date_means(values, "false_completion_rate")
    timeout_rates, _ = _date_means(values, "timeout_rate")
    captcha_rates, _ = _date_means(values, "captcha_rate")
    dates = sorted(date_run_counts)
    return {
        "suite": suite,
        "provider": provider,
        "model": model,
        "endpoint_id": _endpoint_label(provider, model),
        "slice_count": len(values),
        "distinct_dates": len(dates),
        "dates": dates,
        "date_run_counts": date_run_counts,
        "mean_success_rate": statistics.fmean(success_rates),
        "minimum_success_rate": min(success_rates),
        "maximum_success_rate": max(success_rates),
        "success_rate_range": max(success_rates) - min(success_rates),
        "success_rate_population_stdev": statistics.pstdev(success_rates),
        "mean_answer_grounding_rate": statistics.fmean(answer_rates),
        "mean_false_completion_rate": statistics.fmean(false_completion_rates),
        "mean_timeout_rate": statistics.fmean(timeout_rates),
        "mean_captcha_rate": statistics.fmean(captcha_rates),
        "manifest_hashes": sorted({str(item.get("manifest_sha256", "unknown")) for item in values}),
        "config_hashes": sorted(
            {str(item.get("benchmark_config_sha256", "unknown")) for item in values}
        ),
        "task_ids_hashes": sorted({str(item.get("task_ids_sha256", "unknown")) for item in values}),
        "agent_source_hashes": sorted(
            {str(item.get("agent_source_sha256", "unknown")) for item in values}
        ),
        "benchmark_source_hashes": sorted(
            {str(item.get("benchmark_source_sha256", "unknown")) for item in values}
        ),
        "study_manifest_hashes": sorted(
            {str(item.get("study_manifest_sha256", "unknown")) for item in values}
        ),
    }


def _suite_readiness(
    suite: str,
    values: list[dict[str, Any]],
    *,
    minimum_distinct_dates: int,
    minimum_models: int,
    maximum_models: int,
    expected_task_count: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    if any(item.get("_evidence_token") is not _VERIFIED_EVIDENCE for item in values):
        reasons.append("requires verified retained results.json evidence for every record")
    identities = sorted(
        {(_known_text(item.get("provider")), _known_text(item.get("model"))) for item in values}
    )
    if any(provider == "unknown" for provider, _model in identities):
        reasons.append("one or more records have an unknown provider")
    if any(model == "unknown" for _provider, model in identities):
        reasons.append("one or more records have an unknown model")
    valid_identities = [identity for identity in identities if "unknown" not in identity]
    if len(valid_identities) < minimum_models:
        reasons.append(f"requires at least {minimum_models} distinct provider/model endpoints")
    if len(valid_identities) > maximum_models:
        reasons.append(f"allows at most {maximum_models} distinct provider/model endpoints")

    for field, label in (
        ("manifest_sha256", "manifest"),
        ("benchmark_config_sha256", "benchmark config"),
        ("task_ids_sha256", "task set"),
        ("agent_source_sha256", "agent source"),
        ("benchmark_source_sha256", "benchmark source"),
        ("study_manifest_sha256", "study manifest"),
    ):
        hashes = {str(item.get(field, "unknown")) for item in values}
        source_hash = field in {
            "agent_source_sha256",
            "benchmark_source_sha256",
            "study_manifest_sha256",
        }
        invalid_source_hash = source_hash and any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in hashes
        )
        if "unknown" in hashes or len(hashes) != 1 or invalid_source_hash:
            reasons.append(f"requires one known {label} hash")

    run_ids = [str(item.get("run_id", "unknown")) for item in values]
    duplicate_run_ids = sorted(
        run_id for run_id, count in Counter(run_ids).items() if run_id != "unknown" and count > 1
    )
    if duplicate_run_ids:
        reasons.append("duplicate run_id records are present")

    complete_dates: dict[str, set[str]] = {}
    incomplete_cells: list[str] = []
    for provider, model in valid_identities:
        endpoint = _endpoint_label(provider, model)
        model_values = [
            item
            for item in values
            if _known_text(item.get("provider")) == provider
            and _known_text(item.get("model")) == model
        ]
        dates = sorted({_benchmark_date(item) for item in model_values})
        complete: set[str] = set()
        for day in dates:
            cell = [item for item in model_values if _benchmark_date(item) == day]
            if cell and all(
                int(item.get("task_count", -1)) == expected_task_count for item in cell
            ):
                complete.add(day)
            else:
                incomplete_cells.append(f"{endpoint}@{day}")
        complete_dates[endpoint] = complete
        if len(complete) < minimum_distinct_dates:
            reasons.append(
                f"endpoint {endpoint} has {len(complete)} complete dates; "
                f"requires {minimum_distinct_dates}"
            )

    if incomplete_cells:
        reasons.append("one or more model/date cells contain incomplete repetitions")

    common_dates = (
        sorted(set.intersection(*(complete_dates[key] for key in complete_dates)))
        if complete_dates
        else []
    )
    if len(common_dates) < minimum_distinct_dates:
        reasons.append(
            f"model matrix has {len(common_dates)} common complete dates; "
            f"requires {minimum_distinct_dates}"
        )
    return {
        "suite": suite,
        "ready": not reasons,
        "models": [_endpoint_label(*identity) for identity in valid_identities],
        "model_count": len(valid_identities),
        "common_complete_dates": common_dates,
        "incomplete_cells": sorted(incomplete_cells),
        "duplicate_run_ids": duplicate_run_ids,
        "reasons": reasons,
    }


def summarize_slices(
    records: list[dict[str, Any]],
    *,
    minimum_distinct_dates: int = 3,
    minimum_models: int = 2,
    maximum_models: int = 3,
    expected_task_count: int = 30,
) -> dict[str, Any]:
    if minimum_models < 2 or maximum_models < minimum_models or maximum_models > 3:
        raise ValueError("model bounds must satisfy 2 <= minimum_models <= maximum_models <= 3")
    if minimum_distinct_dates < 3:
        raise ValueError("longitudinal readiness requires at least three distinct dates")
    if expected_task_count != 30:
        raise ValueError("open-web longitudinal readiness requires exactly 30 tasks")
    by_suite_model: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_suite: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        suite = str(record["suite"])
        provider = _known_text(record.get("provider"))
        model = _known_text(record.get("model"))
        by_suite_model[(suite, provider, model)].append(record)
        by_suite[suite].append(record)

    suites = {
        (
            f"{suite}::{model}" if provider == "unknown" else f"{suite}::{provider}::{model}"
        ): _model_summary(suite, provider, model, values)
        for (suite, provider, model), values in sorted(by_suite_model.items())
    }
    readiness = {
        suite: _suite_readiness(
            suite,
            values,
            minimum_distinct_dates=minimum_distinct_dates,
            minimum_models=minimum_models,
            maximum_models=maximum_models,
            expected_task_count=expected_task_count,
        )
        for suite, values in sorted(by_suite.items())
    }
    ready = bool(readiness) and all(item["ready"] for item in readiness.values())
    return {
        "schema_version": 3,
        "record_count": len(records),
        "minimum_distinct_dates_required": minimum_distinct_dates,
        "minimum_models_required": minimum_models,
        "maximum_models_allowed": maximum_models,
        "expected_task_count": expected_task_count,
        "ready": ready,
        "suites": suites,
        "readiness": readiness,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", nargs="+", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_study_dir("open-web-longitudinal") / "reports" / "longitudinal.json",
    )
    parser.add_argument("--minimum-distinct-dates", type=int, default=3)
    parser.add_argument("--minimum-models", type=int, default=2)
    parser.add_argument("--expected-task-count", type=int, default=30)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    report = summarize_slices(
        load_slices(args.history),
        minimum_distinct_dates=args.minimum_distinct_dates,
        minimum_models=args.minimum_models,
        maximum_models=3,
        expected_task_count=args.expected_task_count,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["readiness"], ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()


__all__ = [
    "evidence_record_from_report",
    "load_slices",
    "summarize_slices",
    "verify_evidence_record",
]

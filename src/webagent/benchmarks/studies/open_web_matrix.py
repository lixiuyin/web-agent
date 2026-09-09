"""Collect one honest current-date open-web slice for two or three models."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from webagent.benchmarks.core import (
    allocate_execution_dir,
    default_study_dir,
    initialize_matrix_study,
    packaged_manifest_path,
)
from webagent.benchmarks.studies.open_web_longitudinal import (
    evidence_record_from_report,
    load_slices,
    summarize_slices,
    verify_evidence_record,
)
from webagent.benchmarks.suites.open_web.runner import load_manifest
from webagent.core.config import AgentConfig
from webagent.evaluation import (
    StudyBudgets,
    StudyCondition,
    StudyLayout,
    StudyManifest,
    StudyModel,
)
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint


def ledger_record_from_report(
    report: dict[str, Any],
    *,
    report_path: Path,
    ledger_path: Path,
) -> dict[str, Any]:
    return evidence_record_from_report(
        report,
        report_path=report_path,
        history_path=ledger_path,
    )


def append_ledger(path: Path, record: dict[str, Any]) -> None:
    """Append one verified local report binding once; wall-clock time remains unattested."""
    run_id = str(record.get("run_id", "unknown"))
    if run_id == "unknown":
        raise ValueError("matrix records require a known run_id")
    existing = load_slices([path]) if path.is_file() else []
    if any(str(item.get("run_id")) == run_id for item in existing):
        raise ValueError(f"duplicate run_id in matrix ledger: {run_id}")
    verify_evidence_record(record, history_path=path, line_number=len(existing) + 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=packaged_manifest_path("open_web_general.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_study_dir("open-web-model-matrix"),
    )
    parser.add_argument("--shards", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--model-order",
        choices=("as-given", "reverse", "rotate-by-date"),
        default="rotate-by-date",
    )
    parser.add_argument(
        "--require-new-date",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Refuse another matrix slice for a UTC date already present in this study",
    )
    parser.add_argument("--max-steps-per-task", type=int, default=8)
    parser.add_argument("--discovery-max-steps-per-task", type=int, default=12)
    parser.add_argument("--direct-task-timeout-seconds", type=int, default=600)
    parser.add_argument("--discovery-task-timeout-seconds", type=int, default=2400)
    parser.add_argument("--captcha-handling", choices=("fail", "report"), default="fail")
    parser.add_argument("--minimum-success-rate", type=float, default=0.0)
    parser.add_argument("--maximum-false-completion-rate", type=float, default=1.0)
    return parser.parse_args(argv)


def run_matrix(args: argparse.Namespace) -> int:
    context = _prepare_matrix(args)
    collected, acceptable = _collect_matrix_runs(args, context)
    longitudinal = summarize_slices(load_slices([context.ledger_path]))
    _write_matrix_snapshot(args, context, collected, longitudinal)
    print(json.dumps(longitudinal["readiness"], ensure_ascii=False, indent=2))
    return 0 if acceptable else 1


@dataclass(frozen=True, slots=True)
class _MatrixContext:
    requested_models: list[str]
    models: list[str]
    manifest: Path
    suite: str
    manifest_hash: str
    expected_task_ids: list[str]
    output: Path
    study_id: str
    layout: StudyLayout
    study_manifest_sha256: str
    ledger_path: Path
    collection_date: str
    started_at: datetime
    batch_id: str
    discovery_max_steps: int


def _prepare_matrix(args: argparse.Namespace) -> _MatrixContext:
    requested_models = list(dict.fromkeys(str(model) for model in args.models))
    discovery_max_steps = int(getattr(args, "discovery_max_steps_per_task", 12))
    if not 2 <= len(requested_models) <= 3:
        raise ValueError("open-web matrix requires two or three distinct models")
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive")
    if args.direct_task_timeout_seconds < 1:
        raise ValueError("--direct-task-timeout-seconds must be positive")
    if args.discovery_task_timeout_seconds < 1:
        raise ValueError("--discovery-task-timeout-seconds must be positive")
    manifest = args.manifest.resolve()
    suite, tasks, manifest_hash = load_manifest(manifest)
    if len(tasks) != 30:
        raise ValueError("open-web maturity matrix requires exactly 30 manifest tasks")
    started_at = datetime.now(UTC)
    models = _ordered_models(
        requested_models,
        started_at.date(),
        str(getattr(args, "model_order", "rotate-by-date")),
    )
    output = args.output.resolve()
    study_id = output.name
    defaults = AgentConfig()
    source_sha256 = hashlib.sha256(
        (agent_source_fingerprint() + benchmark_source_fingerprint()).encode("ascii")
    ).hexdigest()
    study_manifest = StudyManifest(
        study_id=study_id,
        title="Open-web model comparison and held-out transfer",
        research_questions=(
            "How do model-level success, failures, and calibration transfer to held-out tasks and settings?",
        ),
        suite=suite,
        task_manifest_sha256=manifest_hash,
        task_split_counts=dict(Counter(task.split for task in tasks)),
        models=tuple(StudyModel(provider=args.provider, model=model) for model in requested_models),
        conditions=(
            StudyCondition(
                id="browser-grounded",
                kind="baseline",
                description="Browser-grounded open-web agent with retained execution evidence",
                config_overrides={
                    "task_step_budgets": {
                        "default": args.max_steps_per_task,
                        "discovery_required": discovery_max_steps,
                    },
                    "task_timeout_seconds": {
                        "default": args.direct_task_timeout_seconds,
                        "discovery_required": args.discovery_task_timeout_seconds,
                    },
                },
            ),
        ),
        repetitions=args.repetitions,
        budgets=StudyBudgets(
            max_steps=max(args.max_steps_per_task, discovery_max_steps),
            task_timeout_seconds=max(
                args.direct_task_timeout_seconds,
                args.discovery_task_timeout_seconds,
            ),
            tool_timeout_seconds=defaults.tool_timeout,
            planner_max_tokens=6000,
        ),
        primary_metrics=("success_rate", "held_out_transfer"),
        secondary_metrics=("false_completion_rate", "calibration", "failure_modes"),
        confidence_target="task_success",
        source_sha256=source_sha256,
        created_at=started_at,
    )
    initialize_matrix_study(output, study_manifest, task_manifest_bytes=manifest.read_bytes())
    layout = StudyLayout.from_root(output)
    study_manifest_sha256 = hashlib.sha256(layout.manifest_path.read_bytes()).hexdigest()
    ledger_path = layout.time_slices_path
    collection_date = started_at.date().isoformat()
    _reject_duplicate_date(args, ledger_path, collection_date)
    return _MatrixContext(
        requested_models=requested_models,
        models=models,
        manifest=manifest,
        suite=suite,
        manifest_hash=manifest_hash,
        expected_task_ids=sorted(task.id for task in tasks),
        output=output,
        study_id=study_id,
        layout=layout,
        study_manifest_sha256=study_manifest_sha256,
        ledger_path=ledger_path,
        collection_date=collection_date,
        started_at=started_at,
        batch_id=started_at.strftime("%Y%m%dT%H%M%S%fZ"),
        discovery_max_steps=discovery_max_steps,
    )


def _reject_duplicate_date(
    args: argparse.Namespace, ledger_path: Path, collection_date: str
) -> None:
    if not bool(getattr(args, "require_new_date", True)) or not ledger_path.is_file():
        return
    existing_dates = {str(item["benchmark_date"]) for item in load_slices([ledger_path])}
    if collection_date in existing_dates:
        raise RuntimeError(
            f"study already contains a real slice for {collection_date}; wait for a new UTC "
            "date or pass --no-require-new-date for an explicit same-day repetition"
        )


def _collect_matrix_runs(
    args: argparse.Namespace, context: _MatrixContext
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    collected: dict[str, list[dict[str, Any]]] = {model: [] for model in context.models}
    acceptable = True
    for model in context.models:
        for repetition in range(1, args.repetitions + 1):
            record = _collect_matrix_run(args, context, model, repetition)
            append_ledger(context.ledger_path, record)
            collected[model].append(record)
            acceptable &= bool(
                record["success_rate"] >= args.minimum_success_rate
                and record["false_completion_rate"] <= args.maximum_false_completion_rate
            )
    return collected, acceptable


def _collect_matrix_run(
    args: argparse.Namespace, context: _MatrixContext, model: str, repetition: int
) -> dict[str, Any]:
    run_dir = allocate_execution_dir(
        context.output,
        model=model,
        condition="browser-grounded",
        now=context.started_at,
        execution_id=f"{context.batch_id}-r{repetition:02d}",
    )
    command = _matrix_command(args, context, model, run_dir, repetition)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    process_log = context.layout.logs_dir / (
        f"{context.batch_id}-{model.replace('/', '-')}-r{repetition:02d}.log"
    )
    process_log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    result_path = run_dir / "results.json"
    if not result_path.is_file():
        raise RuntimeError(
            f"model {model} repetition {repetition} exited {completed.returncode} "
            "without results.json"
        )
    report = cast(dict[str, Any], json.loads(result_path.read_text(encoding="utf-8")))
    record = ledger_record_from_report(
        report, report_path=result_path, ledger_path=context.ledger_path
    )
    _validate_matrix_record(
        record,
        collection_date=context.collection_date,
        model=model,
        provider=args.provider,
        study_manifest_sha256=context.study_manifest_sha256,
        suite=context.suite,
        manifest_hash=context.manifest_hash,
        expected_task_ids=context.expected_task_ids,
    )
    return record


def _matrix_command(
    args: argparse.Namespace,
    context: _MatrixContext,
    model: str,
    run_dir: Path,
    repetition: int,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "webagent.benchmarks.suites.open_web.parallel",
        "--manifest",
        str(context.manifest),
        "--output",
        str(run_dir),
        "--model",
        model,
        "--shards",
        str(args.shards),
        "--max-steps-per-task",
        str(args.max_steps_per_task),
        "--discovery-max-steps-per-task",
        str(context.discovery_max_steps),
        "--direct-task-timeout-seconds",
        str(args.direct_task_timeout_seconds),
        "--discovery-task-timeout-seconds",
        str(args.discovery_task_timeout_seconds),
        "--captcha-handling",
        args.captcha_handling,
        "--study-root",
        str(context.output),
        "--study-id",
        context.study_id,
        "--provider",
        args.provider,
        "--condition-id",
        "browser-grounded",
        "--repetition",
        str(repetition),
    ]


def _write_matrix_snapshot(
    args: argparse.Namespace,
    context: _MatrixContext,
    collected: dict[str, list[dict[str, Any]]],
    longitudinal: dict[str, Any],
) -> None:
    matrix = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "collection_date": context.collection_date,
        "batch_id": context.batch_id,
        "provider": args.provider,
        "requested_models": context.requested_models,
        "execution_model_order": context.models,
        "model_order_policy": str(getattr(args, "model_order", "rotate-by-date")),
        "study_manifest_sha256": context.study_manifest_sha256,
        "repetitions": args.repetitions,
        "runs": collected,
        "longitudinal_ready": longitudinal["ready"],
        "longitudinal_readiness": longitudinal["readiness"],
        "evidence_notice": (
            "This command records only the current UTC date. Re-run on future real dates; "
            "same-day repetitions never count as additional dates. Retained local reports are "
            "hash-bound, but local files do not independently attest wall-clock truth."
        ),
    }
    snapshot = context.layout.matrix_snapshots_dir / f"{context.batch_id}.json"
    with snapshot.open("x", encoding="utf-8") as handle:
        json.dump(matrix, handle, ensure_ascii=False, indent=2)
    latest_payload = {
        "schema_version": 1,
        "semantics": "compatibility pointer to the latest immutable matrix snapshot",
        "snapshot_path": snapshot.relative_to(context.layout.root).as_posix(),
        "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    }
    temporary = context.layout.matrix_latest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(latest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(context.layout.matrix_latest_path)


def _ordered_models(models: list[str], collection_date: date, policy: str) -> list[str]:
    """Counterbalance serial provider effects without random, unrecorded ordering."""
    ordered = list(models)
    if policy == "as-given":
        return ordered
    if policy == "reverse":
        return list(reversed(ordered))
    if policy != "rotate-by-date":
        raise ValueError(f"unsupported model order policy: {policy}")
    offset = collection_date.toordinal() % len(ordered)
    return ordered[offset:] + ordered[:offset]


def main() -> None:
    raise SystemExit(run_matrix(parse_args()))


__all__ = ["_ordered_models", "append_ledger", "ledger_record_from_report", "run_matrix"]


def _validate_matrix_record(
    record: dict[str, Any],
    *,
    collection_date: str,
    model: str,
    provider: str,
    study_manifest_sha256: str,
    suite: str,
    manifest_hash: str,
    expected_task_ids: list[str],
) -> None:
    if record["benchmark_date"] != collection_date:
        raise RuntimeError("report timestamp crossed the current UTC collection date")
    if record["model"] != model:
        raise RuntimeError("matrix report model differs from the requested model")
    if record["provider"] != provider:
        raise RuntimeError("matrix report provider differs from the requested provider")
    if record["study_manifest_sha256"] != study_manifest_sha256:
        raise RuntimeError("matrix report differs from the immutable study manifest")
    if record["suite"] != suite or record["manifest_sha256"] != manifest_hash:
        raise RuntimeError("matrix report differs from the requested manifest")
    if record["task_ids"] != expected_task_ids:
        raise RuntimeError("matrix report task set differs from the requested manifest")
    if record["task_count"] != 30:
        raise RuntimeError("matrix run did not return all 30 tasks")


if __name__ == "__main__":
    main()

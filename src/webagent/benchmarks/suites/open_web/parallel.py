"""Run one open-web manifest in isolated parallel shards and merge it exactly once."""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from webagent.benchmarks.core import (
    add_study_run_arguments,
    allocate_execution_dir,
    default_study_dir,
    packaged_manifest_path,
    study_context_from_args,
)
from webagent.benchmarks.suites.open_web.runner import (
    append_time_slice,
    canonical_sha256,
    load_manifest,
    retain_manifest_snapshot,
    retain_report_snapshot,
    retain_study_manifest_snapshot,
)
from webagent.evaluation import (
    BenchmarkReport,
    ResearchAnalyses,
    StudyExecutionLayout,
    StudyRunContext,
    TaskEvaluation,
    aggregate_evaluations,
    analyze_calibration,
    analyze_failures,
    analyze_generality,
    analyze_long_horizon,
    analyze_transfer,
    publish_study_run_records,
    validate_study_task_set,
)

_LINK_FALLBACK_ERRORS = {
    errno.EXDEV,
    errno.EPERM,
    errno.EACCES,
    getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
    errno.EOPNOTSUPP,
}


def _link_or_copy(source: str, target: str) -> str:
    """Share immutable shard bytes locally, falling back across filesystems."""
    try:
        os.link(source, target)
    except OSError as exc:
        if exc.errno not in _LINK_FALLBACK_ERRORS:
            raise
        shutil.copy2(source, target)
    return target


def bind_study_identity(
    report: BenchmarkReport,
    *,
    context: StudyRunContext,
    output_dir: Path,
) -> BenchmarkReport:
    """Bind a merged report to its explicit provider and immutable study contract."""
    study_manifest_path, study_manifest_sha256 = retain_study_manifest_snapshot(
        context,
        output_dir=output_dir,
    )
    metadata = dict(report.metadata)
    benchmark_config = cast(dict[str, Any], dict(metadata["benchmark_config"]))
    benchmark_config.update(
        {
            "provider": context.provider,
            "study_manifest_sha256": study_manifest_sha256,
        }
    )
    metadata.update(
        {
            "provider": context.provider,
            "study_id": context.study_id,
            "study_manifest": study_manifest_path.relative_to(output_dir).as_posix(),
            "study_manifest_sha256": study_manifest_sha256,
            "benchmark_config": benchmark_config,
            "benchmark_config_sha256": canonical_sha256(benchmark_config),
        }
    )
    return report.model_copy(update={"metadata": metadata})


def merge_shard_reports(
    reports: list[dict[str, Any]],
    *,
    expected_task_ids: set[str],
) -> BenchmarkReport:
    if not reports:
        raise ValueError("no shard reports supplied")
    first = reports[0]
    metadata = cast(dict[str, Any], first.get("metadata", {}))
    identity = {
        key: metadata.get(key)
        for key in (
            "manifest_sha256",
            "benchmark_config_sha256",
            "task_ids_sha256",
            "provider",
            "model",
            "stealth_mode",
            "agent_source_sha256",
            "benchmark_source_sha256",
            "discovery_mode",
            "high_risk_action_policy",
            "captcha_handling",
            "max_steps_per_task",
            "discovery_max_steps_per_task",
            "direct_task_timeout",
            "discovery_task_timeout",
        )
    }
    tasks: list[TaskEvaluation] = []
    seen: set[str] = set()
    for report in reports:
        if report.get("suite") != first.get("suite"):
            raise ValueError("shard suites differ")
        shard_metadata = cast(dict[str, Any], report.get("metadata", {}))
        if any(shard_metadata.get(key) != value for key, value in identity.items()):
            raise ValueError("shard provenance differs")
        for raw_task in cast(list[dict[str, Any]], report.get("tasks", [])):
            task = TaskEvaluation.model_validate(raw_task)
            if task.task_id in seen:
                raise ValueError(f"duplicate task across shards: {task.task_id}")
            seen.add(task.task_id)
            tasks.append(task)
    if seen != expected_task_ids:
        missing = sorted(expected_task_ids - seen)
        unexpected = sorted(seen - expected_task_ids)
        raise ValueError(
            f"shard task coverage mismatch: missing={missing}, unexpected={unexpected}"
        )
    merged_metadata = {
        **metadata,
        "run_id": str(uuid.uuid4()),
        "parallel_shards": len(reports),
        "canonical_task_run_paths": {
            task.task_id: f"runs/{task.task_id}"
            for task in sorted(tasks, key=lambda item: item.task_id)
        },
        "shard_count": None,
        "shard_index": None,
        "shard_discovery_task_count": None,
    }
    return BenchmarkReport(
        suite=str(first["suite"]),
        created_at=datetime.now(UTC).isoformat(),
        metadata=merged_metadata,
        summary=aggregate_evaluations(tasks),
        tasks=tasks,
        research=ResearchAnalyses(
            failures=analyze_failures(tasks),
            calibration=analyze_calibration(tasks),
            transfer=analyze_transfer(tasks),
            generality=analyze_generality(tasks),
            long_horizon=analyze_long_horizon(tasks),
        ),
    )


def publish_shard_task_runs(
    layout: StudyExecutionLayout,
    reports: list[dict[str, Any]],
) -> None:
    """Publish complete shard task evidence into the canonical execution namespace."""
    layout.require_prepared()
    planned: list[tuple[Path, Path, str]] = []
    seen: set[str] = set()
    for shard_index, report in enumerate(reports):
        for raw_task in cast(list[dict[str, Any]], report.get("tasks", [])):
            task = TaskEvaluation.model_validate(raw_task)
            if task.task_id in seen:
                raise ValueError(f"duplicate task across shard run evidence: {task.task_id}")
            seen.add(task.task_id)
            source = layout.shards_dir / f"shard-{shard_index:02d}" / "runs" / task.task_id
            target = layout.task_run(task.task_id).root
            source_evaluation = source / "evaluation" / "task.json"
            if not source.is_dir() or not source_evaluation.is_file():
                raise ValueError(f"shard task run evidence is missing: {source}")
            persisted = TaskEvaluation.model_validate_json(source_evaluation.read_bytes())
            if persisted != task:
                raise ValueError(f"shard task evaluation differs from report: {task.task_id}")
            if target.exists():
                raise FileExistsError(f"canonical task run already exists: {target}")
            planned.append((source, target, task.task_id))

    for source, target, task_id in planned:
        temporary = layout.runs_dir / f".{task_id}.publishing-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, temporary, copy_function=_link_or_copy)
            temporary.replace(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=packaged_manifest_path("open_web_general.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Exact execution directory. By default a unique execution is allocated below "
            "outputs/studies/<manifest-suite>/executions/."
        ),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--shards", type=int, default=3)
    parser.add_argument("--max-steps-per-task", type=int, default=8)
    parser.add_argument("--discovery-max-steps-per-task", type=int, default=12)
    parser.add_argument("--direct-task-timeout-seconds", type=int, default=600)
    parser.add_argument("--discovery-task-timeout-seconds", type=int, default=2400)
    parser.add_argument("--captcha-handling", choices=("report", "fail"), default="fail")
    add_study_run_arguments(parser)
    return parser.parse_args(argv)


def run_parallel(args: argparse.Namespace) -> int:
    context = _prepare_parallel(args)
    with ThreadPoolExecutor(max_workers=args.shards) as pool:
        reports = list(pool.map(lambda index: _run_shard(args, context, index), range(args.shards)))
    merged = merge_shard_reports(
        reports,
        expected_task_ids={task.id for task in context.planned_tasks},
    )
    merged = _persist_parallel_report(args, context, reports, merged)
    print(
        f"{context.suite}: {merged.summary.passed_tasks}/{merged.summary.task_count} passed; "
        f"answer grounding {merged.summary.answer_grounding_rate:.1%}"
    )
    return 0 if merged.summary.passed_tasks == merged.summary.task_count else 1


@dataclass(frozen=True, slots=True)
class _ParallelContext:
    suite: str
    manifest: Path
    manifest_hash: str
    output_dir: Path
    layout: StudyExecutionLayout
    study_context: StudyRunContext | None
    planned_tasks: list[Any]


def _prepare_parallel(args: argparse.Namespace) -> _ParallelContext:
    if args.shards < 1:
        raise ValueError("--shards must be positive")
    if args.direct_task_timeout_seconds < 1:
        raise ValueError("--direct-task-timeout-seconds must be positive")
    if args.discovery_task_timeout_seconds < 1:
        raise ValueError("--discovery-task-timeout-seconds must be positive")
    manifest = args.manifest.resolve()
    suite, manifest_tasks, manifest_hash = load_manifest(manifest)
    output_dir = (
        args.output.resolve()
        if args.output is not None
        else allocate_execution_dir(
            default_study_dir(suite), model=args.model, condition="parallel-open-web"
        )
    )
    layout = StudyExecutionLayout.from_root(output_dir)
    study_context = study_context_from_args(args, model=args.model)
    if study_context is not None and manifest_hash != study_context.task_manifest_sha256:
        raise ValueError("selected manifest bytes differ from the preregistered study manifest")
    layout.prepare(
        study_id=study_context.study_id if study_context else None,
        task_manifest_sha256=study_context.task_manifest_sha256 if study_context else None,
        task_set_sha256=study_context.task_set_sha256 if study_context else None,
    )
    planned_tasks = [
        task.model_copy(
            update={
                "max_steps": (
                    args.discovery_max_steps_per_task
                    if task.discovery_required
                    else args.max_steps_per_task
                )
            }
        )
        for task in manifest_tasks
    ]
    if study_context is not None:
        validate_study_task_set(study_context, execution=layout, suite=suite, tasks=planned_tasks)
    return _ParallelContext(
        suite=suite,
        manifest=manifest,
        manifest_hash=manifest_hash,
        output_dir=output_dir,
        layout=layout,
        study_context=study_context,
        planned_tasks=planned_tasks,
    )


def _run_shard(args: argparse.Namespace, context: _ParallelContext, index: int) -> dict[str, Any]:
    shard_dir = context.layout.shards_dir / f"shard-{index:02d}"
    command = [
        sys.executable,
        "-m",
        "webagent.benchmarks.suites.open_web.runner",
        "--manifest",
        str(context.manifest),
        "--output",
        str(shard_dir),
        "--model",
        args.model,
        "--max-steps-per-task",
        str(args.max_steps_per_task),
        "--discovery-max-steps-per-task",
        str(args.discovery_max_steps_per_task),
        "--direct-task-timeout-seconds",
        str(args.direct_task_timeout_seconds),
        "--discovery-task-timeout-seconds",
        str(args.discovery_task_timeout_seconds),
        "--captcha-handling",
        args.captcha_handling,
        "--shard-count",
        str(args.shards),
        "--shard-index",
        str(index),
    ]
    if context.study_context is not None:
        command.extend(["--report-provider", context.study_context.provider])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    (context.layout.logs_dir / f"shard-{index:02d}-process.log").write_text(
        completed.stdout + completed.stderr, encoding="utf-8"
    )
    result_path = shard_dir / "results.json"
    if not result_path.is_file():
        raise RuntimeError(f"shard {index} exited {completed.returncode} without {result_path}")
    return cast(dict[str, Any], json.loads(result_path.read_text(encoding="utf-8")))


def _persist_parallel_report(
    args: argparse.Namespace,
    context: _ParallelContext,
    reports: list[dict[str, Any]],
    merged: BenchmarkReport,
) -> BenchmarkReport:
    retained_manifest = retain_manifest_snapshot(
        context.manifest,
        output_dir=context.output_dir,
        manifest_sha256=context.manifest_hash,
    )
    metadata = dict(merged.metadata)
    metadata["manifest"] = retained_manifest.relative_to(context.output_dir).as_posix()
    merged = merged.model_copy(update={"metadata": metadata})
    if context.study_context is not None:
        merged = bind_study_identity(
            merged, context=context.study_context, output_dir=context.output_dir
        )
    publish_shard_task_runs(context.layout, reports)
    target = context.layout.report_path
    target.write_text(
        json.dumps(merged.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert merged.research is not None
    for name, analysis_payload in (
        ("failures", merged.research.failures),
        ("calibration", merged.research.calibration),
        ("transfer", merged.research.transfer),
    ):
        (context.layout.analysis_dir / f"{name}.json").write_text(
            json.dumps(analysis_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    payload = merged.model_dump(mode="json")
    evidence_report_path = retain_report_snapshot(
        payload, source_path=target, output_dir=context.output_dir
    )
    append_time_slice(
        context.layout.ledger_path,
        payload,
        context.manifest_hash,
        report_path=evidence_report_path,
    )
    if context.study_context is not None:
        publish_study_run_records(
            context.study_context,
            execution=context.layout,
            suite=merged.suite,
            created_at=merged.created_at,
            evaluations=merged.tasks,
        )
    return merged


def main() -> None:
    raise SystemExit(run_parallel(parse_args()))


if __name__ == "__main__":
    main()


__all__ = [
    "bind_study_identity",
    "merge_shard_reports",
    "publish_shard_task_runs",
    "run_parallel",
]

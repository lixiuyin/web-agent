"""Repeat the real-agent interaction benchmark and aggregate results by model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from benchmarks.core import allocate_execution_dir, default_study_dir, initialize_matrix_study
from benchmarks.suites.controlled_web.general_tasks import build_tasks
from webagent.core.config import AgentConfig
from webagent.evaluation import (
    StudyBudgets,
    StudyCondition,
    StudyLayout,
    StudyManifest,
    StudyModel,
)
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint


def _stage_text(target: Path, content: str) -> Path:
    """Write and flush a complete sibling file for atomic publication."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with staged.open("x", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _publish_matrix(output_dir: Path, batch_id: str, encoded: str) -> Path:
    """Publish one immutable batch, then atomically refresh legacy ``matrix.json``."""
    layout = StudyLayout.from_root(output_dir)
    layout.prepare()
    snapshot = layout.matrix_snapshots_dir / f"{batch_id}.json"
    staged_snapshot = _stage_text(snapshot, encoded)
    try:
        try:
            # A hard link makes the fully written snapshot visible in one operation
            # and refuses to replace an existing batch identifier.
            os.link(staged_snapshot, snapshot)
        except FileExistsError:
            raise FileExistsError(f"matrix batch snapshot already exists: {snapshot}") from None
    finally:
        staged_snapshot.unlink(missing_ok=True)

    latest = layout.matrix_latest_path
    staged_latest = _stage_text(latest, encoded)
    try:
        staged_latest.replace(latest)
    finally:
        staged_latest.unlink(missing_ok=True)
    return snapshot


def _consistent_report_metadata(reports: list[dict[str, Any]], key: str) -> str:
    """Return one provenance value and reject mixed-source repetitions."""
    values = {
        str(cast(dict[str, Any], report.get("metadata", {})).get(key, "unknown"))
        for report in reports
    }
    if not values:
        return "unknown"
    if len(values) != 1:
        raise ValueError(f"repetition reports disagree on metadata.{key}: {sorted(values)}")
    value = values.pop()
    if value != "unknown" and re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"metadata.{key} is not a lowercase SHA-256 digest")
    return value


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate task-level records without averaging already-rounded rates."""
    tasks = [task for report in reports for task in report.get("tasks", [])]
    task_count = len(tasks)
    action_count = sum(int(task.get("action_count", 0)) for task in tasks)
    failed_actions = sum(int(task.get("failed_action_count", 0)) for task in tasks)
    completions = sum(bool(task.get("agent_reported_success")) for task in tasks)
    false_completions = sum(
        bool(task.get("agent_reported_success")) and not bool(task.get("passed")) for task in tasks
    )
    answer_count = sum(int(task.get("answer_assertion_count", 0)) for task in tasks)
    answer_passed = sum(int(task.get("answer_assertion_passed", 0)) for task in tasks)
    return {
        "run_count": len(reports),
        "agent_source_sha256": _consistent_report_metadata(reports, "agent_source_sha256"),
        "benchmark_source_sha256": _consistent_report_metadata(reports, "benchmark_source_sha256"),
        "task_run_count": task_count,
        "passed_task_runs": sum(bool(task.get("passed")) for task in tasks),
        "success_rate": (
            sum(bool(task.get("passed")) for task in tasks) / task_count if task_count else 0.0
        ),
        "agent_completion_rate": completions / task_count if task_count else 0.0,
        "false_completion_rate": false_completions / completions if completions else 0.0,
        "action_validity_rate": (
            (action_count - failed_actions) / action_count if action_count else 1.0
        ),
        "answer_grounding_rate": answer_passed / answer_count if answer_count else 0.0,
        "total_planner_tokens": sum(int(task.get("planner_tokens", 0)) for task in tasks),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_study_dir("web-interaction-model-matrix"),
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--max-steps-per-task", type=int, default=12)
    parser.add_argument(
        "--parallel-repetitions",
        type=int,
        default=1,
        help="Run isolated repetitions concurrently (subject to provider rate limits)",
    )
    parser.add_argument("--minimum-success-rate", type=float, default=0.0)
    parser.add_argument("--maximum-false-completion-rate", type=float, default=1.0)
    return parser.parse_args(argv)


def run_matrix(args: argparse.Namespace) -> int:
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive")
    if args.parallel_repetitions < 1:
        raise ValueError("--parallel-repetitions must be positive")
    output_dir = args.output.resolve()
    collection_started_at = datetime.now(UTC)
    study_id = output_dir.name
    defaults = AgentConfig()
    task_contract = [
        task.model_dump(mode="json") for task in build_tasks("http://controlled.invalid")
    ]
    task_manifest_bytes = json.dumps(
        task_contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    task_manifest_sha256 = hashlib.sha256(task_manifest_bytes).hexdigest()
    source_sha256 = hashlib.sha256(
        (agent_source_fingerprint() + benchmark_source_fingerprint()).encode("ascii")
    ).hexdigest()
    study_manifest = StudyManifest(
        study_id=study_id,
        title="Controlled web interaction model comparison",
        research_questions=(
            "How do model-level interaction failures recur across controlled, verifiable workflows?",
        ),
        suite="web-interaction-v1",
        task_manifest_sha256=task_manifest_sha256,
        task_split_counts=dict(Counter(task["split"] for task in task_contract)),
        models=tuple(StudyModel(provider=args.provider, model=model) for model in args.models),
        conditions=(
            StudyCondition(
                id="agent",
                kind="baseline",
                description="Browser-only agent in the controlled web environment",
            ),
        ),
        repetitions=args.repetitions,
        budgets=StudyBudgets(
            max_steps=args.max_steps_per_task,
            task_timeout_seconds=180,
            tool_timeout_seconds=defaults.tool_timeout,
            planner_max_tokens=defaults.planner_max_tokens,
        ),
        primary_metrics=("success_rate", "failure_modes"),
        secondary_metrics=("false_completion_rate", "action_validity_rate", "calibration"),
        confidence_target="task_success",
        source_sha256=source_sha256,
        created_at=collection_started_at,
    )
    initialize_matrix_study(
        output_dir,
        study_manifest,
        task_manifest_bytes=task_manifest_bytes,
    )
    layout = StudyLayout.from_root(output_dir)
    batch_id = collection_started_at.strftime("%Y%m%dT%H%M%S%fZ")
    matrix: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "benchmark": "web-interaction-v1",
        "authoritative_snapshot": f"analysis/matrices/{batch_id}.json",
        "repetitions": args.repetitions,
        "models": {},
    }
    all_acceptable = True
    for model in args.models:

        def run_repetition(
            repetition: int,
            *,
            selected_model: str = model,
        ) -> dict[str, Any]:
            run_dir = allocate_execution_dir(
                output_dir,
                model=selected_model,
                condition="agent",
                now=collection_started_at,
                execution_id=f"{batch_id}-r{repetition:02d}",
            )
            command = [
                sys.executable,
                "-m",
                "benchmarks.suites.controlled_web.general",
                "--mode",
                "agent",
                "--tool-set",
                "browser-only",
                "--model",
                selected_model,
                "--output",
                str(run_dir),
                "--max-steps-per-task",
                str(args.max_steps_per_task),
                "--study-root",
                str(output_dir),
                "--study-id",
                study_id,
                "--provider",
                args.provider,
                "--condition-id",
                "agent",
                "--repetition",
                str(repetition),
            ]
            if args.headed:
                command.append("--headed")
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            process_log = (
                layout.logs_dir
                / f"{batch_id}-{selected_model.replace('/', '-')}-r{repetition:02d}.log"
            )
            process_log.write_text(
                completed.stdout + completed.stderr,
                encoding="utf-8",
            )
            result_path = run_dir / "results.json"
            if not result_path.is_file():
                raise RuntimeError(
                    f"benchmark process exited {completed.returncode} without {result_path}"
                )
            report = cast(dict[str, Any], json.loads(result_path.read_text(encoding="utf-8")))
            report["process_exit_code"] = completed.returncode
            return report

        workers = min(args.parallel_repetitions, args.repetitions)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            reports = list(pool.map(run_repetition, range(1, args.repetitions + 1)))
        aggregate = aggregate_reports(reports)
        acceptable = bool(
            aggregate["success_rate"] >= args.minimum_success_rate
            and aggregate["false_completion_rate"] <= args.maximum_false_completion_rate
        )
        matrix["models"][model] = {"acceptable": acceptable, **aggregate}
        all_acceptable &= acceptable
    encoded = json.dumps(matrix, ensure_ascii=False, indent=2)
    _publish_matrix(output_dir, batch_id, encoded)
    print(json.dumps(matrix["models"], ensure_ascii=False, indent=2))
    return 0 if all_acceptable else 1


def main() -> None:
    raise SystemExit(run_matrix(parse_args()))


if __name__ == "__main__":
    main()


__all__ = ["aggregate_reports", "run_matrix"]

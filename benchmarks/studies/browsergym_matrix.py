"""Run matched models on WebArena-Verified and VisualWebArena via an isolated BrowserGym."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.core import default_study_dir
from benchmarks.studies.open_web_matrix import _ordered_models
from webagent.evaluation import ExternalBenchmarkReport, StudyLayout, safe_slug
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument(
        "--browsergym-python",
        type=Path,
        default=Path(".venv-browsergym/bin/python"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_study_dir("browsergym-external-model-matrix"),
    )
    parser.add_argument("--webarena-task-ids", nargs="+", type=int, default=None)
    parser.add_argument("--visual-task-ids", nargs="+", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--record-video", action="store_true")
    parser.add_argument(
        "--visual-evaluator-device",
        choices=("cpu", "cuda"),
        default="cpu",
    )
    parser.add_argument(
        "--prepare-backend",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-errors", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    models = list(dict.fromkeys(str(model) for model in args.models))
    if not 2 <= len(models) <= 3:
        raise ValueError("BrowserGym matrix requires two or three distinct models")
    python = args.browsergym_python.expanduser().resolve()
    if not python.is_file():
        raise FileNotFoundError(
            f"isolated BrowserGym Python is missing: {python}; run scripts/setup_browsergym_env.sh"
        )
    output = args.output.expanduser().resolve()
    layout = StudyLayout.from_root(output)
    layout.prepare()
    state_path = output / "browsergym-matrix-state.json"
    started = datetime.now(UTC)
    contract = {
        "schema_version": 1,
        "provider": args.provider,
        "models": sorted(models),
        "benchmarks": ["webarena_verified", "visualwebarena"],
        "webarena_task_ids": args.webarena_task_ids,
        "visual_task_ids": args.visual_task_ids,
        "max_steps": args.max_steps,
        "headless": not args.headed,
        "record_video": args.record_video,
        "visual_evaluator_device": args.visual_evaluator_device,
        "prepare_backend": args.prepare_backend,
        "browsergym_python": str(python),
        "agent_source_sha256": agent_source_fingerprint(),
        "benchmark_source_sha256": benchmark_source_fingerprint(),
    }
    if args.resume:
        state = _read_json(state_path)
        if state.get("contract") != contract:
            raise ValueError("resume arguments or source differ from BrowserGym matrix contract")
        executions = dict(state["executions"])
        batch_id = str(state["batch_id"])
    else:
        if state_path.exists():
            raise FileExistsError(
                f"BrowserGym matrix already exists; use --resume or a new --output: {output}"
            )
        batch_id = started.strftime("%Y%m%dT%H%M%S%fZ")
        executions = _execution_paths(output, models, batch_id, started)
        state = {
            "schema_version": 1,
            "status": "running",
            "batch_id": batch_id,
            "contract": contract,
            "executions": executions,
        }
        _write_json_atomic(state_path, state)

    ordered_models = _ordered_models(models, started.date(), "rotate-by-date")
    try:
        for model in ordered_models:
            for benchmark in ("webarena_verified", "visualwebarena"):
                key = f"{model}::{benchmark}"
                execution = output / executions[key]
                report = execution / "browsergym-results.json"
                if report.is_file():
                    print(f"BrowserGym matrix retained: {key}")
                    continue
                command = [
                    str(python),
                    "-m",
                    "benchmarks.suites.browsergym.runner",
                    "--benchmark",
                    benchmark,
                    "--provider",
                    args.provider,
                    "--model",
                    model,
                    "--output",
                    str(execution),
                    "--max-steps",
                    str(args.max_steps),
                    "--visual-evaluator-device",
                    args.visual_evaluator_device,
                ]
                task_ids = (
                    args.webarena_task_ids
                    if benchmark == "webarena_verified"
                    else args.visual_task_ids
                )
                if task_ids:
                    command.extend(["--task-ids", *(str(value) for value in task_ids)])
                if args.headed:
                    command.append("--headed")
                if args.record_video:
                    command.append("--record-video")
                if not args.prepare_backend:
                    command.append("--no-prepare-backend")
                if execution.exists():
                    command.append("--resume")
                    if args.retry_errors:
                        command.append("--retry-errors")
                log = (
                    layout.logs_dir
                    / f"{batch_id}-{safe_slug(model, fallback='model')}-{benchmark}.log"
                )
                _run_command(command, log)
        reports = [
            ExternalBenchmarkReport.model_validate_json(
                (output / relative / "browsergym-results.json").read_bytes()
            )
            for relative in executions.values()
        ]
        matrix = _aggregate_matrix(args.provider, models, reports)
        matrix.update(
            {
                "schema_version": 1,
                "batch_id": batch_id,
                "interface": "browsergym",
                "notice": "Benchmark scores remain separate; no cross-benchmark pooled score.",
            }
        )
        snapshot = layout.matrix_snapshots_dir / f"{batch_id}.json"
        _write_json_atomic(snapshot, matrix)
        _write_json_atomic(layout.matrix_latest_path, matrix)
        state.update({"status": "completed", "matrix": snapshot.relative_to(output).as_posix()})
        _write_json_atomic(state_path, state)
    except Exception as exc:
        state.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        _write_json_atomic(state_path, state)
        raise
    print(json.dumps(matrix, ensure_ascii=False, indent=2))
    return 0


def _execution_paths(
    root: Path,
    models: Sequence[str],
    batch_id: str,
    started: datetime,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for model in models:
        for benchmark in ("webarena_verified", "visualwebarena"):
            execution = (
                root
                / "executions"
                / started.date().isoformat()
                / safe_slug(model, fallback="model")
                / benchmark
                / batch_id
            )
            paths[f"{model}::{benchmark}"] = execution.relative_to(root).as_posix()
    return paths


def _aggregate_matrix(
    provider: str,
    models: Sequence[str],
    reports: Sequence[ExternalBenchmarkReport],
) -> dict[str, Any]:
    if any(report.provider != provider for report in reports):
        raise ValueError("BrowserGym reports use a provider different from the matrix contract")
    observed_keys = [(report.model, report.benchmark) for report in reports]
    if len(observed_keys) != len(set(observed_keys)):
        raise ValueError("BrowserGym matrix contains duplicate model/benchmark reports")
    expected_keys = {
        (model, benchmark)
        for model in models
        for benchmark in ("webarena_verified", "visualwebarena")
    }
    if set(observed_keys) != expected_keys:
        raise ValueError("BrowserGym matrix reports do not match the declared model/suite grid")
    by_key = {(report.model, report.benchmark): report for report in reports}
    benchmarks: dict[str, Any] = {}
    for benchmark in ("webarena_verified", "visualwebarena"):
        values = [by_key[(model, benchmark)] for model in models]
        digests = {report.task_set_sha256 for report in values}
        if len(digests) != 1:
            raise ValueError(f"{benchmark} reports use different task sets")
        protocols = {
            (report.profile, report.max_steps, report.headless, report.evaluator_device)
            for report in values
        }
        if len(protocols) != 1:
            raise ValueError(f"{benchmark} reports use different execution protocols")
        backend_digests = {report.backend_configuration_sha256 for report in values}
        if len(backend_digests) != 1:
            raise ValueError(f"{benchmark} reports use different backend configurations")
        agent_digests = {report.agent_source_sha256 for report in values}
        if len(agent_digests) != 1:
            raise ValueError(f"{benchmark} reports use different agent source")
        adapter_digests = {report.adapter_source_sha256 for report in values}
        if len(adapter_digests) != 1:
            raise ValueError(f"{benchmark} reports use different adapter source")
        package_sets = {json.dumps(report.package_versions, sort_keys=True) for report in values}
        if len(package_sets) != 1:
            raise ValueError(f"{benchmark} reports use different BrowserGym package versions")
        incomplete = [report.model for report in values if report.protocol_status == "incomplete"]
        if incomplete:
            raise ValueError(
                f"{benchmark} has incomplete model reports: {', '.join(sorted(incomplete))}"
            )
        model_values = {
            report.model: {
                "profile": report.profile,
                "protocol_status": report.protocol_status,
                "success_rate": report.summary.success_rate,
                "success_rate_ci95": report.summary.success_rate_ci95,
                "mean_reward": report.summary.mean_reward,
                "system_error_tasks": report.summary.system_error_tasks,
            }
            for report in values
        }
        comparisons = [
            _paired_comparison(by_key[(left, benchmark)], by_key[(right, benchmark)])
            for index, left in enumerate(models)
            for right in models[index + 1 :]
        ]
        benchmarks[benchmark] = {
            "task_set_sha256": next(iter(digests)),
            "backend_configuration_sha256": next(iter(backend_digests)),
            "agent_source_sha256": next(iter(agent_digests)),
            "adapter_source_sha256": next(iter(adapter_digests)),
            "models": model_values,
            "paired_comparisons": comparisons,
        }
    return {"provider": provider, "models": list(models), "benchmarks": benchmarks}


def _paired_comparison(
    left: ExternalBenchmarkReport,
    right: ExternalBenchmarkReport,
) -> dict[str, Any]:
    left_tasks = {task.task_name: task for task in left.tasks if task.error is None}
    right_tasks = {task.task_name: task for task in right.tasks if task.error is None}
    paired = sorted(left_tasks.keys() & right_tasks.keys())
    left_only = sum(left_tasks[name].success and not right_tasks[name].success for name in paired)
    right_only = sum(right_tasks[name].success and not left_tasks[name].success for name in paired)
    left_rate = sum(left_tasks[name].success for name in paired) / len(paired) if paired else None
    right_rate = sum(right_tasks[name].success for name in paired) / len(paired) if paired else None
    return {
        "left_model": left.model,
        "right_model": right.model,
        "paired_tasks": len(paired),
        "left_success_rate": left_rate,
        "right_success_rate": right_rate,
        "success_rate_delta": (
            left_rate - right_rate if left_rate is not None and right_rate is not None else None
        ),
        "left_only_successes": left_only,
        "right_only_successes": right_only,
        "mcnemar_exact_p_value": _mcnemar_exact(left_only, right_only),
    }


def _mcnemar_exact(left_only: int, right_only: int) -> float | None:
    discordant = left_only + right_only
    if discordant == 0:
        return None
    tail = sum(math.comb(discordant, value) for value in range(min(left_only, right_only) + 1))
    return float(min(1.0, 2.0 * tail / (2**discordant)))


def _run_command(command: list[str], log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    paths = [str(repo / "src"), str(repo)]
    if existing:
        paths.append(existing)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        env=environment,
    )
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"BrowserGym command failed ({completed.returncode}); see {log}")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid BrowserGym matrix state: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid BrowserGym matrix state: {path}")
    return value


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()

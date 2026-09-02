"""Run WebArena-Verified or VisualWebArena through BrowserGym's standard API."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.resources
import json
import math
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import load_dotenv

from benchmarks.core import allocate_execution_dir, default_study_dir
from benchmarks.suites.browsergym.adapter import (
    backend_configuration_sha256,
    task_id_from_name,
    task_set_sha256,
)
from benchmarks.suites.browsergym.policy import WebAgentBrowserGymArgs
from webagent.evaluation import (
    ExternalBenchmarkReport,
    ExternalTaskResult,
    StudyExecutionLayout,
    new_external_report,
)
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint

BenchmarkName = Literal["webarena_verified", "visualwebarena"]
EvaluatorDevice = Literal["not_applicable", "cpu", "cuda"]
_DEFAULT_STEPS = 30


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark",
        choices=("webarena_verified", "visualwebarena"),
        required=True,
    )
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--task-ids", nargs="+", type=int, default=None)
    parser.add_argument("--max-steps", type=int, default=_DEFAULT_STEPS)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--record-video", action="store_true")
    parser.add_argument(
        "--visual-evaluator-device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="Device for VisualWebArena's native BLIP-2 evaluator",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="With --resume, rerun episodes whose prior summary recorded a system error",
    )
    parser.add_argument(
        "--prepare-backend",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reset and warm the official backend before collecting tasks",
    )
    parser.add_argument("--planner-max-tokens", type=int, default=None)
    parser.add_argument("--planner-reasoning-effort", default=None)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env")
    if args.max_steps < 1:
        raise ValueError("--max-steps must be positive")
    benchmark = cast(BenchmarkName, args.benchmark)
    backend_digest = backend_configuration_sha256(benchmark)
    all_tasks, default_tasks, default_profile = _task_catalog(benchmark)
    selected = _select_tasks(all_tasks, default_tasks, args.task_ids)
    seed_by_task = _canonical_task_seeds(benchmark)
    selected_seeds = [seed_by_task[name] for name in selected]
    custom = args.task_ids is not None
    profile = "custom" if custom else default_profile
    evaluator_device = cast(
        EvaluatorDevice,
        args.visual_evaluator_device if benchmark == "visualwebarena" else "not_applicable",
    )
    _require_evaluator_device(benchmark, evaluator_device)
    output = (
        args.output.expanduser().resolve()
        if args.output is not None
        else allocate_execution_dir(
            default_study_dir(f"browsergym-{benchmark.replace('_', '-')}-{profile}"),
            model=args.model,
            condition="agent",
        ).resolve()
    )
    layout = StudyExecutionLayout.from_root(output)
    task_digest = task_set_sha256(selected, selected_seeds)
    task_contract = {
        "schema_version": 1,
        "benchmark": benchmark,
        "profile": profile,
        "task_count": len(selected),
        "task_set_sha256": task_digest,
        "backend_configuration_sha256": backend_digest,
        "tasks": [
            {"task_name": name, "task_seed": seed}
            for name, seed in zip(selected, selected_seeds, strict=True)
        ],
    }
    execution_contract = {
        "schema_version": 1,
        "interface": "browsergym",
        "benchmark": benchmark,
        "profile": profile,
        "provider": args.provider,
        "model": args.model,
        "max_steps": args.max_steps,
        "headless": not args.headed,
        "evaluator_device": evaluator_device,
        "prepare_backend": args.prepare_backend,
        "task_set_sha256": task_digest,
        "backend_configuration_sha256": backend_digest,
        "agent_source_sha256": agent_source_fingerprint(),
        "benchmark_source_sha256": benchmark_source_fingerprint(),
    }
    if args.resume:
        layout.require_prepared()
        _require_json(layout.inputs_dir / "browsergym-task-set.json", task_contract)
        _require_json(layout.root / "browsergym-execution.json", execution_contract)
        if (layout.root / "browsergym-results.json").exists():
            raise FileExistsError("BrowserGym execution is already complete")
    else:
        layout.prepare()
        _write_json_atomic(layout.inputs_dir / "browsergym-task-set.json", task_contract)
        _write_json_atomic(layout.root / "browsergym-execution.json", execution_contract)
    if args.prepare_backend:
        _prepare_backend(benchmark)

    agent_args = WebAgentBrowserGymArgs(
        model=args.model,
        benchmark=benchmark,
        planner_max_tokens=args.planner_max_tokens,
        planner_reasoning_effort=args.planner_reasoning_effort,
    )
    results = _resume_results(layout) if args.resume else []
    completed = {
        result.task_name
        for result in results
        if not (args.retry_errors and result.error is not None)
    }
    if args.retry_errors:
        results = [result for result in results if result.error is None]
    agent_args.prepare()
    try:
        for index, task_name in enumerate(selected, start=1):
            if task_name in completed:
                print(
                    f"BrowserGym {benchmark}: task {index}/{len(selected)} {task_name} (retained)"
                )
                continue
            print(f"BrowserGym {benchmark}: task {index}/{len(selected)} {task_name}")
            results.append(
                _run_task(
                    agent_args,
                    task_name=task_name,
                    task_seed=seed_by_task[task_name],
                    output=layout.runs_dir,
                    max_steps=args.max_steps,
                    headless=not args.headed,
                    record_video=args.record_video,
                    evaluator_device=evaluator_device,
                    root=layout.root,
                )
            )
            _write_partial_report(
                layout=layout,
                benchmark=benchmark,
                profile=profile,
                official_protocol=False,
                provider=args.provider,
                model=args.model,
                max_steps=args.max_steps,
                headless=not args.headed,
                evaluator_device=evaluator_device,
                task_digest=task_digest,
                backend_digest=backend_digest,
                expected_tasks=len(selected),
                results=results,
            )
    finally:
        agent_args.close()

    official_protocol = (
        not custom and args.max_steps == _DEFAULT_STEPS and bool(args.prepare_backend)
    )
    by_task = {result.task_name: result for result in results}
    ordered_results = [by_task[name] for name in selected if name in by_task]
    report = new_external_report(
        benchmark=benchmark,
        profile=profile,
        official_protocol=official_protocol,
        provider=args.provider,
        model=args.model,
        max_steps=args.max_steps,
        headless=not args.headed,
        evaluator_device=evaluator_device,
        task_set_sha256=task_digest,
        backend_configuration_sha256=backend_digest,
        agent_source_sha256=agent_source_fingerprint(),
        adapter_source_sha256=benchmark_source_fingerprint(),
        package_versions=_package_versions(),
        expected_tasks=len(selected),
        tasks=ordered_results,
    )
    _write_json_atomic(layout.root / "browsergym-results.json", report.model_dump(mode="json"))
    print(report.model_dump_json(indent=2, include={"protocol_status", "summary"}))
    return 0 if report.protocol_status != "incomplete" else 1


def _task_catalog(benchmark: BenchmarkName) -> tuple[list[str], list[str], str]:
    seed_by_task = _canonical_task_seeds(benchmark)
    if benchmark == "webarena_verified":
        import browsergym.webarena_verified as suite  # type: ignore[import-not-found]

        all_tasks = list(seed_by_task)
        if set(all_tasks) != set(suite.ALL_WEBARENA_TASK_IDS):
            raise RuntimeError("BrowserGym WebArena-Verified registry and schedule differ")
        resource = importlib.resources.files("webarena_verified").joinpath(
            "assets/dataset/subsets/webarena-verified-hard.json"
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
        hard_ids = {int(value) for value in payload["task_ids"]}
        hard = [name for name in all_tasks if task_id_from_name(name) in hard_ids]
        if len(hard) != 258:
            raise RuntimeError(f"expected 258 WebArena-Verified Hard tasks, found {len(hard)}")
        return all_tasks, hard, "hard"

    import browsergym.visualwebarena as suite  # type: ignore[import-not-found]

    tasks = list(seed_by_task)
    if set(tasks) != set(suite.ALL_VISUALWEBARENA_TASK_IDS):
        raise RuntimeError("BrowserGym VisualWebArena registry and schedule differ")
    if len(tasks) != 910:
        raise RuntimeError(f"expected 910 VisualWebArena tasks, found {len(tasks)}")
    return tasks, tasks, "full"


@lru_cache(maxsize=2)
def _canonical_task_seeds(benchmark: BenchmarkName) -> dict[str, int]:
    """Read BrowserGym's deterministic one-repeat seed schedule in upstream order."""
    from browsergym.experiments.benchmark.configs import (  # type: ignore[import-not-found]
        DEFAULT_BENCHMARKS,
    )

    configured = DEFAULT_BENCHMARKS[benchmark](n_repeats=1)
    seeds: dict[str, int] = {}
    for env_args in configured.env_args_list:
        task_name = str(env_args.task_name)
        if task_name in seeds:
            raise RuntimeError(f"duplicate BrowserGym task in canonical schedule: {task_name}")
        seeds[task_name] = int(env_args.task_seed)
    return seeds


def _select_tasks(
    all_tasks: Sequence[str],
    default_tasks: Sequence[str],
    task_ids: Sequence[int] | None,
) -> list[str]:
    if task_ids is None:
        return list(default_tasks)
    if not task_ids:
        raise ValueError("--task-ids cannot be empty")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("--task-ids cannot contain duplicates")
    by_id = {task_id_from_name(name): name for name in all_tasks}
    missing = sorted(set(task_ids) - set(by_id))
    if missing:
        raise ValueError(f"unknown task IDs: {missing}")
    return [by_id[task_id] for task_id in task_ids]


def _prepare_backend(benchmark: BenchmarkName) -> None:
    from browsergym.experiments.benchmark.configs import (
        DEFAULT_BENCHMARKS,
    )

    DEFAULT_BENCHMARKS[benchmark]().prepare_backends()


def _run_task(
    agent_args: WebAgentBrowserGymArgs,
    *,
    task_name: str,
    task_seed: int,
    output: Path,
    max_steps: int,
    headless: bool,
    record_video: bool,
    evaluator_device: EvaluatorDevice,
    root: Path,
) -> ExternalTaskResult:
    from browsergym.experiments import EnvArgs, ExpArgs  # type: ignore[import-not-found]

    experiment = ExpArgs(
        agent_args=agent_args,
        env_args=EnvArgs(
            task_name=task_name,
            task_seed=task_seed,
            max_steps=max_steps,
            headless=headless,
            record_video=record_video,
            wait_for_user_message=False,
            task_kwargs=(
                {"eval_captioning_model_device": evaluator_device}
                if evaluator_device != "not_applicable"
                else None
            ),
        ),
        save_screenshot=True,
        save_som=False,
        enable_debug=False,
        logging_level_stdout=30,
    )
    experiment.prepare(output)
    experiment.run()
    summary_path = Path(experiment.exp_dir) / "summary_info.json"
    summary = cast(dict[str, Any], json.loads(summary_path.read_text(encoding="utf-8")))
    reward = float(summary.get("cum_reward") or 0.0)
    error = summary.get("err_msg")
    return ExternalTaskResult(
        task_name=task_name,
        task_id=task_id_from_name(task_name),
        task_seed=task_seed,
        reward=reward,
        success=math.isclose(reward, 1.0, rel_tol=0.0, abs_tol=1e-9),
        steps=int(summary.get("n_steps") or 0),
        terminated=bool(summary.get("terminated")),
        truncated=bool(summary.get("truncated")),
        error=str(error) if error else None,
        evidence_path=Path(experiment.exp_dir).resolve().relative_to(root).as_posix(),
    )


def _write_partial_report(
    *,
    layout: StudyExecutionLayout,
    benchmark: BenchmarkName,
    profile: str,
    official_protocol: bool,
    provider: str,
    model: str,
    max_steps: int,
    headless: bool,
    evaluator_device: EvaluatorDevice,
    task_digest: str,
    backend_digest: str,
    expected_tasks: int,
    results: Sequence[ExternalTaskResult],
) -> None:
    report = new_external_report(
        benchmark=benchmark,
        profile=profile,
        official_protocol=official_protocol,
        provider=provider,
        model=model,
        max_steps=max_steps,
        headless=headless,
        evaluator_device=evaluator_device,
        task_set_sha256=task_digest,
        backend_configuration_sha256=backend_digest,
        agent_source_sha256=agent_source_fingerprint(),
        adapter_source_sha256=benchmark_source_fingerprint(),
        package_versions=_package_versions(),
        expected_tasks=expected_tasks,
        tasks=results,
    )
    _write_json_atomic(
        layout.root / "browsergym-results.partial.json", report.model_dump(mode="json")
    )


def _require_evaluator_device(benchmark: BenchmarkName, evaluator_device: EvaluatorDevice) -> None:
    """Fail before an episode when the requested native evaluator device is unavailable."""
    if benchmark != "visualwebarena" or evaluator_device != "cuda":
        return
    import torch  # type: ignore[import-untyped]

    if not torch.cuda.is_available():
        raise RuntimeError(
            "VisualWebArena CUDA evaluation was requested, but torch.cuda.is_available() is false"
        )


def _package_versions() -> dict[str, str]:
    packages = (
        "browsergym-core",
        "browsergym-experiments",
        "browsergym-webarena-verified",
        "browsergym-visualwebarena",
        "webarena-verified",
        "libvisualwebarena",
        "playwright",
    )
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not-installed"
    return versions


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _require_json(path: Path, expected: object) -> None:
    try:
        observed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"resume evidence is missing or invalid: {path}") from exc
    if observed != expected:
        raise ValueError(f"resume configuration differs from immutable evidence: {path}")


def _resume_results(layout: StudyExecutionLayout) -> list[ExternalTaskResult]:
    path = layout.root / "browsergym-results.partial.json"
    if not path.is_file():
        return []
    report = ExternalBenchmarkReport.model_validate_json(path.read_bytes())
    return report.tasks


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()

"""Run dated, evidence-grounded tasks against real public websites."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # Support direct execution from the repository checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from webagent.agent.loop import WebAgent
from webagent.benchmarks.core import (
    BROWSER_ONLY_TOOLS,
    add_study_run_arguments,
    allocate_execution_dir,
    default_study_dir,
    packaged_manifest_path,
    study_context_from_args,
    task_run_dir,
)
from webagent.benchmarks.studies.open_web_longitudinal import evidence_record_from_report
from webagent.browser.controller import BrowserController
from webagent.cli import _build_planner, _build_tool_registry
from webagent.core.config import AgentConfig
from webagent.core.protocols import Planner
from webagent.evaluation import (
    BenchmarkRunner,
    BenchmarkTask,
    StudyExecutionLayout,
    StudyLayout,
    StudyRunContext,
    TerminalStateEvaluator,
)
from webagent.planner.stub import StubPlanner
from webagent.tools.executor import ToolExecutor
from webagent.tools.exposure import allowed_tools_for_discovery_mode
from webagent.tools.policy import BrowserGroundedPolicy, SearchEngineOnlyPolicy
from webagent.tools.risk import ActionRiskPolicy, BrowserRiskContext
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint

_MINIMUM_FREE_BYTES = 512 * 1024 * 1024


def _known_provider(value: object) -> str:
    provider = str(value or "").strip()
    return provider if provider and provider != "unknown" else "unknown"


def canonical_sha256(payload: Any) -> str:
    """Hash one JSON-compatible value with stable key ordering."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_free_space(path: Path, *, minimum_bytes: int = _MINIMUM_FREE_BYTES) -> None:
    """Fail before browser startup when the artifact volume cannot hold a trace."""
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < minimum_bytes:
        raise RuntimeError(
            "benchmark output volume has insufficient free space: "
            f"{free_bytes // (1024 * 1024)} MiB available, "
            f"{minimum_bytes // (1024 * 1024)} MiB required"
        )


def retain_manifest_snapshot(
    manifest_path: Path, *, output_dir: Path, manifest_sha256: str
) -> Path:
    """Retain the exact manifest bytes below the run output for later evidence checks."""
    target = (
        StudyExecutionLayout.from_root(output_dir).retained_manifests_dir
        / f"{manifest_sha256}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(manifest_path, target)
    if hashlib.sha256(target.read_bytes()).hexdigest() != manifest_sha256:
        raise ValueError("retained manifest snapshot does not match its content hash")
    return target.resolve()


def retain_report_snapshot(report: dict[str, Any], *, source_path: Path, output_dir: Path) -> Path:
    """Retain one content-addressed report so later runs cannot overwrite its evidence."""
    report_sha256 = canonical_sha256(report)
    target = (
        StudyExecutionLayout.from_root(output_dir).retained_reports_dir
        / report_sha256
        / "results.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source_path, target)
    persisted = json.loads(target.read_text(encoding="utf-8"))
    if persisted != report:
        raise ValueError("retained report snapshot does not match its content hash")
    return target.resolve()


def retain_study_manifest_snapshot(
    context: StudyRunContext,
    *,
    output_dir: Path,
) -> tuple[Path, str]:
    """Retain the immutable study contract inside this execution's evidence boundary."""
    source = StudyLayout.from_root(context.study_root).manifest_path
    if not source.is_file():
        raise ValueError(f"study manifest evidence is missing: {source}")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    target = (
        StudyExecutionLayout.from_root(output_dir).inputs_dir / "study-manifests" / f"{digest}.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        if target.read_bytes() != raw:
            raise ValueError("retained study manifest hash path contains different bytes")
    else:
        temporary = target.with_suffix(".json.tmp")
        temporary.write_bytes(raw)
        temporary.replace(target)
    return target.resolve(), digest


def load_manifest(path: Path, *, today: date | None = None) -> tuple[str, list[BenchmarkTask], str]:
    """Load a dated task snapshot and reject stale expectations by default."""
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
        raise ValueError("manifest must be an object with a tasks list")
    suite = str(payload.get("suite") or path.stem)
    tasks = [BenchmarkTask.model_validate(item) for item in payload["tasks"]]
    current = today or datetime.now(UTC).date()
    stale = [
        task.id
        for task in tasks
        if (task.valid_from and current < date.fromisoformat(task.valid_from))
        or (task.valid_until and current > date.fromisoformat(task.valid_until))
    ]
    if stale:
        raise ValueError(f"manifest expectations are outside their validity window: {stale}")
    return suite, tasks, hashlib.sha256(raw).hexdigest()


def append_time_slice(
    path: Path,
    report: dict[str, Any],
    manifest_sha256: str,
    *,
    report_path: Path,
) -> None:
    """Append one local row bound to results.json; this does not attest wall-clock time."""
    record = evidence_record_from_report(
        report,
        report_path=report_path,
        history_path=path,
    )
    if record["manifest_sha256"] != manifest_sha256:
        raise ValueError("caller manifest hash differs from retained report evidence")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def benchmark_config_evidence(
    cfg: AgentConfig,
    *,
    manifest_sha256: str,
    max_steps_per_task: int,
    discovery_max_steps_per_task: int | None = None,
    direct_task_timeout: int = 600,
    discovery_task_timeout: int = 2400,
    discovery_task_count: int,
    provider: str = "unknown",
    study_manifest_sha256: str = "unknown",
) -> dict[str, Any]:
    """Select final non-secret AgentConfig values that determine benchmark behavior."""
    return {
        "schema_version": 3,
        "manifest_sha256": manifest_sha256,
        "provider": provider,
        "declared_endpoint_access_mode": cfg.endpoint_access_mode,
        "study_manifest_sha256": study_manifest_sha256,
        "max_steps_per_task": max_steps_per_task,
        "discovery_max_steps_per_task": (
            discovery_max_steps_per_task
            if discovery_max_steps_per_task is not None
            else max_steps_per_task
        ),
        "discovery_task_count": discovery_task_count,
        "browser_profile_mode": cfg.browser_profile_mode,
        "browser_headless": cfg.browser_headless,
        "browser_timeout": cfg.browser_timeout,
        "viewport_width": cfg.viewport_width,
        "viewport_height": cfg.viewport_height,
        "stealth_mode": cfg.stealth_mode,
        "browser_humanize_delays": cfg.browser_humanize_delays,
        "browser_ignore_https_errors": cfg.browser_ignore_https_errors,
        "browser_locale": cfg.browser_locale,
        "browser_timezone_id": cfg.browser_timezone_id,
        "captcha_pause": cfg.captcha_pause,
        "captcha_handling": cfg.captcha_handling,
        "captcha_wait_timeout_seconds": cfg.captcha_wait_timeout_seconds,
        "captcha_poll_interval_seconds": cfg.captcha_poll_interval_seconds,
        "discovery_mode": cfg.discovery_mode,
        "high_risk_action_policy": cfg.high_risk_action_policy,
        "persistent_pdf_cache": cfg.persistent_pdf_cache,
        "task_timeout": cfg.task_timeout,
        "direct_task_timeout": direct_task_timeout,
        "discovery_task_timeout": discovery_task_timeout,
        "tool_timeout": cfg.tool_timeout,
        "api_timeout": cfg.api_timeout,
        "api_hard_timeout": cfg.api_hard_timeout,
        "api_transient_retries": cfg.api_transient_retries,
        "api_retry_base_seconds": cfg.api_retry_base_seconds,
        "api_retry_max_seconds": cfg.api_retry_max_seconds,
        "use_vllm": cfg.use_vllm,
        "allow_google_search": cfg.allow_google_search,
        "search_default_engine": cfg.search_default_engine,
        "search_bing_market": cfg.search_bing_market,
        "strict_search_default_engine": "bing",
        "strict_search_bing_market": "en-US",
        "planner_output_mode": cfg.planner_output_mode,
        "use_structured_output": cfg.use_structured_output,
        "planner_max_tokens": cfg.planner_max_tokens,
        "planner_reasoning_effort": cfg.planner_reasoning_effort,
        "planner_max_attempts": cfg.planner_max_attempts,
        "max_consecutive_failures": cfg.max_consecutive_failures,
        "max_steps": cfg.max_steps,
        "post_action_wait_ms": cfg.post_action_wait_ms,
        "history_context_length": cfg.history_context_length,
        "history_full_result_steps": cfg.history_full_result_steps,
        "enable_loop_detection": cfg.enable_loop_detection,
        "loop_window_size": cfg.loop_window_size,
        "loop_threshold": cfg.loop_threshold,
        "checkpoint_enabled": cfg.checkpoint_enabled,
        "strategy_enabled": cfg.strategy_enabled,
        "strategy_failure_threshold": cfg.strategy_failure_threshold,
        "strategy_no_progress_threshold": cfg.strategy_no_progress_threshold,
        "strategy_max_switches": cfg.strategy_max_switches,
        "use_cdp": cfg.use_cdp,
        "max_snapshot_elements": cfg.max_snapshot_elements,
        "enable_ad_filtering": cfg.enable_ad_filtering,
    }


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
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--model", help="Override the configured planner model")
    parser.add_argument("--max-steps-per-task", type=int, default=8)
    parser.add_argument(
        "--discovery-max-steps-per-task",
        type=int,
        default=12,
        help="Action budget for search-discovery tasks; direct tasks keep --max-steps-per-task",
    )
    parser.add_argument("--direct-task-timeout-seconds", type=int, default=600)
    parser.add_argument("--discovery-task-timeout-seconds", type=int, default=2400)
    parser.add_argument("--shard-count", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--shard-index", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--report-provider", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--search-engine-only",
        action="store_true",
        help="Start discovery tasks from about:blank under the strict anti-shortcut policy",
    )
    parser.add_argument(
        "--captcha-handling",
        choices=("report", "fail", "wait_for_human"),
        default="fail",
    )
    add_study_run_arguments(parser)
    return parser.parse_args(argv)


async def run_benchmark(args: argparse.Namespace) -> int:
    context = _prepare_open_web_benchmark(args)
    report = await _run_open_web_benchmark(args, context)
    return _finish_open_web_benchmark(context, report)


@dataclass(frozen=True, slots=True)
class _OpenWebBenchmarkContext:
    suite: str
    tasks: list[BenchmarkTask]
    manifest_path: Path
    manifest_hash: str
    max_steps_per_task: int
    discovery_max_steps_per_task: int
    direct_task_timeout: int
    discovery_task_timeout: int
    shard_count: int
    shard_index: int
    full_task_count: int
    full_discovery_task_count: int
    discovery_task_count: int
    force_search_engine_only: bool
    has_discovery_tasks: bool
    task_ids_sha256: str
    cfg: AgentConfig
    output_dir: Path
    layout: StudyExecutionLayout
    study_context: StudyRunContext | None
    provider: str
    study_manifest_path: Path | None
    study_manifest_sha256: str
    evidence_manifest_path: Path
    benchmark_config: dict[str, Any]
    benchmark_config_sha256: str


@dataclass(frozen=True, slots=True)
class _OpenWebTaskSelection:
    manifest_path: Path
    suite: str
    tasks: list[BenchmarkTask]
    manifest_hash: str
    max_steps: int
    discovery_max_steps: int
    shard_count: int
    shard_index: int
    full_task_count: int
    full_discovery_count: int
    discovery_count: int
    force_search: bool
    has_discovery: bool
    task_ids_sha256: str


def _select_open_web_tasks(args: argparse.Namespace) -> _OpenWebTaskSelection:
    manifest_path = args.manifest.resolve()
    suite, tasks, manifest_hash = load_manifest(manifest_path)
    max_steps, discovery_max_steps, shard_count, shard_index = _task_budget_and_shard(args)
    full_task_count = len(tasks)
    all_task_ids = sorted(task.id for task in tasks)
    full_discovery_count = sum(task.discovery_required for task in tasks)
    selected_tasks = tasks[shard_index::shard_count]
    if not selected_tasks:
        raise ValueError("selected shard has no tasks")
    selected_tasks = [
        task.model_copy(
            update={"max_steps": discovery_max_steps if task.discovery_required else max_steps}
        )
        for task in selected_tasks
    ]
    force_search = bool(getattr(args, "search_engine_only", False))
    if force_search and any(not task.discovery_required for task in selected_tasks):
        raise ValueError("--search-engine-only requires every task to set discovery_required")
    return _OpenWebTaskSelection(
        manifest_path=manifest_path,
        suite=suite,
        tasks=selected_tasks,
        manifest_hash=manifest_hash,
        max_steps=max_steps,
        discovery_max_steps=discovery_max_steps,
        shard_count=shard_count,
        shard_index=shard_index,
        full_task_count=full_task_count,
        full_discovery_count=full_discovery_count,
        discovery_count=sum(task.discovery_required for task in selected_tasks),
        force_search=force_search,
        has_discovery=bool(full_discovery_count),
        task_ids_sha256=canonical_sha256(all_task_ids),
    )


def _build_open_web_config(
    args: argparse.Namespace, selection: _OpenWebTaskSelection
) -> tuple[AgentConfig, Path, int, int]:
    direct_task_timeout, discovery_task_timeout = _task_timeouts(args)
    configured_output = getattr(args, "output", None)
    condition = "strict-search" if selection.force_search or selection.has_discovery else "open-web"
    overrides: dict[str, Any] = {}
    if model := getattr(args, "model", None):
        overrides["model_name"] = model
    cfg = AgentConfig(
        output_dir=(
            configured_output.resolve()
            if configured_output is not None
            else default_study_dir(selection.suite).resolve()
        ),
        strict_eval_mode=False,
        search_engine_only=False,
        discovery_mode="browser-grounded",
        high_risk_action_policy="deny",
        persistent_pdf_cache=False,
        browser_profile_mode="temporary",
        AGENT_BROWSER_HEADLESS=not args.headed,
        browser_humanize_delays=False,
        browser_ignore_https_errors=False,
        captcha_handling=args.captcha_handling,
        max_steps=30,
        task_timeout=discovery_task_timeout if selection.has_discovery else direct_task_timeout,
        planner_max_tokens=6000 if selection.has_discovery else 4096,
        planner_reasoning_effort="low" if selection.has_discovery else None,
        use_structured_output=selection.has_discovery,
        elicit_terminal_confidence=True,
        **overrides,
    )
    output_dir = (
        configured_output.resolve()
        if configured_output is not None
        else allocate_execution_dir(
            default_study_dir(selection.suite), model=cfg.model_name, condition=condition
        ).resolve()
    )
    return (
        cfg.model_copy(update={"output_dir": output_dir}),
        output_dir,
        direct_task_timeout,
        discovery_task_timeout,
    )


def _prepare_open_web_evidence(
    args: argparse.Namespace,
    selection: _OpenWebTaskSelection,
    cfg: AgentConfig,
    output_dir: Path,
    direct_task_timeout: int,
    discovery_task_timeout: int,
) -> tuple[
    StudyExecutionLayout,
    StudyRunContext | None,
    str,
    Path | None,
    str,
    Path,
    dict[str, Any],
]:
    layout = StudyExecutionLayout.from_root(output_dir)
    study_context = study_context_from_args(args, model=cfg.model_name)
    if study_context is not None and selection.manifest_hash != study_context.task_manifest_sha256:
        raise ValueError("selected manifest bytes differ from the preregistered study manifest")
    layout.prepare(
        study_id=study_context.study_id if study_context else None,
        task_manifest_sha256=study_context.task_manifest_sha256 if study_context else None,
        task_set_sha256=study_context.task_set_sha256 if study_context else None,
    )
    require_free_space(output_dir)
    provider = (
        study_context.provider
        if study_context is not None
        else _known_provider(getattr(args, "report_provider", None))
    )
    study_manifest_path: Path | None = None
    study_manifest_sha256 = "unknown"
    if study_context is not None:
        study_manifest_path, study_manifest_sha256 = retain_study_manifest_snapshot(
            study_context, output_dir=output_dir
        )
    evidence_manifest_path = retain_manifest_snapshot(
        selection.manifest_path,
        output_dir=output_dir,
        manifest_sha256=selection.manifest_hash,
    )
    benchmark_config = benchmark_config_evidence(
        cfg,
        manifest_sha256=selection.manifest_hash,
        max_steps_per_task=selection.max_steps,
        discovery_max_steps_per_task=selection.discovery_max_steps,
        direct_task_timeout=direct_task_timeout,
        discovery_task_timeout=discovery_task_timeout,
        discovery_task_count=selection.full_discovery_count,
        provider=provider,
        study_manifest_sha256=study_manifest_sha256,
    )
    return (
        layout,
        study_context,
        provider,
        study_manifest_path,
        study_manifest_sha256,
        evidence_manifest_path,
        benchmark_config,
    )


def _prepare_open_web_benchmark(args: argparse.Namespace) -> _OpenWebBenchmarkContext:
    selection = _select_open_web_tasks(args)
    cfg, output_dir, direct_task_timeout, discovery_task_timeout = _build_open_web_config(
        args, selection
    )
    (
        layout,
        study_context,
        provider,
        study_manifest_path,
        study_manifest_sha256,
        evidence_manifest_path,
        benchmark_config,
    ) = _prepare_open_web_evidence(
        args,
        selection,
        cfg,
        output_dir,
        direct_task_timeout,
        discovery_task_timeout,
    )
    return _OpenWebBenchmarkContext(
        suite=selection.suite,
        tasks=selection.tasks,
        manifest_path=selection.manifest_path,
        manifest_hash=selection.manifest_hash,
        max_steps_per_task=selection.max_steps,
        discovery_max_steps_per_task=selection.discovery_max_steps,
        direct_task_timeout=direct_task_timeout,
        discovery_task_timeout=discovery_task_timeout,
        shard_count=selection.shard_count,
        shard_index=selection.shard_index,
        full_task_count=selection.full_task_count,
        full_discovery_task_count=selection.full_discovery_count,
        discovery_task_count=selection.discovery_count,
        force_search_engine_only=selection.force_search,
        has_discovery_tasks=selection.has_discovery,
        task_ids_sha256=selection.task_ids_sha256,
        cfg=cfg,
        output_dir=output_dir,
        layout=layout,
        study_context=study_context,
        provider=provider,
        study_manifest_path=study_manifest_path,
        study_manifest_sha256=study_manifest_sha256,
        evidence_manifest_path=evidence_manifest_path,
        benchmark_config=benchmark_config,
        benchmark_config_sha256=canonical_sha256(benchmark_config),
    )


async def _run_open_web_benchmark(
    args: argparse.Namespace, context: _OpenWebBenchmarkContext
) -> Any:
    planner = _build_planner(context.cfg)
    if isinstance(planner, StubPlanner):
        raise RuntimeError("Open-web benchmark requires a configured API/vLLM planner")
    browser = BrowserController(
        headless=context.cfg.browser_headless,
        temporary_profile=True,
        temporary_profile_root=context.layout.browser_profiles_dir,
        stealth_mode=context.cfg.stealth_mode,
        humanize_delays=False,
    )
    await planner.load()
    try:
        await browser.start()

        async def execute_task(task: BenchmarkTask) -> Any:
            return await _execute_open_web_task(
                task,
                browser,
                context.cfg,
                planner,
                context.force_search_engine_only,
                context.direct_task_timeout,
                context.discovery_task_timeout,
            )

        runner = BenchmarkRunner(
            TerminalStateEvaluator(browser.page, output_dir=context.output_dir),
            execute_task,
            output_dir=context.output_dir,
            execution_prepared=True,
            study_context=context.study_context,
        )
        return await runner.run(
            context.suite,
            context.tasks,
            metadata=_benchmark_metadata(args, context),
        )
    finally:
        await browser.close()
        await planner.unload()


def _benchmark_metadata(
    args: argparse.Namespace, context: _OpenWebBenchmarkContext
) -> dict[str, Any]:
    return {
        "mode": (
            "strict-search-engine-agent"
            if context.force_search_engine_only
            else "mixed-open-web-agent"
            if context.has_discovery_tasks
            else "open-web-agent"
        ),
        "manifest": context.evidence_manifest_path.relative_to(context.output_dir).as_posix(),
        "manifest_sha256": context.manifest_hash,
        "benchmark_config": context.benchmark_config,
        "benchmark_config_sha256": context.benchmark_config_sha256,
        "task_ids_sha256": context.task_ids_sha256,
        "run_id": str(uuid.uuid4()),
        "browser_profile_mode": "temporary",
        "captcha_handling": args.captcha_handling,
        "model": context.cfg.model_name,
        "provider": context.provider,
        "declared_endpoint_access_mode": context.cfg.endpoint_access_mode,
        "study_id": context.study_context.study_id if context.study_context else None,
        "study_manifest": (
            context.study_manifest_path.relative_to(context.output_dir).as_posix()
            if context.study_manifest_path
            else None
        ),
        "study_manifest_sha256": context.study_manifest_sha256,
        "max_steps_per_task": context.max_steps_per_task,
        "discovery_max_steps_per_task": context.discovery_max_steps_per_task,
        "direct_task_timeout": context.direct_task_timeout,
        "discovery_task_timeout": context.discovery_task_timeout,
        "discovery_mode": "browser-grounded",
        "high_risk_action_policy": "deny",
        "stealth_mode": context.cfg.stealth_mode,
        "agent_source_sha256": agent_source_fingerprint(),
        "benchmark_source_sha256": benchmark_source_fingerprint(),
        "shard_count": context.shard_count,
        "shard_index": context.shard_index,
        "full_task_count": context.full_task_count,
        "discovery_task_count": context.full_discovery_task_count,
        "shard_discovery_task_count": context.discovery_task_count,
        "anti_shortcut_contract": "search_engine_only_v8" if context.has_discovery_tasks else None,
    }


def _finish_open_web_benchmark(context: _OpenWebBenchmarkContext, report: Any) -> int:
    payload = report.model_dump(mode="json")
    if context.shard_count == 1:
        evidence_report_path = retain_report_snapshot(
            payload,
            source_path=context.output_dir / "results.json",
            output_dir=context.output_dir,
        )
        append_time_slice(
            context.layout.ledger_path,
            payload,
            context.manifest_hash,
            report_path=evidence_report_path,
        )
    print(
        f"{context.suite}: {report.summary.passed_tasks}/{report.summary.task_count} passed; "
        f"answer grounding {report.summary.answer_grounding_rate:.1%}"
    )
    return int(report.summary.passed_tasks != report.summary.task_count)


def main() -> None:
    raise SystemExit(asyncio.run(run_benchmark(parse_args())))


__all__ = [
    "append_time_slice",
    "benchmark_config_evidence",
    "canonical_sha256",
    "load_manifest",
    "require_free_space",
    "retain_manifest_snapshot",
    "retain_report_snapshot",
    "retain_study_manifest_snapshot",
    "run_benchmark",
]


async def _execute_open_web_task(
    task: BenchmarkTask,
    browser: BrowserController,
    cfg: AgentConfig,
    planner: Planner,
    force_search_engine_only: bool,
    direct_task_timeout: int,
    discovery_task_timeout: int,
) -> Any:
    reset = await browser.reset_session_state()
    if not reset.get("success"):
        raise RuntimeError(f"browser session reset failed: {reset.get('error')}")
    await browser.goto(task.start_url)
    strict_discovery = task.discovery_required or force_search_engine_only
    task_cfg = cfg.model_copy(
        update={
            "output_dir": task_run_dir(cfg.output_dir, task.id),
            "max_steps": task.max_steps,
            "strict_eval_mode": strict_discovery,
            "search_engine_only": strict_discovery,
            "search_default_engine": ("bing" if strict_discovery else cfg.search_default_engine),
            "search_bing_market": "en-US" if strict_discovery else cfg.search_bing_market,
            "task_timeout": discovery_task_timeout if strict_discovery else direct_task_timeout,
        }
    )
    registry = _build_tool_registry(browser, task_cfg, planner)
    allowed_tools = allowed_tools_for_discovery_mode(
        BROWSER_ONLY_TOOLS,
        "browser-grounded",
    )
    assert allowed_tools is not None
    policy = (
        SearchEngineOnlyPolicy(browser, artifacts_dir=task_cfg.artifacts_dir)
        if strict_discovery
        else BrowserGroundedPolicy(
            browser,
            artifacts_dir=task_cfg.artifacts_dir,
            allowed_tools=allowed_tools,
        )
    )
    executor = ToolExecutor(
        registry,
        tool_timeout=task_cfg.tool_timeout,
        allowed_tools=None if strict_discovery else allowed_tools,
        policy=policy,
        risk_policy=ActionRiskPolicy("deny", context_provider=BrowserRiskContext(browser)),
    )
    agent = WebAgent(planner, browser, executor, config=task_cfg)
    return await agent.run(task.goal, max_steps=task.max_steps)


def _task_budget_and_shard(args: argparse.Namespace) -> tuple[int, int, int, int]:
    max_steps_per_task = int(getattr(args, "max_steps_per_task", 8))
    discovery_max_steps_per_task = int(getattr(args, "discovery_max_steps_per_task", 12))
    if max_steps_per_task < 1:
        raise ValueError("--max-steps-per-task must be positive")
    if discovery_max_steps_per_task < 1:
        raise ValueError("--discovery-max-steps-per-task must be positive")
    shard_count = int(getattr(args, "shard_count", 1))
    shard_index = int(getattr(args, "shard_index", 0))
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("shard index must be in [0, shard count)")
    return max_steps_per_task, discovery_max_steps_per_task, shard_count, shard_index


def _task_timeouts(args: argparse.Namespace) -> tuple[int, int]:
    direct = int(getattr(args, "direct_task_timeout_seconds", 600))
    discovery = int(getattr(args, "discovery_task_timeout_seconds", 2400))
    if direct < 1:
        raise ValueError("--direct-task-timeout-seconds must be positive")
    if discovery < 1:
        raise ValueError("--discovery-task-timeout-seconds must be positive")
    return direct, discovery


if __name__ == "__main__":
    main()

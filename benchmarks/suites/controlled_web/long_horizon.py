"""Run a 60-stage controlled workflow with optional checkpoint/browser restart."""

from __future__ import annotations

import argparse
import asyncio
import re
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from benchmarks.core import (
    BROWSER_ONLY_TOOLS,
    add_study_run_arguments,
    allocate_execution_dir,
    default_study_dir,
    execution_model_label,
    study_context_from_args,
    task_run_dir,
)
from benchmarks.environments.controlled_web.long_horizon_site import (
    CUES,
    RECALLS,
    long_horizon_site,
)
from benchmarks.suites.controlled_web.long_horizon_tasks import build_long_horizon_tasks
from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.cli import _build_planner, _build_tool_registry
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall
from webagent.core.protocols import Planner
from webagent.evaluation import (
    BenchmarkRunner,
    BenchmarkTask,
    RunLayout,
    StudyExecutionLayout,
    TerminalStateEvaluator,
)
from webagent.planner.stub import StubPlanner
from webagent.tools.executor import ToolExecutor
from webagent.tools.exposure import allowed_tools_for_discovery_mode
from webagent.tools.policy import BrowserGroundedPolicy
from webagent.tools.risk import ActionRiskPolicy, BrowserRiskContext
from webagent.utils.runtime import agent_source_fingerprint, benchmark_source_fingerprint


def _css(value: str) -> dict[str, str]:
    return {"type": "css", "value": value}


class LongHorizonHarnessPlanner:
    """Deterministic environment baseline; never reported as model performance."""

    vision_actually_works = True

    def __init__(self) -> None:
        self._typed_stages: set[int] = set()
        self._remembered_stages: set[int] = set()
        self.last_call_metadata: dict[str, object] = {}

    async def load(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        del image, question
        return "The deterministic harness baseline does not analyze images."

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall:
        del task, history_text, available_tools
        if "Mission complete" in browser_state.dom_summary:
            return ToolCall(
                tool_name="done",
                parameters={"summary": "CEDAR ORBIT LANTERN DELTA"},
            )
        stage_match = re.search(r"/mission/(\d+)", browser_state.url)
        if stage_match is None:
            raise RuntimeError(f"mission stage is not observable in {browser_state.url}")
        stage = int(stage_match.group(1))
        if "Transient interruption" in browser_state.dom_summary:
            return ToolCall(tool_name="click", parameters={"selector": _css(f"#retry-{stage}")})
        if stage in CUES and stage not in self._remembered_stages:
            self._remembered_stages.add(stage)
            return ToolCall(
                tool_name="remember",
                parameters={"note": f"Mission cue {stage}: {CUES[stage]}"},
            )
        if stage in RECALLS and stage not in self._typed_stages:
            self._typed_stages.add(stage)
            return ToolCall(
                tool_name="type",
                parameters={
                    "selector": _css("#recall-answer"),
                    "text": RECALLS[stage],
                    "delay_ms": 0,
                },
            )
        return ToolCall(
            tool_name="click",
            parameters={"selector": _css(f"#continue-{stage}")},
        )


class _CurrentPageEvaluator(TerminalStateEvaluator):
    """Resolve the page after a deliberate browser restart."""

    def __init__(self, browser: BrowserController, *, output_dir: Path) -> None:
        self._browser_controller = browser
        self._dynamic_output_dir = output_dir

    async def evaluate(self, task: BenchmarkTask, result: Any) -> Any:
        evaluator = TerminalStateEvaluator(
            self._browser_controller.page,
            output_dir=self._dynamic_output_dir,
        )
        return await evaluator.evaluate(task, result)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Exact execution directory; a unique study execution is allocated by default.",
    )
    parser.add_argument(
        "--mode",
        choices=("scripted-harness-baseline", "agent"),
        default="scripted-harness-baseline",
    )
    parser.add_argument("--model", help="Override the configured planner model")
    parser.add_argument(
        "--report-provider",
        default=None,
        help="Provider identity retained for cross-suite empirical portfolios",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--resume-at-step",
        type=int,
        default=35,
        help="Restart Chromium and resume from the checkpoint after this many agent steps; 0 disables.",
    )
    parser.add_argument("--max-steps", type=int, default=100)
    parser.add_argument("--planner-max-tokens", type=int, default=1024)
    parser.add_argument(
        "--planner-reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    add_study_run_arguments(parser)
    return parser.parse_args(argv)


async def run_benchmark(args: argparse.Namespace) -> int:
    if args.max_steps < 70:
        raise ValueError("the 60-stage mission requires --max-steps >= 70")
    if args.resume_at_step < 0 or args.resume_at_step >= args.max_steps:
        raise ValueError("--resume-at-step must be 0 or below --max-steps")
    configured_model = args.model or AgentConfig().model_name
    model_label = execution_model_label(mode=args.mode, configured_model=configured_model)
    output_dir = (
        args.output.resolve()
        if args.output is not None
        else allocate_execution_dir(
            default_study_dir("long-horizon-controlled-v1"),
            model=model_label,
            condition=args.mode,
        )
    )
    layout = StudyExecutionLayout.from_root(output_dir)
    study_context = study_context_from_args(args, model=model_label)
    layout.prepare(
        study_id=(study_context.study_id if study_context else None),
        task_manifest_sha256=(study_context.task_manifest_sha256 if study_context else None),
        task_set_sha256=(study_context.task_set_sha256 if study_context else None),
    )
    cfg = AgentConfig(
        model_name=configured_model,
        output_dir=output_dir,
        strict_eval_mode=False,
        persistent_pdf_cache=False,
        browser_profile_mode="temporary",
        AGENT_BROWSER_HEADLESS=not args.headed,
        browser_humanize_delays=False,
        post_action_wait_ms=0,
        captcha_pause=False,
        high_risk_action_policy="allow",
        max_steps=args.max_steps,
        task_timeout=1200,
        checkpoint_enabled=True,
        history_context_length=12,
        planner_max_tokens=args.planner_max_tokens,
        planner_reasoning_effort=args.planner_reasoning_effort,
    )
    shared_planner: Planner | None = None
    if args.mode == "agent":
        shared_planner = _build_planner(cfg)
        if isinstance(shared_planner, StubPlanner):
            raise RuntimeError("agent mode requires a configured model API or vLLM server")
        await shared_planner.load()
    browser = BrowserController(
        headless=cfg.browser_headless,
        temporary_profile=True,
        temporary_profile_root=layout.browser_profiles_dir,
        stealth_mode=False,
        humanize_delays=False,
    )
    await browser.start()
    try:
        with long_horizon_site() as base_url:
            task = build_long_horizon_tasks(base_url)[0].model_copy(
                update={"max_steps": args.max_steps}
            )

            async def reset_task(_task: BenchmarkTask) -> None:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.post(f"{base_url}/api/reset")
                    response.raise_for_status()

            def make_agent(planner: Planner) -> WebAgent:
                task_cfg = cfg.model_copy(update={"output_dir": task_run_dir(output_dir, task.id)})
                registry = _build_tool_registry(browser, task_cfg, planner)
                allowed_tools = allowed_tools_for_discovery_mode(
                    BROWSER_ONLY_TOOLS, "browser-grounded"
                )
                assert allowed_tools is not None
                policy = BrowserGroundedPolicy(
                    browser,
                    artifacts_dir=task_cfg.artifacts_dir,
                    allowed_tools=allowed_tools,
                )
                executor = ToolExecutor(
                    registry,
                    tool_timeout=task_cfg.tool_timeout,
                    allowed_tools=allowed_tools,
                    policy=policy,
                    risk_policy=ActionRiskPolicy(
                        "allow",
                        context_provider=BrowserRiskContext(browser),
                        trusted_origins={base_url},
                    ),
                )
                return WebAgent(planner, browser, executor, task_cfg)

            async def execute_task(_task: BenchmarkTask) -> Any:
                await browser.goto(task.start_url)
                first_planner = shared_planner or LongHorizonHarnessPlanner()
                first_agent = make_agent(first_planner)
                if args.resume_at_step == 0:
                    return await first_agent.run(task.goal, max_steps=task.max_steps)
                partial = await first_agent.run(task.goal, max_steps=args.resume_at_step)
                if partial.status != "max_steps_reached":
                    raise RuntimeError(
                        "pre-resume phase terminated unexpectedly: " + partial.status
                    )
                checkpoint = RunLayout.from_root(task_run_dir(output_dir, task.id)).checkpoint_path
                await browser.close()
                await browser.start()
                second_planner = shared_planner or LongHorizonHarnessPlanner()
                return await make_agent(second_planner).run(
                    task.goal,
                    max_steps=task.max_steps,
                    resume_from=checkpoint,
                )

            runner = BenchmarkRunner(
                _CurrentPageEvaluator(browser, output_dir=output_dir),
                execute_task,
                output_dir=output_dir,
                reset_task=reset_task,
                execution_prepared=True,
                study_context=study_context,
            )
            report = await runner.run(
                "long-horizon-controlled-v1",
                [task],
                metadata={
                    "run_id": str(uuid.uuid4()),
                    "mode": args.mode,
                    "model": (
                        cfg.model_name if args.mode == "agent" else "scripted-harness-baseline"
                    ),
                    "provider": getattr(args, "report_provider", None) or "unknown",
                    "resume_at_step": args.resume_at_step,
                    "restart_boundary": (
                        "new-temporary-browser-session" if args.resume_at_step else "none"
                    ),
                    "max_steps": args.max_steps,
                    "planner_max_tokens": cfg.planner_max_tokens,
                    "planner_reasoning_effort": cfg.planner_reasoning_effort,
                    "agent_source_sha256": agent_source_fingerprint(),
                    "benchmark_source_sha256": benchmark_source_fingerprint(),
                },
            )
    finally:
        await browser.close()
        if shared_planner is not None:
            await shared_planner.unload()
    task_result = report.tasks[0]
    print(
        f"{report.suite}: passed={task_result.passed}, actions={task_result.action_count}, "
        f"resumed={bool(task_result.trajectory and task_result.trajectory.resumed_from_checkpoint)}"
    )
    print(f"Results: {output_dir / 'results.json'}")
    return 0 if task_result.passed else 1


def main() -> None:
    raise SystemExit(asyncio.run(run_benchmark(parse_args())))


if __name__ == "__main__":
    main()


__all__ = ["LongHorizonHarnessPlanner", "parse_args", "run_benchmark"]

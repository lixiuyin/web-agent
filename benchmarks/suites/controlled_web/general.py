"""Run reproducible general web-interaction tasks through the real agent loop."""

from __future__ import annotations

import argparse
import asyncio
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
from benchmarks.environments.controlled_web.general_site import benchmark_site
from benchmarks.suites.controlled_web.general_tasks import build_tasks
from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.cli import _build_planner, _build_tool_registry
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall
from webagent.core.protocols import Planner
from webagent.evaluation import (
    BenchmarkRunner,
    BenchmarkTask,
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


_SCRIPTED_ACTIONS: dict[str, list[ToolCall]] = {
    "navigate_product": [
        ToolCall(tool_name="click", parameters={"selector": _css("#browse-products")}),
        ToolCall(tool_name="click", parameters={"selector": _css("#product-amber")}),
    ],
    "cross_page_lookup": [
        ToolCall(tool_name="click", parameters={"selector": _css("#research-team")}),
        ToolCall(tool_name="click", parameters={"selector": _css("#mira-profile")}),
    ],
    "submit_profile": [
        ToolCall(
            tool_name="type",
            parameters={"selector": _css("#name"), "text": "Ada Lovelace", "delay_ms": 0},
        ),
        ToolCall(
            tool_name="type",
            parameters={
                "selector": _css("#email"),
                "text": "ada@example.test",
                "delay_ms": 0,
            },
        ),
        ToolCall(
            tool_name="select_dropdown",
            parameters={"selector": _css("#role"), "value": "researcher"},
        ),
        ToolCall(tool_name="click", parameters={"selector": _css("#save-profile")}),
    ],
    "mutate_cart": [
        ToolCall(tool_name="click", parameters={"selector": _css("#product-amber")}),
        ToolCall(tool_name="click", parameters={"selector": _css("#add-amber")}),
    ],
    "dynamic_reveal": [
        ToolCall(
            tool_name="wait_for_element",
            parameters={"selector": _css("#reveal"), "timeout_ms": 5000},
        ),
        ToolCall(tool_name="click", parameters={"selector": _css("#reveal")}),
    ],
    "recover_transient": [
        ToolCall(tool_name="click", parameters={"selector": _css("#retry")}),
    ],
    "login_account": [
        ToolCall(
            tool_name="type",
            parameters={"selector": _css("#username"), "text": "benchmark-agent", "delay_ms": 0},
        ),
        ToolCall(
            tool_name="type",
            parameters={"selector": _css("#password"), "text": "orbit42", "delay_ms": 0},
        ),
        ToolCall(tool_name="click", parameters={"selector": _css("#login")}),
    ],
    "table_lookup": [
        ToolCall(tool_name="click", parameters={"selector": _css("#inventory-nova")}),
    ],
    "map_lookup": [
        ToolCall(tool_name="click", parameters={"selector": _css("#clinic-harbor")}),
    ],
    "create_booking": [
        ToolCall(
            tool_name="type",
            parameters={"selector": _css("#booking-date"), "text": "2026-09-15", "delay_ms": 0},
        ),
        ToolCall(
            tool_name="select_dropdown",
            parameters={"selector": _css("#booking-time"), "value": "14:30"},
        ),
        ToolCall(
            tool_name="type",
            parameters={"selector": _css("#booking-guests"), "text": "3", "delay_ms": 0},
        ),
        ToolCall(tool_name="click", parameters={"selector": _css("#confirm-booking")}),
    ],
    "checkout_purchase": [
        ToolCall(tool_name="click", parameters={"selector": _css("#product-amber")}),
        ToolCall(tool_name="click", parameters={"selector": _css("#add-amber")}),
        ToolCall(tool_name="click", parameters={"selector": _css("#checkout")}),
        ToolCall(
            tool_name="type",
            parameters={"selector": _css("#address"), "text": "42 Orbit Road", "delay_ms": 0},
        ),
        ToolCall(tool_name="click", parameters={"selector": _css("#terms")}),
        ToolCall(tool_name="click", parameters={"selector": _css("#place-order")}),
    ],
}

_SCRIPTED_SUMMARIES = {
    "cross_page_lookup": "Mira Chen is the Reliability Lead; email mira.chen@example.test.",
    "table_lookup": "Nova Stand has the highest Office stock with 37 units.",
    "map_lookup": "Harbor Clinic is closest at 1.2 km and is open until 20:00.",
}


class HarnessBaselinePlanner:
    """Calibrate harness plumbing without claiming agent-quality performance."""

    def __init__(self, task_id: str) -> None:
        self._task_id = task_id
        self._actions = list(_SCRIPTED_ACTIONS[task_id])
        self._index = 0
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
        del task, browser_state, history_text, available_tools
        if self._index < len(self._actions):
            action = self._actions[self._index]
            self._index += 1
            return action
        return ToolCall(
            tool_name="done",
            parameters={
                "summary": _SCRIPTED_SUMMARIES.get(
                    self._task_id, "Deterministic harness baseline actions completed."
                )
            },
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Exact execution directory. By default a unique execution is allocated below "
            "outputs/studies/web-interaction-v1/executions/."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("scripted-harness-baseline", "scripted", "agent"),
        default="scripted-harness-baseline",
        help=(
            "scripted-harness-baseline validates the environment, tools, trace, and judge; "
            "agent evaluates the configured LLM (scripted is a deprecated alias)"
        ),
    )
    parser.add_argument(
        "--tool-set",
        choices=("browser-only", "all"),
        default="browser-only",
        help="browser-only prevents domain-specific one-shot tools from shortcutting tasks",
    )
    parser.add_argument("--headed", action="store_true", help="Show the benchmark browser")
    parser.add_argument("--model", help="Override the configured planner model for this run")
    parser.add_argument(
        "--max-steps-per-task",
        type=int,
        default=12,
        help="Bound each task so one planner loop cannot stall the full matrix",
    )
    parser.add_argument(
        "--disable-loop-detection",
        action="store_true",
        help="Ablation: run without loop detection",
    )
    add_study_run_arguments(parser)
    return parser.parse_args(argv)


async def run_benchmark(args: argparse.Namespace) -> int:
    mode = "scripted-harness-baseline" if args.mode == "scripted" else args.mode
    max_steps_per_task = int(getattr(args, "max_steps_per_task", 12))
    if max_steps_per_task < 1:
        raise ValueError("--max-steps-per-task must be positive")
    requested_model = getattr(args, "model", None)
    configured_model = requested_model or AgentConfig().model_name
    model_label = execution_model_label(
        mode=mode,
        configured_model=configured_model,
    )
    output_dir = (
        args.output.resolve()
        if args.output is not None
        else allocate_execution_dir(
            default_study_dir("web-interaction-v1"),
            model=model_label,
            condition=mode,
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
        elicit_terminal_confidence=True,
        strict_eval_mode=False,
        persistent_pdf_cache=False,
        browser_profile_mode="temporary",
        AGENT_BROWSER_HEADLESS=not args.headed,
        browser_humanize_delays=False,
        post_action_wait_ms=0,
        captcha_pause=False,
        high_risk_action_policy="allow",
        enable_loop_detection=not args.disable_loop_detection,
        max_steps=max_steps_per_task,
        task_timeout=180,
    )
    shared_planner: Planner | None = None
    if mode == "agent":
        shared_planner = _build_planner(cfg)
        if isinstance(shared_planner, StubPlanner):
            raise RuntimeError(
                "Agent mode requires AGENT_MODEL_API_URL and AGENT_MODEL_API_KEY, "
                "or AGENT_USE_VLLM=true"
            )
        await shared_planner.load()

    browser = BrowserController(
        headless=cfg.browser_headless,
        temporary_profile=True,
        temporary_profile_root=layout.browser_profiles_dir,
        stealth_mode=False,
        humanize_delays=False,
    )
    try:
        await browser.start()
        with benchmark_site() as base_url:
            tasks = [
                task.model_copy(update={"max_steps": max_steps_per_task})
                for task in build_tasks(base_url)
            ]

            async def reset_task(_task: BenchmarkTask) -> None:
                reset = await browser.reset_session_state()
                if not reset.get("success"):
                    raise RuntimeError(f"browser session reset failed: {reset.get('error')}")
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.post(f"{base_url}/api/reset")
                    response.raise_for_status()

            async def execute_task(task: BenchmarkTask) -> Any:
                await browser.goto(task.start_url)
                planner = shared_planner or HarnessBaselinePlanner(task.id)
                task_cfg = cfg.model_copy(
                    update={
                        "output_dir": task_run_dir(output_dir, task.id),
                        "max_steps": task.max_steps,
                    }
                )
                registry = _build_tool_registry(browser, task_cfg, planner)
                requested_tools = (
                    BROWSER_ONLY_TOOLS if args.tool_set == "browser-only" else registry.names()
                )
                allowed_tools = allowed_tools_for_discovery_mode(
                    requested_tools, "browser-grounded"
                )
                assert allowed_tools is not None
                grounding_policy = BrowserGroundedPolicy(
                    browser,
                    artifacts_dir=task_cfg.artifacts_dir,
                    allowed_tools=allowed_tools,
                )
                executor = ToolExecutor(
                    registry,
                    tool_timeout=task_cfg.tool_timeout,
                    allowed_tools=allowed_tools,
                    policy=grounding_policy,
                    risk_policy=ActionRiskPolicy(
                        "allow",
                        context_provider=BrowserRiskContext(browser),
                        trusted_origins={base_url},
                    ),
                )
                agent = WebAgent(
                    planner=planner,
                    browser=browser,
                    tool_executor=executor,
                    config=task_cfg,
                )
                return await agent.run(task.goal, max_steps=task.max_steps)

            runner = BenchmarkRunner(
                TerminalStateEvaluator(browser.page),
                execute_task,
                output_dir=output_dir,
                reset_task=reset_task,
                execution_prepared=True,
                study_context=study_context,
            )
            report = await runner.run(
                "web-interaction-v1",
                tasks,
                metadata={
                    "run_id": str(uuid.uuid4()),
                    "mode": mode,
                    "tool_set": args.tool_set,
                    "loop_detection": cfg.enable_loop_detection,
                    "max_steps_per_task": max_steps_per_task,
                    "browser_profile_mode": "temporary",
                    "model": (cfg.model_name if mode == "agent" else "scripted-harness-baseline"),
                    "discovery_mode": "browser-grounded",
                    "high_risk_action_policy": cfg.high_risk_action_policy,
                    "stealth_mode": False,
                    "agent_source_sha256": agent_source_fingerprint(),
                    "benchmark_source_sha256": benchmark_source_fingerprint(),
                },
            )
    finally:
        await browser.close()
        if shared_planner is not None:
            await shared_planner.unload()

    summary = report.summary
    print(
        f"{report.suite}: {summary.passed_tasks}/{summary.task_count} passed "
        f"({summary.success_rate:.1%}), action validity {summary.action_validity_rate:.1%}"
    )
    print(f"Results: {output_dir / 'results.json'}")
    return 0 if summary.passed_tasks == summary.task_count else 1


def main() -> None:
    raise SystemExit(asyncio.run(run_benchmark(parse_args())))


if __name__ == "__main__":
    main()


# One-cycle import compatibility for callers that used the old class name.
ScriptedBenchmarkPlanner = HarnessBaselinePlanner

__all__ = [
    "HarnessBaselinePlanner",
    "ScriptedBenchmarkPlanner",
    "parse_args",
    "run_benchmark",
]

"""Run safe stateful workflows against a deterministic two-origin sandbox."""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
from benchmarks.environments.controlled_web.sandbox_site import (
    SandboxOrigins,
    sandbox_interaction_site,
)
from benchmarks.suites.controlled_web.sandbox_tasks import build_sandbox_tasks
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


def _scripted_actions(task: BenchmarkTask, artifacts_dir: Path) -> list[ToolCall]:
    actions: dict[str, list[ToolCall]] = {
        "spa_hydration_route": [
            ToolCall(
                tool_name="wait_for_element",
                parameters={"selector": _css("#show-active:not([disabled])"), "timeout_ms": 5000},
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#show-active")}),
            ToolCall(
                tool_name="wait_for_element",
                parameters={"selector": _css("#open-orbit"), "timeout_ms": 5000},
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#open-orbit")}),
        ],
        "authenticated_account": [
            ToolCall(
                tool_name="type",
                parameters={
                    "selector": _css("#username"),
                    "text": "benchmark-agent",
                    "delay_ms": 0,
                },
            ),
            ToolCall(
                tool_name="type",
                parameters={
                    "selector": _css("#password"),
                    "text": "orbit42",
                    "delay_ms": 0,
                },
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#sign-in")}),
        ],
        "cross_origin_intake": [
            ToolCall(tool_name="click", parameters={"selector": _css("#continue-intake")}),
            ToolCall(
                tool_name="type",
                parameters={"selector": _css("#intake-owner"), "text": "Ada", "delay_ms": 0},
            ),
            ToolCall(
                tool_name="select_dropdown",
                parameters={"selector": _css("#intake-priority"), "value": "urgent"},
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#submit-intake")}),
        ],
        "download_upload_handoff": [
            ToolCall(
                tool_name="download_file",
                parameters={
                    "selector": _css("#download-payload"),
                    "filename": "sandbox-payload.txt",
                },
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#upload-destination")}),
            ToolCall(
                tool_name="upload_file",
                parameters={
                    "selector": _css("#upload-file"),
                    "path": str(artifacts_dir / "downloads" / "sandbox-payload.txt"),
                },
            ),
            ToolCall(
                tool_name="wait_for_element",
                parameters={"selector": _css("#upload-ready"), "timeout_ms": 5000},
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#submit-upload")}),
        ],
        "sandbox_checkout": [
            ToolCall(tool_name="click", parameters={"selector": _css("#add-orbit")}),
            ToolCall(
                tool_name="type",
                parameters={
                    "selector": _css("#order-address"),
                    "text": "42 Orbit Road",
                    "delay_ms": 0,
                },
            ),
            ToolCall(tool_name="click", parameters={"selector": _css("#order-terms")}),
            ToolCall(tool_name="click", parameters={"selector": _css("#place-sandbox-order")}),
        ],
    }
    return actions[task.id]


class SandboxHarnessBaselinePlanner:
    """Calibrate workflow plumbing without presenting it as model quality."""

    def __init__(self, task: BenchmarkTask, artifacts_dir: Path) -> None:
        self._actions = _scripted_actions(task, artifacts_dir)
        self._index = 0
        self.last_call_metadata: dict[str, object] = {}

    async def load(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        del image, question
        return "The deterministic sandbox harness baseline does not analyze images."

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
            parameters={"summary": "Deterministic sandbox harness baseline completed."},
        )


def _assert_loopback_origins(origins: SandboxOrigins) -> None:
    for value in (origins.primary, origins.secondary):
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(f"sandbox mutation origin is not loopback: {value}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Exact execution directory. By default a unique execution is allocated below "
            "outputs/studies/sandbox-interaction-v1/executions/."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("scripted-harness-baseline", "scripted", "agent"),
        default="scripted-harness-baseline",
        help="scripted is retained as a deprecated alias for scripted-harness-baseline",
    )
    parser.add_argument("--model")
    parser.add_argument(
        "--report-provider",
        default=None,
        help="Provider identity retained for cross-suite empirical portfolios",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--max-steps-per-task", type=int, default=12)
    add_study_run_arguments(parser)
    return parser.parse_args(argv)


async def run_benchmark(args: argparse.Namespace) -> int:
    mode = "scripted-harness-baseline" if args.mode == "scripted" else args.mode
    max_steps = int(getattr(args, "max_steps_per_task", 12))
    if max_steps < 1:
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
            default_study_dir("sandbox-interaction-v1"),
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
        browser_profile_mode="temporary",
        AGENT_BROWSER_HEADLESS=not args.headed,
        browser_humanize_delays=False,
        post_action_wait_ms=0,
        captcha_pause=False,
        high_risk_action_policy="deny",
        persistent_pdf_cache=False,
        max_steps=max_steps,
        task_timeout=180,
    )
    shared_planner: Planner | None = None
    if mode == "agent":
        shared_planner = _build_planner(cfg)
        if isinstance(shared_planner, StubPlanner):
            raise RuntimeError("Agent mode requires a configured API/vLLM planner")
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
        with sandbox_interaction_site() as origins:
            _assert_loopback_origins(origins)
            tasks = [
                task.model_copy(update={"max_steps": max_steps})
                for task in build_sandbox_tasks(origins)
            ]

            async def reset_task(_task: BenchmarkTask) -> None:
                reset = await browser.reset_session_state()
                if not reset.get("success"):
                    raise RuntimeError(f"browser session reset failed: {reset.get('error')}")
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.post(f"{origins.primary}/api/reset")
                    response.raise_for_status()

            async def execute_task(task: BenchmarkTask) -> Any:
                await browser.goto(task.start_url)
                task_output = task_run_dir(output_dir, task.id)
                artifacts_dir = task_output / "artifacts"
                planner = shared_planner or SandboxHarnessBaselinePlanner(task, artifacts_dir)
                task_cfg = cfg.model_copy(
                    update={
                        "output_dir": task_output,
                        "browser_upload_root": artifacts_dir,
                        "max_steps": task.max_steps,
                    }
                )
                registry = _build_tool_registry(browser, task_cfg, planner)
                allowed_tools = allowed_tools_for_discovery_mode(
                    BROWSER_ONLY_TOOLS, "browser-grounded"
                )
                assert allowed_tools is not None
                executor = ToolExecutor(
                    registry,
                    tool_timeout=task_cfg.tool_timeout,
                    allowed_tools=allowed_tools,
                    policy=BrowserGroundedPolicy(
                        browser,
                        artifacts_dir=task_cfg.artifacts_dir,
                        allowed_tools=allowed_tools,
                    ),
                    risk_policy=ActionRiskPolicy(
                        "allow" if task.risk_scope == "sandbox_mutation" else "deny",
                        context_provider=BrowserRiskContext(browser),
                        trusted_origins={origins.primary, origins.secondary},
                    ),
                )
                agent = WebAgent(planner, browser, executor, config=task_cfg)
                return await agent.run(task.goal, max_steps=task.max_steps)

            runner = BenchmarkRunner(
                TerminalStateEvaluator(browser.page, output_dir=output_dir),
                execute_task,
                output_dir=output_dir,
                reset_task=reset_task,
                execution_prepared=True,
                study_context=study_context,
            )
            report = await runner.run(
                "sandbox-interaction-v1",
                tasks,
                metadata={
                    "run_id": str(uuid.uuid4()),
                    "mode": mode,
                    "model": (cfg.model_name if mode == "agent" else "scripted-harness-baseline"),
                    "provider": getattr(args, "report_provider", None) or "unknown",
                    "browser_profile_mode": "temporary",
                    "environment": "loopback-multi-origin-sandbox",
                    "origin_count": 2,
                    "public_mutations_allowed": False,
                    "sandbox_mutations_allowed": True,
                    "agent_source_sha256": agent_source_fingerprint(),
                    "benchmark_source_sha256": benchmark_source_fingerprint(),
                },
            )
    finally:
        await browser.close()
        if shared_planner is not None:
            await shared_planner.unload()

    print(
        f"{report.suite}: {report.summary.passed_tasks}/{report.summary.task_count} passed; "
        f"action validity {report.summary.action_validity_rate:.1%}"
    )
    return 0 if report.summary.passed_tasks == report.summary.task_count else 1


def main() -> None:
    raise SystemExit(asyncio.run(run_benchmark(parse_args())))


if __name__ == "__main__":
    main()


# One-cycle import compatibility for callers that used the old class name.
ScriptedSandboxPlanner = SandboxHarnessBaselinePlanner

__all__ = [
    "SandboxHarnessBaselinePlanner",
    "ScriptedSandboxPlanner",
    "parse_args",
    "run_benchmark",
]

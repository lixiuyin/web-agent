"""Real-browser proof that search-only evaluation blocks and audits shortcuts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from benchmarks.environments.controlled_web.general_site import benchmark_site
from PIL import Image

from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall, ToolResult
from webagent.evaluation.artifacts import RunLayout
from webagent.tools.builtin.browser_tools import GotoTool
from webagent.tools.builtin.task_tools import DoneTool
from webagent.tools.executor import ToolExecutor
from webagent.tools.policy import SearchEngineOnlyPolicy
from webagent.tools.registry import ToolRegistry


class _PolicyPlanner:
    def __init__(self, result_url: str) -> None:
        self._result_url = result_url
        self._calls = 0
        self.last_call_metadata: dict[str, object] = {}

    async def load(self) -> None:
        return None

    async def unload(self) -> None:
        return None

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        del image, question
        return "unused"

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall:
        del task, browser_state
        self._calls += 1
        if self._calls == 1:
            assert "official_report_search:" not in available_tools
            return ToolCall(
                tool_name="official_report_search",
                parameters={"subject": "shortcut attempt"},
            )
        if self._calls == 2:
            assert "not allowed" in history_text
            return ToolCall(tool_name="search", parameters={"query": "catalog"})
        if self._calls == 3:
            return ToolCall(tool_name="goto", parameters={"url": self._result_url})
        return ToolCall(tool_name="done", parameters={"summary": "Reached observed result."})


class _LocalSearchTool:
    _tool_name = "search"
    _tool_description = "Fixture browser search"

    def __init__(self, browser: BrowserController, page_url: str, result_url: str) -> None:
        self._browser = browser
        self._page_url = page_url
        self._result_url = result_url

    def validate_params(self, params: dict[str, Any]) -> None:
        if not params.get("query"):
            raise ValueError("query required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        await self._browser.goto(self._page_url)
        return ToolResult(
            success=True,
            tool_name="search",
            data={
                "query": params["query"],
                "engine": "local-fixture",
                "url": self._page_url,
                "results": [{"title": "Catalog", "url": self._result_url}],
            },
        )


class _ForbiddenTool:
    _tool_name = "official_report_search"
    _tool_description = "Must remain hidden"

    def validate_params(self, _params: dict[str, Any]) -> None:
        return None

    async def execute(self, _params: dict[str, Any]) -> ToolResult:
        raise AssertionError("policy must deny before execution")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_policy_denies_shortcut_then_allows_observed_result(tmp_path: Path) -> None:
    with benchmark_site() as base_url:
        result_url = f"{base_url}/catalog"
        browser = BrowserController(
            headless=True,
            temporary_profile=True,
            stealth_mode=False,
            humanize_delays=False,
        )
        await browser.start()
        try:
            output = tmp_path / "search-policy"
            config = AgentConfig(
                _env_file=None,
                output_dir=output,
                search_engine_only=True,
                strict_eval_mode=True,
                browser_profile_mode="temporary",
                persistent_pdf_cache=False,
                browser_headless=True,
                post_action_wait_ms=0,
                max_consecutive_failures=3,
                max_steps=6,
                task_timeout=30,
            )
            registry = ToolRegistry()
            registry.register(_LocalSearchTool(browser, f"{base_url}/", result_url))
            registry.register(GotoTool(browser=browser))
            registry.register(DoneTool())
            registry.register(_ForbiddenTool())
            executor = ToolExecutor(
                registry,
                policy=SearchEngineOnlyPolicy(browser, artifacts_dir=config.artifacts_dir),
            )
            agent = WebAgent(
                planner=_PolicyPlanner(result_url),
                browser=browser,
                tool_executor=executor,
                config=config,
            )

            result = await agent.run("Find the catalog using browser search")

            assert result.status == "completed"
            assert browser.page.url == result_url
            layout = RunLayout.from_root(output)
            trace = json.loads(layout.trace_path.read_text())
            assert trace["evaluation"]["search_engine_only"] is True
            assert [step["policy"]["decision"] for step in trace["steps"]] == [
                "deny",
                "allow",
                "allow",
                "allow",
            ]
            assert trace["steps"][2]["policy"]["provenance"]["source"] == ("search_planner_visible")
            verification = json.loads(layout.verification_path.read_text())
            assert verification["valid"] is True
        finally:
            await browser.close()

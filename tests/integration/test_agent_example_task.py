"""Integration test: run the README example task through the full agent loop."""

import pytest

from webagent.agent.loop import WebAgent
from webagent.browser.controller import BrowserController
from webagent.core.config import AgentConfig
from webagent.planner.stub import StubPlanner
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry

EXAMPLE_TASK = (
    "Find the most recent technical report (PDF) about Qwen, "
    "then interpret Figure 1 by describing its purpose and key findings"
)


@pytest.fixture
def artifacts_dir(tmp_path):
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "outputs"
    d.mkdir()
    return d


def _build_registry(browser, artifacts_dir, planner=None):
    import webagent.tools.builtin  # noqa: F401

    registry = ToolRegistry()
    kwargs = {"browser": browser, "artifacts_dir": artifacts_dir}
    if planner is not None:
        kwargs["planner"] = planner
    registry.auto_discover(**kwargs)
    return registry


@pytest.mark.integration
@pytest.mark.asyncio
async def test_example_task_completes_gracefully(artifacts_dir, output_dir):
    """The exact README example task should complete without infinite loops or crashes."""
    config = AgentConfig(
        use_vllm=False,
        browser_headless=True,
        max_steps=10,
        max_consecutive_failures=5,
        task_timeout=30,
    )

    planner = StubPlanner()
    await planner.load()

    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()

    try:
        registry = _build_registry(browser, artifacts_dir, planner)
        executor = ToolExecutor(registry)

        agent = WebAgent(
            planner=planner,
            browser=browser,
            tool_executor=executor,
            config=config,
            output_dir=output_dir,
        )

        result = await agent.run(EXAMPLE_TASK)

        assert result.status == "completed"
        assert result.steps_taken >= 1
        assert result.steps_taken <= 10
        assert "LLM" in result.final_result.get("summary", "")
    finally:
        await browser.close()
        await planner.unload()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_example_task_does_not_loop(artifacts_dir, output_dir):
    """Stub planner must not produce infinite retry loops for the example task."""
    config = AgentConfig(
        use_vllm=False,
        browser_headless=True,
        max_steps=20,
        max_consecutive_failures=10,
        task_timeout=60,
    )

    planner = StubPlanner()
    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()

    try:
        registry = _build_registry(browser, artifacts_dir, planner)
        executor = ToolExecutor(registry)
        agent = WebAgent(
            planner=planner,
            browser=browser,
            tool_executor=executor,
            config=config,
            output_dir=output_dir,
        )

        result = await agent.run(EXAMPLE_TASK)

        assert result.status == "completed", f"Expected completed, got {result.status}"
        assert result.steps_taken <= 5, f"Too many steps: {result.steps_taken}"
    finally:
        await browser.close()

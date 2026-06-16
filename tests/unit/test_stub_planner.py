"""Tests for the stub planner."""

import pytest

from webagent.core.models import BrowserState
from webagent.planner.stub import StubPlanner


@pytest.fixture
def planner():
    return StubPlanner()


@pytest.fixture
def state():
    return BrowserState(
        dom_summary="<h1>Test</h1>",
        url="https://example.com",
        title="Test",
        timestamp="2024-01-01",
    )


@pytest.fixture
def blank_state():
    return BrowserState(
        dom_summary="",
        url="about:blank",
        title="",
        timestamp="2024-01-01",
    )


@pytest.mark.asyncio
async def test_stub_always_returns_done(planner, state):
    """Stub planner returns done for any task since it has no reasoning capability."""
    result = await planner.plan_action(
        task="Do something",
        browser_state=state,
        history_text="",
        available_tools="goto, click, done",
    )
    assert result is not None
    assert result.tool_name == "done"
    assert "LLM" in result.parameters.get("summary", "")


@pytest.mark.asyncio
async def test_stub_done_for_screenshot_task(planner, state):
    """Even for screenshot task, stub returns done (no heuristic behavior)."""
    result = await planner.plan_action(
        task="Take a screenshot of the page",
        browser_state=state,
        history_text="",
        available_tools="goto, screenshot, done",
    )
    assert result is not None
    assert result.tool_name == "done"


@pytest.mark.asyncio
async def test_stub_done_for_pdf_task(planner, state):
    """Even for PDF task, stub returns done (no hardcoded URL navigation)."""
    result = await planner.plan_action(
        task="Find the most recent technical report (PDF) about Qwen",
        browser_state=state,
        history_text="",
        available_tools="goto, download_pdf, done",
    )
    assert result is not None
    assert result.tool_name == "done"


@pytest.mark.asyncio
async def test_stub_done_on_blank_page(planner, blank_state):
    """Stub returns done even on blank page."""
    result = await planner.plan_action(
        task="Download a PDF",
        browser_state=blank_state,
        history_text="",
        available_tools="goto, download_pdf, done",
    )
    assert result is not None
    assert result.tool_name == "done"


@pytest.mark.asyncio
async def test_stub_example_task_immediately_done(planner, state):
    """The exact README example task should immediately return done (no infinite loops)."""
    task = "Find the most recent technical report (PDF) about Qwen, then interpret Figure 1 by describing its purpose and key findings"

    result = await planner.plan_action(
        task=task,
        browser_state=state,
        history_text="",
        available_tools="goto, download_pdf, done",
    )
    assert result is not None
    assert result.tool_name == "done"
    assert "LLM" in result.parameters.get("summary", "")


@pytest.mark.asyncio
async def test_stub_analyze_image(planner):
    """Image analysis also requires LLM."""
    result = await planner.analyze_image(None, "What is this?")
    assert "LLM" in result

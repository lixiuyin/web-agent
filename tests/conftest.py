"""Shared pytest fixtures."""

import pytest

from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall, ToolResult


@pytest.fixture
def config():
    return AgentConfig(
        use_vllm=False,
        browser_headless=True,
        max_steps=5,
        task_timeout=30,
    )


@pytest.fixture
def sample_browser_state():
    return BrowserState(
        screenshot=None,
        dom_summary="<h1>Example</h1>",
        url="https://example.com",
        title="Example Domain",
        timestamp="2024-01-01T00:00:00",
    )


@pytest.fixture
def sample_tool_call():
    return ToolCall(
        tool_name="goto",
        parameters={"url": "https://example.com"},
        reasoning="Navigate to example",
    )


@pytest.fixture
def sample_tool_result():
    return ToolResult(
        success=True,
        tool_name="goto",
        data={"url": "https://example.com", "title": "Example Domain"},
    )

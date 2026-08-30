"""Tests for default browser-grounded tool exposure."""

from __future__ import annotations

import pytest

from webagent.core.models import ToolCall, ToolResult
from webagent.tools.executor import ToolExecutor
from webagent.tools.exposure import (
    DIRECT_SOURCE_DISCOVERY_TOOLS,
    allowed_tools_for_discovery_mode,
)
from webagent.tools.registry import ToolRegistry


class _Tool:
    def __init__(self, name: str) -> None:
        self._tool_name = name
        self._tool_description = f"{name} description"

    def validate_params(self, _params: dict[str, object]) -> None:
        return None

    async def execute(self, _params: dict[str, object]) -> ToolResult:
        return ToolResult(success=True, tool_name=self._tool_name)


@pytest.fixture
def registry() -> ToolRegistry:
    result = ToolRegistry()
    result.register(_Tool("search"))
    for name in DIRECT_SOURCE_DISCOVERY_TOOLS:
        result.register(_Tool(name))
    return result


async def test_browser_grounded_hides_and_denies_direct_source_tools(
    registry: ToolRegistry,
) -> None:
    allowed = allowed_tools_for_discovery_mode(registry.names(), "browser-grounded")
    executor = ToolExecutor(registry, allowed_tools=allowed)

    descriptions = executor.get_tool_descriptions()
    denied = await executor.execute(ToolCall(tool_name="official_report_search"))

    assert "search: search description" in descriptions
    assert all(f"{name}:" not in descriptions for name in DIRECT_SOURCE_DISCOVERY_TOOLS)
    assert denied.success is False
    assert "not allowed" in str(denied.error)


async def test_hybrid_explicitly_exposes_direct_source_tools(registry: ToolRegistry) -> None:
    allowed = allowed_tools_for_discovery_mode(registry.names(), "hybrid")
    executor = ToolExecutor(registry, allowed_tools=allowed)

    descriptions = executor.get_tool_descriptions()
    result = await executor.execute(ToolCall(tool_name="official_report_search"))

    assert "official_report_search: official_report_search description" in descriptions
    assert result.success is True


def test_unknown_discovery_mode_fails_closed(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError, match="Unsupported discovery mode"):
        allowed_tools_for_discovery_mode(registry.names(), "direct")

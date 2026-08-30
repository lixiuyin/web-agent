"""Tests for ToolExecutor's timeout safety net and error propagation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from webagent.core.models import ToolCall, ToolResult
from webagent.core.protocols import Tool
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry


class _SlowTool:
    _tool_name = "slow"
    _tool_description = "sleeps"

    def validate_params(self, params: dict[str, Any]) -> None:
        del params

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(success=True, tool_name="slow")


class _FastTool:
    _tool_name = "fast"
    _tool_description = "instant"

    def validate_params(self, params: dict[str, Any]) -> None:
        del params

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, tool_name="fast", data={"ok": True})


class _BoomTool:
    _tool_name = "boom"
    _tool_description = "raises"

    def validate_params(self, params: dict[str, Any]) -> None:
        del params

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        raise RuntimeError("kaboom")


def _executor(tool: Tool, timeout: float = 0.05) -> ToolExecutor:
    reg = ToolRegistry()
    reg.register(tool)
    return ToolExecutor(reg, tool_timeout=timeout)


async def test_slow_tool_times_out_and_is_cancelled():
    ex = _executor(_SlowTool(), timeout=0.05)
    result = await ex.execute(ToolCall(tool_name="slow", parameters={}))
    assert result.success is False
    assert "timeout" in result.error.lower()


async def test_fast_tool_succeeds_within_timeout():
    ex = _executor(_FastTool(), timeout=5)
    result = await ex.execute(ToolCall(tool_name="fast", parameters={}))
    assert result.success is True
    assert result.data == {"ok": True}


async def test_tool_exception_becomes_error_result():
    ex = _executor(_BoomTool(), timeout=5)
    result = await ex.execute(ToolCall(tool_name="boom", parameters={}))
    assert result.success is False
    assert "kaboom" in result.error


async def test_unknown_tool_returns_error():
    ex = _executor(_FastTool(), timeout=5)
    result = await ex.execute(ToolCall(tool_name="nonexistent", parameters={}))
    assert result.success is False
    assert "unknown tool" in result.error.lower()


async def test_allowed_tools_restrict_descriptions_and_execution():
    registry = ToolRegistry()
    registry.register(_FastTool())
    registry.register(_BoomTool())
    executor = ToolExecutor(registry, allowed_tools={"fast"})

    assert executor.get_tool_descriptions() == "fast: instant"
    denied = await executor.execute(ToolCall(tool_name="boom", parameters={}))
    assert denied.success is False
    assert "not allowed" in denied.error.lower()


async def test_empty_allowed_tools_denies_everything():
    registry = ToolRegistry()
    registry.register(_FastTool())
    executor = ToolExecutor(registry, allowed_tools=set())

    assert executor.get_tool_descriptions() == ""
    denied = await executor.execute(ToolCall(tool_name="fast"))
    assert denied.success is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Tests for ToolExecutor's timeout safety net and error propagation."""

from __future__ import annotations

import asyncio

import pytest

from webagent.core.models import ToolCall, ToolResult
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry


class _SlowTool:
    _tool_name = "slow"
    _tool_description = "sleeps"

    async def execute(self, params: dict) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(success=True, tool_name="slow")


class _FastTool:
    _tool_name = "fast"
    _tool_description = "instant"

    async def execute(self, params: dict) -> ToolResult:
        return ToolResult(success=True, tool_name="fast", data={"ok": True})


class _BoomTool:
    _tool_name = "boom"
    _tool_description = "raises"

    async def execute(self, params: dict) -> ToolResult:
        raise RuntimeError("kaboom")


def _executor(tool: object, timeout: float = 0.05) -> ToolExecutor:
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

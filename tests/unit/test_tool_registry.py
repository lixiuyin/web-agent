"""Tests for the tool registry system."""

import pytest

from webagent.core.models import ToolResult
from webagent.tools.registry import _TOOL_REGISTRY, ToolRegistry, tool


@tool("test_echo", "Echo tool for testing")
class EchoTool:
    def __init__(self, **kw):
        pass

    def validate_params(self, params):
        if "message" not in params:
            raise ValueError("'message' required")

    async def execute(self, params):
        return ToolResult(success=True, tool_name="test_echo", data={"echo": params["message"]})


def test_tool_decorator_registers():
    assert "test_echo" in _TOOL_REGISTRY


def test_registry_auto_discover():
    reg = ToolRegistry()
    reg.auto_discover()
    assert "test_echo" in reg.names()


@pytest.mark.asyncio
async def test_registry_execute_success():
    reg = ToolRegistry()
    reg.auto_discover()
    result = await reg.execute("test_echo", {"message": "hello"})
    assert result.success is True
    assert result.data["echo"] == "hello"


@pytest.mark.asyncio
async def test_registry_execute_validation_error():
    reg = ToolRegistry()
    reg.auto_discover()
    result = await reg.execute("test_echo", {})
    assert result.success is False
    assert "Validation" in (result.error or "")


@pytest.mark.asyncio
async def test_registry_execute_unknown_tool():
    reg = ToolRegistry()
    result = await reg.execute("nonexistent", {})
    assert result.success is False
    assert "Unknown" in (result.error or "")


def test_registry_descriptions():
    reg = ToolRegistry()
    reg.auto_discover()
    desc = reg.descriptions()
    assert "test_echo" in desc
    assert "Echo tool" in desc

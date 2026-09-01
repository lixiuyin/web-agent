"""Tests for the tool registry system."""

from typing import Any

import pytest

from webagent.core.models import ToolResult
from webagent.tools.registry import (
    _TOOL_REGISTRY,
    ToolRegistrationError,
    ToolRegistry,
    ToolSpec,
    tool,
)
from webagent.tools.schemas import TOOL_PARAMETER_SCHEMAS, validate_parameter_schema


@tool("test_echo", "Echo tool for testing")
class EchoTool:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def validate_params(self, params: dict[str, Any]) -> None:
        if "message" not in params:
            raise ValueError("'message' required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, tool_name="test_echo", data={"echo": params["message"]})


def test_tool_decorator_registers():
    assert "test_echo" in _TOOL_REGISTRY


def test_registry_auto_discover():
    reg = ToolRegistry()
    reg.auto_discover()
    assert "test_echo" in reg.names()


def test_auto_discover_preserves_constructor_failure() -> None:
    @tool("broken_constructor", "Raises a TypeError during initialization")
    class BrokenConstructorTool:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            raise TypeError("internal constructor failure")

        def validate_params(self, params: dict[str, Any]) -> None:
            del params

        async def execute(self, params: dict[str, Any]) -> ToolResult:
            del params
            return ToolResult(success=True, tool_name="broken_constructor")

    try:
        reg = ToolRegistry()
        with pytest.raises(ToolRegistrationError, match="broken_constructor") as error:
            reg.auto_discover()
        assert isinstance(error.value.__cause__, TypeError)
    finally:
        _TOOL_REGISTRY.pop("broken_constructor")


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


def test_registry_can_validate_without_executing() -> None:
    reg = ToolRegistry()
    reg.auto_discover()

    assert reg.validate_call("test_echo", {"message": "hello"}) is None
    assert "Validation" in (reg.validate_call("test_echo", {}) or "")
    assert reg.validate_call("missing", {}) == "Unknown tool: missing"


@pytest.mark.asyncio
async def test_registry_enforces_json_schema_before_tool_validator() -> None:
    schema = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }

    @tool("strict_schema_test", "Strict schema test", parameters=schema)
    class StrictSchemaTool:
        def validate_params(self, params: dict[str, Any]) -> None:
            del params

        async def execute(self, params: dict[str, Any]) -> ToolResult:
            return ToolResult(success=True, tool_name="strict_schema_test", data=params)

    try:
        reg = ToolRegistry()
        reg.auto_discover()

        wrong_type = await reg.execute("strict_schema_test", {"message": 3})
        extra = await reg.execute("strict_schema_test", {"message": "ok", "guess": True})

        assert wrong_type.success is False
        assert "not of type 'string'" in (wrong_type.error or "")
        assert extra.success is False
        assert "Additional properties" in (extra.error or "")
    finally:
        _TOOL_REGISTRY.pop("strict_schema_test")


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


def test_registry_specs_are_filtered_and_isolated() -> None:
    reg = ToolRegistry()
    reg.auto_discover()

    specs = reg.specs({"test_echo"})

    assert len(specs) == 1
    assert isinstance(specs[0], ToolSpec)
    assert specs[0].name == "test_echo"
    assert specs[0].parameters["type"] == "object"
    assert specs[0].parameters["additionalProperties"] is True
    specs[0].parameters["properties"]["mutated"] = {"type": "string"}
    assert "mutated" not in reg.specs({"test_echo"})[0].parameters["properties"]


def test_every_builtin_tool_has_a_valid_compact_schema() -> None:
    import webagent.tools.builtin  # noqa: F401

    builtin_names = {
        name
        for name, definition in _TOOL_REGISTRY.items()
        if definition.implementation.__module__.startswith("webagent.tools.builtin")
    }

    assert builtin_names == set(TOOL_PARAMETER_SCHEMAS)
    for name in builtin_names:
        validate_parameter_schema(TOOL_PARAMETER_SCHEMAS[name])

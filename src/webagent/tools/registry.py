"""Plugin-based tool registry with @tool decorator."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from webagent.core.models import ToolResult
from webagent.core.protocols import Tool
from webagent.tools.schemas import (
    JsonSchema,
    parameter_schema_for,
    validate_parameter_schema,
)

logger = logging.getLogger("webagent.tools")


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Immutable metadata captured by :func:`tool`."""

    name: str
    description: str
    implementation: type[Any]
    parameters: JsonSchema


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Provider-neutral function-tool definition exposed to planners."""

    name: str
    description: str
    parameters: JsonSchema

    def as_openai_tool(self) -> dict[str, Any]:
        """Return the OpenAI-compatible Chat Completions tool shape."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(self.parameters),
            },
        }


class ToolRegistrationError(RuntimeError):
    """Raised when a decorated tool cannot be initialized or registered."""


_TOOL_REGISTRY: dict[str, ToolDefinition] = {}


def tool(
    name: str,
    description: str,
    *,
    parameters: JsonSchema | None = None,
) -> Callable[[type[Any]], type[Any]]:
    """Class decorator to register a tool implementation.

    Usage::

        @tool("goto", "Navigate to URL. params: url (string)")
        class GotoTool:
            async def execute(self, params: dict[str, Any]) -> ToolResult: ...
            def validate_params(self, params: dict[str, Any]) -> None: ...
    """

    schema = parameter_schema_for(name, parameters)
    validate_parameter_schema(schema)

    def decorator(cls: type[Any]) -> type[Any]:
        # Preserve these attributes for callers that manually instantiate a
        # decorated tool, while keeping registry metadata independent of the
        # implementation object's shape.
        metadata = {
            "_tool_name": name,
            "_tool_description": description,
            "_tool_parameters_schema": schema,
        }
        for attribute, value in metadata.items():
            setattr(cls, attribute, value)
        _TOOL_REGISTRY[name] = ToolDefinition(name, description, cls, schema)
        return cls

    return decorator


class ToolRegistry:
    """Manages tool instances and provides lookup / discovery."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._descriptions: dict[str, str] = {}
        self._schemas: dict[str, JsonSchema] = {}

    def register(self, tool_instance: Tool) -> None:
        """Register a single tool instance."""
        name = getattr(tool_instance, "_tool_name", None) or getattr(tool_instance, "name", None)
        if not isinstance(name, str) or not name:
            msg = f"Tool {type(tool_instance).__name__} has no non-empty name attribute"
            raise ToolRegistrationError(msg)
        if not isinstance(tool_instance, Tool):
            msg = f"Tool '{name}' does not implement the Tool protocol"
            raise ToolRegistrationError(msg)
        self._tools[name] = tool_instance
        description = getattr(tool_instance, "_tool_description", None) or getattr(
            tool_instance, "description", ""
        )
        self._descriptions[name] = description if isinstance(description, str) else ""
        explicit_schema = getattr(tool_instance, "_tool_parameters_schema", None)
        schema = parameter_schema_for(
            name,
            explicit_schema if isinstance(explicit_schema, dict) else None,
        )
        validate_parameter_schema(schema)
        self._schemas[name] = schema
        logger.debug("Registered tool: %s", name)

    def auto_discover(self, **kwargs: Any) -> None:
        """Instantiate and register all tools decorated with @tool.

        Keyword arguments are passed through to each tool's constructor.
        """
        for name, definition in _TOOL_REGISTRY.items():
            if name not in self._tools:
                try:
                    instance = definition.implementation(**kwargs)
                except Exception as exc:
                    msg = f"Failed to initialize tool '{name}': {exc}"
                    raise ToolRegistrationError(msg) from exc
                if not isinstance(instance, Tool):
                    msg = f"Tool '{name}' does not implement the Tool protocol"
                    raise ToolRegistrationError(msg)
                self._tools[name] = instance
                self._descriptions[name] = definition.description
                self._schemas[name] = deepcopy(definition.parameters)
                logger.debug("Auto-discovered tool: %s", name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def descriptions(self, names: Collection[str] | None = None) -> str:
        """Return compact tool descriptions, optionally restricted to ``names``."""
        allowed = set(names) if names is not None else None
        return "\n".join(
            f"{name}: {self._descriptions.get(name, '')}"
            for name in self._tools
            if allowed is None or name in allowed
        )

    def specs(self, names: Collection[str] | None = None) -> list[ToolSpec]:
        """Return immutable structured definitions, respecting the exposure filter."""
        allowed = {name.casefold() for name in names} if names is not None else None
        return [
            ToolSpec(
                name=name,
                description=_compact_description(self._descriptions.get(name, "")),
                parameters=deepcopy(self._schemas[name]),
            )
            for name in self._tools
            if allowed is None or name.casefold() in allowed
        ]

    async def execute(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Validate and execute a tool by name."""
        validation_error = self.validate_call(name, params)
        if validation_error is not None:
            return ToolResult(success=False, tool_name=name, error=validation_error)
        impl = self._tools[name]

        try:
            return await impl.execute(params)
        except Exception as exc:
            return ToolResult(success=False, tool_name=name, error=f"Execution: {exc}")

    def validate_call(self, name: str, params: dict[str, Any]) -> str | None:
        """Return a planner-facing validation error without executing the tool."""
        impl = self._tools.get(name)
        if impl is None:
            return f"Unknown tool: {name}"
        try:
            Draft202012Validator(self._schemas[name]).validate(params)
            impl.validate_params(params)
        except (ValidationError, ValueError) as exc:
            return f"Validation: {exc}"
        return None


def _compact_description(description: str, max_chars: int = 500) -> str:
    """Drop prose parameter syntax now represented by JSON Schema and bound size."""
    marker = " params:"
    index = description.casefold().find(marker)
    if index >= 0:
        description = description[:index]
    normalized = " ".join(description.split())
    return normalized[:max_chars].rstrip()


__all__ = [
    "ToolDefinition",
    "ToolRegistrationError",
    "ToolRegistry",
    "ToolSpec",
    "tool",
]

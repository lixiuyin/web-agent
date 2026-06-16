"""Plugin-based tool registry with @tool decorator."""

from __future__ import annotations

import logging
from typing import Any

from webagent.core.models import ToolResult

logger = logging.getLogger("webagent.tools")

_TOOL_REGISTRY: dict[str, type] = {}


def tool(name: str, description: str):
    """Class decorator to register a tool implementation.

    Usage::

        @tool("goto", "Navigate to URL. params: url (string)")
        class GotoTool:
            async def execute(self, params: dict) -> ToolResult: ...
            def validate_params(self, params: dict) -> None: ...
    """

    def decorator(cls: type) -> type:
        cls._tool_name = name  # type: ignore[attr-defined]
        cls._tool_description = description  # type: ignore[attr-defined]
        _TOOL_REGISTRY[name] = cls
        return cls

    return decorator


class ToolRegistry:
    """Manages tool instances and provides lookup / discovery."""

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, tool_instance: Any) -> None:
        """Register a single tool instance."""
        name = getattr(tool_instance, "_tool_name", None) or getattr(tool_instance, "name", None)
        if name is None:
            raise ValueError(f"Tool {tool_instance} has no name attribute")
        self._tools[name] = tool_instance
        logger.debug("Registered tool: %s", name)

    def auto_discover(self, **kwargs: Any) -> None:
        """Instantiate and register all tools decorated with @tool.

        Keyword arguments are passed through to each tool's constructor.
        """
        for name, cls in _TOOL_REGISTRY.items():
            if name not in self._tools:
                try:
                    instance = cls(**kwargs)
                    self._tools[name] = instance
                except TypeError:
                    instance = cls()
                    self._tools[name] = instance
                logger.debug("Auto-discovered tool: %s", name)

    def get(self, name: str) -> Any | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def descriptions(self) -> str:
        """Return a compact string of all tool descriptions for LLM prompts."""
        lines: list[str] = []
        for name, t in self._tools.items():
            desc = getattr(t, "_tool_description", getattr(t, "description", ""))
            lines.append(f"{name}: {desc}")
        return "\n".join(lines)

    async def execute(self, name: str, params: dict[str, Any]) -> ToolResult:
        """Validate and execute a tool by name."""
        impl = self._tools.get(name)
        if impl is None:
            return ToolResult(success=False, tool_name=name, error=f"Unknown tool: {name}")

        try:
            if hasattr(impl, "validate_params"):
                impl.validate_params(params)
        except ValueError as e:
            return ToolResult(success=False, tool_name=name, error=f"Validation: {e}")

        try:
            return await impl.execute(params)
        except Exception as e:
            return ToolResult(success=False, tool_name=name, error=f"Execution: {e}")

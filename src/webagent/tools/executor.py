"""Tool executor that dispatches ToolCall objects to the registry."""

from __future__ import annotations

import asyncio

from webagent.core.models import ToolCall, ToolResult
from webagent.tools.registry import ToolRegistry

# Default per-tool wall-clock timeout.  Long-running tools (MinerU, PDF parse)
# should enforce their own internal timeouts; this is a safety net.
_DEFAULT_TOOL_TIMEOUT = 600  # 10 minutes


class ToolExecutor:
    """Thin wrapper that dispatches ToolCalls to a ToolRegistry."""

    def __init__(self, registry: ToolRegistry, tool_timeout: int = _DEFAULT_TOOL_TIMEOUT) -> None:
        self._registry = registry
        self._tool_timeout = tool_timeout

    def get_tool_descriptions(self) -> str:
        return self._registry.descriptions()

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        name = (tool_call.tool_name or "").lower()
        params = tool_call.parameters or {}
        try:
            return await asyncio.wait_for(
                self._registry.execute(name, params),
                timeout=self._tool_timeout,
            )
        except TimeoutError:
            return ToolResult(
                success=False,
                tool_name=name,
                error=f"Tool '{name}' exceeded {self._tool_timeout}s timeout and was cancelled",
            )

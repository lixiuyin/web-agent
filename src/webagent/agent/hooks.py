"""Built-in lifecycle hooks."""

from __future__ import annotations

import json
import logging
from typing import Any

from webagent.agent.context import planner_context
from webagent.core.models import ToolCall, ToolResult

logger = logging.getLogger("webagent")


class LoggingHook:
    """Logs agent lifecycle events to the Python logging system."""

    def __init__(self, verbose: bool = True) -> None:
        """Initialize with optional verbose output."""
        self.verbose = verbose

    def _format_params(self, params: dict[str, Any]) -> str:
        """Format parameters for display."""
        if not params:
            return "{}"
        # Truncate long strings for readability
        formatted = {}
        for k, v in params.items():
            if isinstance(v, str) and len(v) > 80:
                formatted[k] = v[:80] + "..."
            else:
                formatted[k] = v
        return json.dumps(formatted, ensure_ascii=False)

    def _format_data(self, data: dict[str, Any], tool_name: str = "") -> str:
        """Format result data for display."""
        if not data:
            return "{}"
        data = planner_context(tool_name, data)
        # Limit output size
        formatted = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 100:
                formatted[k] = v[:100] + "..."
            else:
                formatted[k] = v
        rendered = json.dumps(formatted, ensure_ascii=False)
        return rendered[:5000] + ("...[truncated]" if len(rendered) > 5000 else "")

    async def on_task_start(self, task: str) -> None:
        logger.info("Starting task: %s", task)

    async def on_step_complete(
        self, step_number: int, tool_call: ToolCall, tool_result: ToolResult
    ) -> None:
        # Basic status line
        status_str = "✓" if tool_result.success else "✗"
        logger.info(
            "Step %d: %s %s -> %s",
            step_number,
            status_str,
            tool_call.tool_name,
            "success" if tool_result.success else "failed",
        )

        if not self.verbose:
            return

        # Detailed information
        if tool_call.reasoning:
            logger.info("  └─ Reasoning: %s", tool_call.reasoning)

        if tool_call.parameters:
            logger.info("  └─ Parameters: %s", self._format_params(tool_call.parameters))

        if tool_result.success and tool_result.data:
            logger.info(
                "  └─ Result: %s",
                self._format_data(tool_result.data, tool_call.tool_name),
            )

        if not tool_result.success and tool_result.error:
            logger.info("  └─ Error: %s", tool_result.error)
            if tool_result.data:
                logger.info(
                    "  └─ Failure details: %s",
                    self._format_data(tool_result.data, tool_call.tool_name),
                )

    async def on_task_end(self, status: str, steps: int) -> None:
        logger.info("Task finished: status=%s, steps=%d", status, steps)

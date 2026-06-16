"""Built-in lifecycle hooks."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from webagent.core.models import AgentStep, ToolCall, ToolResult

logger = logging.getLogger("webagent")


class LoggingHook:
    """Logs agent lifecycle events to the Python logging system."""

    def __init__(self, verbose: bool = True) -> None:
        """Initialize with optional verbose output."""
        self.verbose = verbose

    def _format_params(self, params: dict) -> str:
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

    def _format_data(self, data: dict) -> str:
        """Format result data for display."""
        if not data:
            return "{}"
        # Limit output size
        formatted = {}
        for k, v in data.items():
            if isinstance(v, str) and len(v) > 100:
                formatted[k] = v[:100] + "..."
            else:
                formatted[k] = v
        return json.dumps(formatted, ensure_ascii=False)

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
            logger.info("  └─ Result: %s", self._format_data(tool_result.data))

        if not tool_result.success and tool_result.error:
            logger.info("  └─ Error: %s", tool_result.error)

    async def on_task_end(self, status: str, steps: int) -> None:
        logger.info("Task finished: status=%s, steps=%d", status, steps)


class FileLoggingHook:
    """Persists session history to a JSON file when the task ends."""

    def __init__(self, log_dir: Path, session_id: str) -> None:
        self._log_dir = log_dir
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._steps: list[AgentStep] = []

    async def on_task_start(self, task: str) -> None:
        pass

    async def on_step_complete(
        self, step_number: int, tool_call: ToolCall, tool_result: ToolResult
    ) -> None:
        pass

    async def on_task_end(self, status: str, steps: int) -> None:
        pass

    def save_history(self, steps: list[AgentStep]) -> None:
        path = self._log_dir / f"{self._session_id}_history.json"
        data = [s.model_dump(exclude={"browser_state": {"screenshot"}}) for s in steps]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

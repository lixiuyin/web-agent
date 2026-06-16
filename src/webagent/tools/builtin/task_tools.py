"""Task lifecycle tools."""

from __future__ import annotations

from typing import Any

from webagent.core.models import ToolResult
from webagent.tools.registry import tool


@tool(
    "done",
    "Mark task complete with final answer. params: summary (string, REQUIRED - the actual answer to the user's question, not just 'done'), attachments? (list of file paths to include)",
)
class DoneTool:
    def __init__(self, **kw: Any) -> None:
        pass

    def validate_params(self, params: dict) -> None:
        summary = params.get("summary") or params.get("result")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("'summary' is required and must be a non-empty answer")

    async def execute(self, params: dict) -> ToolResult:
        summary = params.get("summary") or params.get("result") or ""
        attachments = params.get("attachments", [])
        return ToolResult(
            success=True,
            tool_name="done",
            data={"summary": summary, "attachments": attachments},
        )

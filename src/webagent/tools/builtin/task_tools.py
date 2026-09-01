"""Task lifecycle tools."""

from __future__ import annotations

import math
from typing import Any

from webagent.agent.state import validate_durable_note
from webagent.core.models import ToolResult
from webagent.tools.registry import tool


@tool(
    "remember",
    "Store one short, non-sensitive fact in durable controller memory for later steps and "
    "checkpoint resume. params: note (string, REQUIRED). Never store passwords, tokens, URLs, "
    "email addresses, personal data, or other secrets.",
)
class RememberTool:
    """Explicit bounded memory write whose output is checkpoint-safe."""

    def __init__(self, **kw: Any) -> None:
        del kw

    def validate_params(self, params: dict[str, Any]) -> None:
        validate_durable_note(params.get("note"))

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name="remember",
            data={"note": validate_durable_note(params.get("note"))},
        )


@tool(
    "done",
    "Mark task complete with final answer. For interaction tasks, call this only after the "
    "current page confirms submission/completion and every requested form control or file step "
    "was actually performed; typing or uploading alone is not submission. params: summary "
    "(string, REQUIRED - the actual answer to the user's question, not placeholder punctuation "
    "or just 'done'), attachments? (list of file paths to include), success_probability? "
    "(number from 0 to 1 estimating task success before external judging)",
)
class DoneTool:
    def __init__(self, **kw: Any) -> None:
        del kw

    def validate_params(self, params: dict[str, Any]) -> None:
        summary = params.get("summary") or params.get("result")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("'summary' is required and must be a non-empty answer")
        if not any(character.isalnum() for character in summary):
            raise ValueError("'summary' must contain an actual answer, not placeholder punctuation")
        probability = params.get("success_probability")
        if probability is not None and (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(float(probability))
            or not 0.0 <= float(probability) <= 1.0
        ):
            raise ValueError("'success_probability' must be a finite number from 0 to 1")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        summary = params.get("summary") or params.get("result") or ""
        attachments = params.get("attachments", [])
        data: dict[str, Any] = {"summary": summary, "attachments": attachments}
        if "success_probability" in params:
            data["success_probability"] = float(params["success_probability"])
        return ToolResult(
            success=True,
            tool_name="done",
            data=data,
        )


__all__ = ["DoneTool", "RememberTool"]

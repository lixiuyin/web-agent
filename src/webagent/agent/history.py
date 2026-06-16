"""Session history management."""

from __future__ import annotations

import json
from typing import Any

from webagent.core.models import AgentStep


class SessionHistory:
    """Maintains a list of agent steps and formats them for LLM context."""

    def __init__(self, context_length: int = 10) -> None:
        self._steps: list[AgentStep] = []
        self._context_length = context_length

    def add(self, step: AgentStep) -> None:
        self._steps.append(step)

    def clear(self) -> None:
        self._steps.clear()

    @property
    def steps(self) -> list[AgentStep]:
        return list(self._steps)

    def format_for_llm(self) -> str:
        """Return a compact text summary of recent history."""
        if not self._steps:
            return "No previous actions."

        recent = self._steps[-self._context_length :]
        lines: list[str] = []
        for step in recent:
            action = f"{step.tool_call.tool_name}({json.dumps(step.tool_call.parameters)})"
            if step.tool_result.success:
                if step.tool_result.data:
                    preview = json.dumps(step.tool_result.data, ensure_ascii=False)
                    # Don't truncate analyze_image results - they contain the analysis we need
                    max_len = 3000 if step.tool_call.tool_name == "analyze_image" else 500
                    if len(preview) > max_len:
                        preview = preview[:max_len] + "..."
                    result = f"success, returned: {preview}"
                else:
                    result = "success"
            else:
                result = f"failed: {step.tool_result.error}"
            lines.append(f"Step {step.step_number}: {action} -> {result}")
        return "\n".join(lines)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Serialise history (excluding screenshots) for JSON export."""
        return [step.model_dump(exclude={"browser_state": {"screenshot"}}) for step in self._steps]

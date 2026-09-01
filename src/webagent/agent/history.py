"""Session history management."""

from __future__ import annotations

import json

from webagent.agent.context import planner_result_preview
from webagent.core.models import AgentStep


class SessionHistory:
    """Maintains a list of agent steps and formats them for LLM context."""

    def __init__(self, context_length: int = 10, full_result_steps: int = 2) -> None:
        self._steps: list[AgentStep] = []
        self._context_length = context_length
        self._full_result_steps = full_result_steps

    def add(self, step: AgentStep) -> None:
        self._steps.append(step)

    def clear(self) -> None:
        self._steps.clear()

    def restore(self, steps: list[AgentStep]) -> None:
        """Replace history with validated checkpoint steps."""
        self._steps = list(steps)

    def restore_serialized(self, steps: tuple[dict[str, object], ...]) -> None:
        """Validate screenshot-free checkpoint payloads before restoring them."""
        self.restore([AgentStep.model_validate(step) for step in steps])

    @property
    def steps(self) -> list[AgentStep]:
        return list(self._steps)

    def format_for_llm(self) -> str:
        """Return a compact text summary of recent history."""
        if not self._steps:
            return "No previous actions."

        recent = self._steps[-self._context_length :]
        lines: list[str] = []
        full_results_start = max(0, len(recent) - self._full_result_steps)
        for index, step in enumerate(recent):
            action = f"{step.tool_call.tool_name}({json.dumps(step.tool_call.parameters)})"
            if step.tool_result.success:
                if step.tool_result.data:
                    preview = (
                        planner_result_preview(
                            step.tool_call.tool_name,
                            step.tool_result.data,
                            success=True,
                        )
                        if index >= full_results_start
                        else _older_result_summary(step)
                    )
                    result = f"success, returned: {preview}"
                else:
                    result = "success"
            else:
                result = f"failed: {step.tool_result.error}"
                if step.tool_result.data:
                    preview = planner_result_preview(
                        step.tool_call.tool_name,
                        step.tool_result.data,
                        success=False,
                    )
                    result += f", returned: {preview}"
            policy_missing = step.tool_result.audit.get("latest_missing_prerequisites")
            if isinstance(policy_missing, list):
                if policy_missing:
                    result += ", policy still requires: " + json.dumps(
                        policy_missing, ensure_ascii=False
                    )
                elif step.tool_result.audit.get("latest_evidence_complete") is True:
                    result += ", latest-evidence checklist complete"
            next_action = step.tool_result.audit.get("required_next_action")
            if isinstance(next_action, dict):
                result += ", policy required next action: " + json.dumps(
                    next_action, ensure_ascii=False
                )
            lines.append(f"Step {step.step_number}: {action} -> {result}")
        return "\n".join(lines)


def _older_result_summary(step: AgentStep) -> str:
    """Preserve progress markers without replaying every old SERP into the prompt."""
    data = step.tool_result.data
    scalar_types = (str, int, float, bool)
    summary = {
        key: data[key]
        for key in ("query", "engine", "url", "path", "filename", "found", "figure_number")
        if key in data and isinstance(data[key], scalar_types)
    }
    results = data.get("results")
    if isinstance(results, list):
        summary["result_count"] = len(results)
    summary["detail"] = "full result evidence recorded in run trace; omitted from active context"
    return json.dumps(summary, ensure_ascii=False)

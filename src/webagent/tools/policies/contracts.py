"""Execution policy interfaces and auditable decisions."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Protocol

from playwright.async_api import Page

from webagent.core.models import ToolCall, ToolResult

PLANNER_VISIBLE_URL_PROVENANCE_SOURCES = frozenset(
    {
        "planner_state_current_url",
        "planner_observation_text",
        "goto_planner_visible",
        "open_tab_planner_visible",
        "extract_text_planner_visible",
        "frame_interact_planner_visible",
        "search_planner_visible",
        "get_all_links_planner_visible",
        "get_attribute_planner_visible",
        "get_search_results_planner_visible",
        "get_url_planner_visible",
        "inspect_download_links_planner_visible",
    }
)


class PageProvider(Protocol):
    """Small browser surface needed by URL-provenance policies."""

    @property
    def page(self) -> Page: ...


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One allow/deny decision with evidence suitable for the run trace."""

    allowed: bool
    reason: str
    step: int
    target: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_audit(self, policy_name: str) -> dict[str, Any]:
        return {
            "policy": policy_name,
            "decision": "allow" if self.allowed else "deny",
            "reason": self.reason,
            "policy_step": self.step,
            "target": self.target,
            "provenance": self.provenance,
        }


class ToolExecutionPolicy(Protocol):
    """Authorizes tool calls and records trusted evidence after execution."""

    @property
    def name(self) -> str: ...

    @property
    def allowed_tools(self) -> Collection[str]: ...

    @property
    def prompt_notice(self) -> str: ...

    async def authorize(self, tool_call: ToolCall) -> PolicyDecision: ...

    async def record_result(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        decision: PolicyDecision,
        *,
        planner_visible_result: str,
    ) -> dict[str, Any]: ...

    def denial_audit(self, tool_name: str, reason: str) -> dict[str, Any]: ...

    def reset(self, task: str) -> None: ...

    def export_state(self) -> dict[str, Any]: ...

    def import_state(self, state: dict[str, Any], *, task: str) -> None: ...

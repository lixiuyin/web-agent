"""Protocol definitions for pluggable components.

All major abstractions are defined as ``typing.Protocol`` so that implementations
are structurally typed.  This means any class that has the right methods will
satisfy the protocol—no explicit inheritance required.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from PIL import Image

from webagent.core.models import BrowserState, ToolCall, ToolResult


@runtime_checkable
class Planner(Protocol):
    """Plans the next agent action given the current state."""

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall | None:
        """Return the next tool call, or *None* if planning fails."""
        ...

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        """Describe / answer a question about an image."""
        ...

    async def load(self) -> None:
        """Initialise any heavyweight resources (model weights, connections)."""
        ...

    async def unload(self) -> None:
        """Release resources."""
        ...


@runtime_checkable
class Tool(Protocol):
    """A single tool that the agent can invoke."""

    async def execute(self, params: dict[str, Any]) -> ToolResult: ...

    def validate_params(self, params: dict[str, Any]) -> None: ...


class AgentHook(Protocol):
    """Lifecycle hook for observing / modifying agent behaviour."""

    async def on_task_start(self, task: str) -> None: ...

    async def on_step_complete(
        self,
        step_number: int,
        tool_call: ToolCall,
        tool_result: ToolResult,
    ) -> None: ...

    async def on_task_end(self, status: str, steps: int) -> None: ...

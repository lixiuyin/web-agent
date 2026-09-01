"""Deterministic stub planner used when no model is available."""

from __future__ import annotations

from PIL import Image

from webagent.core.models import BrowserState, ToolCall


class StubPlanner:
    """Fallback planner that requires an LLM backend.

    The stub planner does NOT attempt to solve tasks heuristically.
    It honestly reports that an LLM is required for autonomous planning.
    """

    async def load(self) -> None:
        pass

    async def unload(self) -> None:
        pass

    async def plan_action(
        self,
        task: str,
        browser_state: BrowserState,
        history_text: str,
        available_tools: str,
    ) -> ToolCall | None:
        # Immediately return done - this planner cannot solve any task
        return ToolCall(
            tool_name="done",
            parameters={
                "summary": (
                    "This agent requires an LLM backend to plan and execute web tasks. "
                    "The stub planner does not make autonomous decisions. "
                    "Please configure --api-url and --api-key for an OpenAI-compatible API, "
                    "or use --use-vllm with a local model."
                ),
            },
            reasoning="Stub planner has no reasoning capability; LLM required.",
        )

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        return "Image analysis requires an LLM backend."

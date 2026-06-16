"""Pydantic data models shared across all modules."""

from __future__ import annotations

from enum import Enum
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    MAX_STEPS_REACHED = "max_steps_reached"


class ToolCall(BaseModel):
    """A planned tool invocation from the LLM."""

    tool_name: str = Field(..., description="Name of the tool to execute")
    parameters: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = Field(default="", description="LLM rationale")


class ToolResult(BaseModel):
    """Result of a tool execution."""

    success: bool
    tool_name: str
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class BrowserState(BaseModel):
    """Observed browser state at a point in time."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    screenshot: Image.Image | None = None
    dom_summary: str
    url: str
    title: str
    timestamp: str


class AgentStep(BaseModel):
    """Record of a single observe-think-act cycle."""

    step_number: int
    timestamp: str
    browser_state: BrowserState
    tool_call: ToolCall
    tool_result: ToolResult
    duration_seconds: float


class AgentResult(BaseModel):
    """Final result of an agent task execution."""

    success: bool
    status: str
    steps_taken: int
    total_duration: float
    final_result: dict[str, Any] = Field(default_factory=dict)
    history: list[AgentStep] = Field(default_factory=list)

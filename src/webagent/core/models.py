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
    BLOCKED = "blocked"


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
    audit: dict[str, Any] = Field(
        default_factory=dict,
        description="Execution-policy evidence excluded from planner-facing tool data",
    )


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
    tool_duration_seconds: float | None = None


class PlannerAttempt(BaseModel):
    """Auditable record of one planner API/parse attempt."""

    step_number: int
    attempt_number: int
    timestamp: str
    duration_seconds: float
    success: bool
    error: str | None = None
    transport_retries: int = Field(default=0, ge=0)
    response_length: int | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    requested_output_mode: str | None = None
    effective_output_mode: str | None = None
    structured_fallbacks: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Final result of an agent task execution."""

    success: bool
    status: str
    steps_taken: int
    total_duration: float
    final_result: dict[str, Any] = Field(default_factory=dict)
    history: list[AgentStep] = Field(default_factory=list)
    planner_attempts: list[PlannerAttempt] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)

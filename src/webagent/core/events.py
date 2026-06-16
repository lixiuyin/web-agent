"""Event types for the agent lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TaskStarted:
    task: str


@dataclass(frozen=True)
class StepCompleted:
    step_number: int
    tool_name: str
    success: bool
    duration: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskFinished:
    status: str
    steps_taken: int
    total_duration: float

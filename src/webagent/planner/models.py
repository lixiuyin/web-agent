"""Enhanced response models for structured planner output.

Provides structured data models for enhanced planning with explicit
thinking, memory tracking, and goal setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EnhancedToolCall:
    """Enhanced tool call with structured thinking and memory.

    Extends the basic ToolCall model to include:
    - thinking: Analysis of current situation
    - memory: Progress tracking across steps
    - next_goal: Next immediate objective
    - tool_name: Tool to execute
    - parameters: Tool parameters
    - reasoning: Why this action

    This structured output helps the LLM reason more explicitly
    about its progress and prevents repeating failed actions.
    """

    thinking: str
    """Brief analysis of current situation (1-2 sentences)"""

    memory: str
    """Progress tracking - what has been found, visited, or accomplished"""

    next_goal: str
    """Next immediate objective - what this action aims to achieve"""

    tool_name: str
    """Name of the tool to execute"""

    parameters: dict[str, Any]
    """Tool parameters"""

    reasoning: str
    """Why this action is the right choice"""

    def to_tool_call(self) -> tuple[str, dict[str, Any], str]:
        """Convert to basic ToolCall format.

        Returns:
            Tuple of (tool_name, parameters, reasoning)
        """
        return (
            self.tool_name,
            self.parameters,
            self.reasoning,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "thinking": self.thinking,
            "memory": self.memory,
            "next_goal": self.next_goal,
            "tool": self.tool_name,
            "parameters": self.parameters,
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnhancedToolCall | None:
        """Create from dictionary, handling various field name variations.

        Args:
            data: Dictionary from parsed JSON response

        Returns:
            EnhancedToolCall instance or None if invalid
        """
        if not isinstance(data, dict):
            return None

        # Extract with fallbacks for different field names
        thinking = data.get("thinking", "")
        memory = str(data.get("memory", data.get("progress", "")) or "")
        next_goal = str(data.get("next_goal", data.get("goal", data.get("objective", ""))) or "")

        # Tool name has multiple possible field names
        tool_name = (
            data.get("tool")
            or data.get("tool_name")
            or data.get("action")
            or data.get("function")
            or ""
        )
        if not tool_name:
            return None

        parameters = data.get("parameters", data.get("params", data.get("arguments", {})))
        if not isinstance(parameters, dict):
            parameters = {}

        reasoning = (
            data.get("reasoning")
            or data.get("reason")
            or data.get("thought")
            or data.get("explanation")
            or ""
        )

        return cls(
            thinking=thinking,
            memory=memory,
            next_goal=next_goal,
            tool_name=tool_name,
            parameters=parameters,
            reasoning=reasoning,
        )

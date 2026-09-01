"""Agent runtime: loop orchestration, history, and hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from webagent.agent.loop import WebAgent


def __getattr__(name: str) -> Any:
    """Preserve ``webagent.agent.WebAgent`` without eager import cycles."""
    if name == "WebAgent":
        from webagent.agent.loop import WebAgent

        return WebAgent
    raise AttributeError(name)


__all__ = ["WebAgent"]

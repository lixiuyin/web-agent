"""Webagent planner policy implementing BrowserGym's standard Agent interface."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

from browsergym.core.action.highlevel import HighLevelActionSet  # type: ignore[import-not-found]
from browsergym.experiments import AbstractAgentArgs, Agent  # type: ignore[import-not-found]
from browsergym.experiments.agent import (  # type: ignore[import-not-found]
    default_obs_preprocessor,
)

from benchmarks.suites.browsergym.adapter import (
    browser_state_from_observation,
    browsergym_tool_specs,
    goal_text,
    render_browsergym_action,
)
from webagent.core.config import AgentConfig
from webagent.core.protocols import Planner
from webagent.planner.factory import build_planner
from webagent.planner.stub import StubPlanner
from webagent.tools.registry import ToolSpec


@dataclass
class WebAgentBrowserGymArgs(AbstractAgentArgs):  # type: ignore[misc]
    """Secret-safe BrowserGym factory sharing one loaded planner across tasks."""

    model: str = ""
    benchmark: str = "webarena_verified"
    planner_max_tokens: int | None = None
    planner_reasoning_effort: str | None = None
    _planner: Planner | None = field(default=None, init=False, repr=False, compare=False)
    _loop: asyncio.AbstractEventLoop | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.agent_name = f"webagent-{self.model.replace('/', '-')}"
        if self.benchmark not in {"webarena_verified", "visualwebarena"}:
            raise ValueError(f"unsupported BrowserGym benchmark: {self.benchmark}")

    def __getstate__(self) -> dict[str, Any]:
        """Exclude the loaded planner because it contains API credentials."""
        state = dict(self.__dict__)
        state["_planner"] = None
        state["_loop"] = None
        return state

    def prepare(self) -> None:
        if self._planner is not None:
            return
        config = AgentConfig()
        config.model_name = self.model
        if self.planner_max_tokens is not None:
            config.planner_max_tokens = self.planner_max_tokens
        if self.planner_reasoning_effort is not None:
            config.planner_reasoning_effort = cast(Any, self.planner_reasoning_effort)
        if self.benchmark == "visualwebarena":
            config.planner_screenshot_mode = "always"
        planner = build_planner(config)
        if isinstance(planner, StubPlanner):
            raise RuntimeError(
                "BrowserGym evaluation requires a real planner; configure AGENT_MODEL_API_URL "
                "and AGENT_MODEL_API_KEY or local vLLM"
            )
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(planner.load())
        self._planner = planner

    def make_agent(self) -> WebAgentBrowserGymPolicy:
        if self._planner is None or self._loop is None:
            raise RuntimeError("WebAgentBrowserGymArgs.prepare() must run before make_agent()")
        return WebAgentBrowserGymPolicy(
            planner=self._planner,
            event_loop=self._loop,
            benchmark=self.benchmark,
        )

    def close(self) -> None:
        if self._planner is not None and self._loop is not None:
            self._loop.run_until_complete(self._planner.unload())
            self._loop.close()
        self._planner = None
        self._loop = None


class WebAgentBrowserGymPolicy(Agent):  # type: ignore[misc]
    """Translate BrowserGym observations/actions through the configured planner."""

    def __init__(
        self,
        *,
        planner: Planner,
        event_loop: asyncio.AbstractEventLoop,
        benchmark: str,
    ) -> None:
        self._planner = planner
        self._event_loop = event_loop
        self._history: list[str] = []
        action_subset = "visualwebarena" if benchmark == "visualwebarena" else "webarena"
        self.action_set = HighLevelActionSet(
            subsets=[action_subset],
            multiaction=False,
            strict=False,
            retry_with_force=True,
            demo_mode="off",
        )
        descriptors = cast(list[dict[str, Any]], self.action_set.to_tool_description())
        self._tool_specs: list[ToolSpec] = browsergym_tool_specs(descriptors)
        configure = getattr(planner, "configure_tools", None)
        if callable(configure):
            configure(self._tool_specs)

    def obs_preprocessor(self, obs: dict[str, Any]) -> dict[str, Any]:
        return cast(dict[str, Any], default_obs_preprocessor(obs))

    def get_action(self, obs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        task = goal_text(obs.get("goal_object"))
        state = browser_state_from_observation(obs)
        history = "\n".join(self._history[-12:])
        descriptions = "\n".join(f"{spec.name}: {spec.description}" for spec in self._tool_specs)
        call = self._event_loop.run_until_complete(
            self._planner.plan_action(task, state, history, descriptions)
        )
        if call is None:
            raise RuntimeError("planner returned no BrowserGym action")
        action = render_browsergym_action(call, self._tool_specs)
        last_error = str(obs.get("last_action_error", ""))
        self._history.append(
            f"{len(self._history) + 1}. {action}"
            + (f" -> previous environment error: {last_error}" if last_error else "")
        )
        metadata = getattr(self._planner, "last_call_metadata", {})
        return action, {
            "think": call.reasoning,
            "extra_info": {
                "tool": call.tool_name,
                "parameters": call.parameters,
                "planner": dict(metadata) if isinstance(metadata, dict) else {},
            },
        }


__all__ = ["WebAgentBrowserGymArgs", "WebAgentBrowserGymPolicy"]

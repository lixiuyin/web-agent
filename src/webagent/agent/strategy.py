"""Deterministic strategy switching and replanning triggers.

The manager never authorizes tools and therefore cannot bypass execution or risk
policies.  It only turns observable failure/progress signals into a bounded,
serializable strategy state and a prompt hint for the next planner call.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type StrategyName = Literal[
    "default",
    "search-discovery",
    "semantic-dom",
    "alternate-navigation",
    "visual-grounding",
    "document-local",
    "recovery",
]

_STRATEGY_ORDER: tuple[StrategyName, ...] = (
    "search-discovery",
    "semantic-dom",
    "alternate-navigation",
    "visual-grounding",
    "document-local",
    "recovery",
)

_PROMPT_HINTS: dict[StrategyName, str] = {
    "default": "Use the shortest evidence-grounded route to the next milestone.",
    "search-discovery": (
        "Replan discovery: vary the search query or allowed engine, compare visible candidates, "
        "and preserve explicit source/date evidence."
    ),
    "semantic-dom": (
        "Replan around semantic DOM state: refresh the observation and use visible labels, roles, "
        "or stable CSS attributes instead of repeating the failed selector."
    ),
    "alternate-navigation": (
        "Change navigation route using observed links, tabs, frames, or page history; do not guess "
        "an unseen URL."
    ),
    "visual-grounding": (
        "Use the current screenshot or image-analysis capability to disambiguate the page, then "
        "return to a grounded browser action."
    ),
    "document-local": (
        "Use the downloaded artifact and the smallest suitable local PDF/text/figure operation; "
        "avoid repeating expensive parsing without new evidence."
    ),
    "recovery": (
        "Reassess the active milestone from durable evidence, abandon the failed action signature, "
        "and choose a materially different allowed tool or finish honestly if blocked."
    ),
}


class StrategySwitch(BaseModel):
    """One auditable transition between strategies."""

    model_config = ConfigDict(frozen=True)

    step_number: int = Field(ge=0)
    previous: StrategyName
    current: StrategyName
    reason: str = Field(min_length=1, max_length=1000)


class StrategyState(BaseModel):
    """Checkpoint-safe strategy counters and transition history."""

    model_config = ConfigDict(frozen=True)

    current: StrategyName = "default"
    consecutive_failures: int = Field(default=0, ge=0)
    consecutive_no_progress: int = Field(default=0, ge=0)
    switch_count: int = Field(default=0, ge=0)
    attempted: tuple[StrategyName, ...] = ("default",)
    switches: tuple[StrategySwitch, ...] = ()
    exhausted: bool = False


class StrategyObservation(BaseModel):
    """Controller signal emitted after a planner attempt or tool result."""

    model_config = ConfigDict(frozen=True)

    tool_name: str = ""
    success: bool
    progress: bool
    error: str | None = Field(default=None, max_length=2000)
    policy_denied: bool = False
    loop_type: str | None = Field(default=None, max_length=100)
    planner_failure: bool = False


class StrategyUpdate(BaseModel):
    """Result consumed by the loop before the next planner call."""

    model_config = ConfigDict(frozen=True)

    state: StrategyState
    switch: StrategySwitch | None = None
    replan_required: bool = False
    prompt_hint: str = ""
    exhausted: bool = False


class StrategyManager:
    """Convert repeated failure/no-progress signals into bounded replans."""

    def __init__(
        self,
        state: StrategyState | None = None,
        *,
        failure_threshold: int = 2,
        no_progress_threshold: int = 3,
        max_switches: int = 6,
    ) -> None:
        if failure_threshold < 1 or no_progress_threshold < 1 or max_switches < 1:
            raise ValueError("strategy thresholds and max_switches must be positive")
        self._state = state or StrategyState()
        self.failure_threshold = failure_threshold
        self.no_progress_threshold = no_progress_threshold
        self.max_switches = max_switches

    @property
    def state(self) -> StrategyState:
        return self._state

    @property
    def prompt_hint(self) -> str:
        return _PROMPT_HINTS[self._state.current]

    def restore(self, state: StrategyState) -> None:
        """Restore a validated checkpoint state."""
        self._state = state

    def observe(self, observation: StrategyObservation, *, step_number: int) -> StrategyUpdate:
        failures = self._state.consecutive_failures
        no_progress = self._state.consecutive_no_progress

        if observation.success and observation.progress:
            self._state = self._state.model_copy(
                update={"consecutive_failures": 0, "consecutive_no_progress": 0}
            )
            return self._update()

        failures = 0 if observation.success else failures + 1
        no_progress = no_progress + 1 if not observation.progress else 0
        self._state = self._state.model_copy(
            update={
                "consecutive_failures": failures,
                "consecutive_no_progress": no_progress,
            }
        )

        reason = self._switch_reason(observation)
        if reason is None:
            return self._update()
        if self._state.switch_count >= self.max_switches:
            self._state = self._state.model_copy(update={"exhausted": True})
            return self._update(replan_required=True)

        target = self._choose_strategy(observation)
        if target is None:
            self._state = self._state.model_copy(update={"exhausted": True})
            return self._update(replan_required=True)

        switch = StrategySwitch(
            step_number=step_number,
            previous=self._state.current,
            current=target,
            reason=reason,
        )
        attempted = (
            self._state.attempted
            if target in self._state.attempted
            else (*self._state.attempted, target)
        )
        self._state = self._state.model_copy(
            update={
                "current": target,
                "consecutive_failures": 0,
                "consecutive_no_progress": 0,
                "switch_count": self._state.switch_count + 1,
                "attempted": attempted,
                "switches": (*self._state.switches, switch),
            }
        )
        return self._update(switch=switch, replan_required=True)

    def _switch_reason(self, observation: StrategyObservation) -> str | None:
        if observation.policy_denied:
            return f"execution policy denied {observation.tool_name or 'the action'}"
        if observation.loop_type:
            return f"loop detector reported {observation.loop_type}"
        if observation.planner_failure and self._state.consecutive_failures >= 1:
            return "planner failed to produce an executable action"
        if self._state.consecutive_failures >= self.failure_threshold:
            detail = f": {observation.error}" if observation.error else ""
            return f"{self._state.consecutive_failures} consecutive action failures{detail}"
        if self._state.consecutive_no_progress >= self.no_progress_threshold:
            return f"{self._state.consecutive_no_progress} consecutive actions made no progress"
        return None

    def _choose_strategy(self, observation: StrategyObservation) -> StrategyName | None:
        preferred = _preferred_strategies(observation)
        candidates = (*preferred, *_STRATEGY_ORDER)
        return next(
            (
                candidate
                for candidate in candidates
                if candidate != self._state.current and candidate not in self._state.attempted
            ),
            None,
        )

    def _update(
        self,
        *,
        switch: StrategySwitch | None = None,
        replan_required: bool = False,
    ) -> StrategyUpdate:
        return StrategyUpdate(
            state=self._state,
            switch=switch,
            replan_required=replan_required,
            prompt_hint=self.prompt_hint,
            exhausted=self._state.exhausted,
        )


def _preferred_strategies(observation: StrategyObservation) -> tuple[StrategyName, ...]:
    tool = observation.tool_name.casefold()
    if observation.policy_denied or tool in {"search", "get_search_results"}:
        return ("search-discovery", "alternate-navigation", "recovery")
    if tool.startswith("pdf_") or tool in {"download_pdf", "read_image", "analyze_image"}:
        return ("document-local", "visual-grounding", "recovery")
    if tool in {
        "click",
        "click_link",
        "type",
        "press",
        "hover",
        "select_dropdown",
        "wait_for_element",
        "scroll_to_element",
        "frame_interact",
        "shadow_dom",
    }:
        return ("semantic-dom", "alternate-navigation", "visual-grounding", "recovery")
    if observation.planner_failure:
        return ("recovery", "semantic-dom", "alternate-navigation")
    return ("alternate-navigation", "semantic-dom", "recovery")


__all__ = [
    "StrategyManager",
    "StrategyName",
    "StrategyObservation",
    "StrategyState",
    "StrategySwitch",
    "StrategyUpdate",
]

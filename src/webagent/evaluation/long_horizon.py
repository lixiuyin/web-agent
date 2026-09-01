"""Trajectory diagnostics for long-horizon reliability and recovery."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Sequence
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from webagent.core.models import AgentResult, AgentStep

if TYPE_CHECKING:
    from webagent.evaluation.models import TaskEvaluation


class TrajectoryDiagnostics(BaseModel):
    """Directly observable properties of one tool-use trajectory."""

    schema_version: int = 2
    action_count: int = Field(ge=0)
    distinct_tool_count: int = Field(ge=0)
    tool_entropy_bits: float = Field(ge=0.0)
    repeated_action_rate: float = Field(ge=0.0, le=1.0)
    longest_identical_action_streak: int = Field(ge=0)
    longest_failure_streak: int = Field(ge=0)
    recovery_count: int = Field(ge=0)
    minimum_window_entropy_bits: float | None = Field(default=None, ge=0.0)
    collapse_onset_step: int | None = Field(default=None, ge=1)
    longest_same_state_streak: int = Field(default=0, ge=0)
    stagnation_onset_step: int | None = Field(default=None, ge=1)
    replan_count: int = Field(default=0, ge=0)
    strategy_switch_count: int = Field(default=0, ge=0)
    replan_rate: float = Field(default=0.0, ge=0.0)
    resume_count: int = Field(ge=0)
    resumed_from_checkpoint: bool = False


class HorizonBucket(BaseModel):
    """Performance for one observed action-horizon range."""

    task_count: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_score: float | None = Field(default=None, ge=0.0, le=1.0)


class LongHorizonAnalysis(BaseModel):
    """Suite-level long-horizon outcomes and observable collapse indicators."""

    schema_version: int = 2
    status: Literal["available", "unavailable"]
    reason: str | None = None
    task_count: int = Field(ge=0)
    long_task_count: int = Field(ge=0)
    resumed_task_count: int = Field(ge=0)
    buckets: dict[str, HorizonBucket]
    long_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    long_mean_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reliability_degradation: float | None = Field(default=None, ge=-1.0, le=1.0)
    collapse_incidence: float | None = Field(default=None, ge=0.0, le=1.0)
    stagnation_incidence: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_recovery_count: float | None = Field(default=None, ge=0.0)
    mean_replan_count: float | None = Field(default=None, ge=0.0)
    mean_strategy_switch_count: float | None = Field(default=None, ge=0.0)
    interpretation_notice: str = (
        "Reliability degradation is short success rate minus long success rate. Collapse onset "
        "tracks repeated actions; stagnation onset tracks extended unchanged page state even when "
        "tools vary. Neither signal is a causal diagnosis of reasoning or memory."
    )


def trajectory_diagnostics(result: AgentResult, *, window_size: int = 5) -> TrajectoryDiagnostics:
    """Calculate deterministic trajectory metrics from retained agent steps and events."""
    if window_size < 2:
        raise ValueError("window_size must be at least 2")
    actions = [step for step in result.history if step.tool_call.tool_name != "done"]
    names = [step.tool_call.tool_name for step in actions]
    signatures = [
        f"{step.tool_call.tool_name}:"
        + json.dumps(step.tool_call.parameters, sort_keys=True, separators=(",", ":"))
        + f"@{step.browser_state.url}"
        for step in actions
    ]
    state_signatures = [f"{step.browser_state.url}@{step.browser_state.title}" for step in actions]
    resume_events = [event for event in result.events if event.get("type") == "run_resumed"]
    replan_count = sum(event.get("type") == "replan" for event in result.events)
    strategy_switch_count = sum(event.get("type") == "strategy_switch" for event in result.events)
    window_entropies = [
        _entropy(names[index : index + window_size])
        for index in range(max(0, len(names) - window_size + 1))
    ]
    collapse_onset = _collapse_onset(result, signatures, window_size=window_size)
    return TrajectoryDiagnostics(
        action_count=len(actions),
        distinct_tool_count=len(set(names)),
        tool_entropy_bits=_entropy(names),
        repeated_action_rate=(
            sum(left == right for left, right in pairwise(signatures)) / (len(signatures) - 1)
            if len(signatures) > 1
            else 0.0
        ),
        longest_identical_action_streak=_longest_streak(signatures),
        longest_failure_streak=_longest_streak(
            [
                "failure" if not step.tool_result.success else f"success-{index}"
                for index, step in enumerate(actions)
            ]
        ),
        recovery_count=_recovery_count(actions),
        minimum_window_entropy_bits=min(window_entropies) if window_entropies else None,
        collapse_onset_step=collapse_onset,
        longest_same_state_streak=_longest_streak(state_signatures),
        stagnation_onset_step=_stagnation_onset(actions, window_size=max(window_size, 8)),
        replan_count=replan_count,
        strategy_switch_count=strategy_switch_count,
        replan_rate=replan_count / len(actions) if actions else 0.0,
        resume_count=max([int(event.get("resume_count", 0)) for event in resume_events] or [0]),
        resumed_from_checkpoint=bool(resume_events),
    )


def analyze_long_horizon(evaluations: Sequence[TaskEvaluation]) -> LongHorizonAnalysis:
    """Compare short and 50+-action outcomes while preserving missing-evidence semantics."""
    grouped = {
        "short": [item for item in evaluations if item.action_count < 20],
        "medium": [item for item in evaluations if 20 <= item.action_count < 50],
        "long": [item for item in evaluations if item.action_count >= 50],
    }
    buckets = {name: _bucket(values) for name, values in grouped.items()}
    long_tasks = grouped["long"]
    if not long_tasks:
        return LongHorizonAnalysis(
            status="unavailable",
            reason="no trajectory reached 50 observable actions",
            task_count=len(evaluations),
            long_task_count=0,
            resumed_task_count=sum(
                bool(item.trajectory and item.trajectory.resumed_from_checkpoint)
                for item in evaluations
            ),
            buckets=buckets,
        )
    short = grouped["short"]
    long_rate = _success_rate(long_tasks)
    diagnostics = [item.trajectory for item in long_tasks if item.trajectory is not None]
    return LongHorizonAnalysis(
        status="available",
        task_count=len(evaluations),
        long_task_count=len(long_tasks),
        resumed_task_count=sum(item.resumed_from_checkpoint for item in diagnostics),
        buckets=buckets,
        long_success_rate=long_rate,
        long_mean_score=sum(item.score for item in long_tasks) / len(long_tasks),
        reliability_degradation=(_success_rate(short) - long_rate if short else None),
        collapse_incidence=(
            sum(item.collapse_onset_step is not None for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
        stagnation_incidence=(
            sum(item.stagnation_onset_step is not None for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
        mean_recovery_count=(
            sum(item.recovery_count for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
        mean_replan_count=(
            sum(item.replan_count for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
        mean_strategy_switch_count=(
            sum(item.strategy_switch_count for item in diagnostics) / len(diagnostics)
            if diagnostics
            else None
        ),
    )


def _entropy(values: Sequence[str]) -> float:
    if not values:
        return 0.0
    counts = Counter(values)
    return -sum((count / len(values)) * math.log2(count / len(values)) for count in counts.values())


def _longest_streak(values: Sequence[str]) -> int:
    longest = current = 0
    previous: str | None = None
    for value in values:
        current = current + 1 if value == previous else 1
        longest = max(longest, current)
        previous = value
    return longest


def _collapse_onset(
    result: AgentResult, signatures: Sequence[str], *, window_size: int
) -> int | None:
    """Flag persistent identical actions only when accompanied by no progress or errors."""
    actions = [step for step in result.history if step.tool_call.tool_name != "done"]
    for index in range(len(signatures) - window_size + 1):
        window = signatures[index : index + window_size]
        if len(set(window)) != 1:
            continue
        steps = actions[index : index + window_size]
        failed = any(not step.tool_result.success for step in steps)
        unchanged = len({(step.browser_state.url, step.browser_state.title) for step in steps}) == 1
        if failed or unchanged:
            return steps[0].step_number
    return None


def _stagnation_onset(actions: Sequence[AgentStep], *, window_size: int) -> int | None:
    """Locate sustained unchanged browser state despite any sequence of tool choices."""
    states = [(step.browser_state.url, step.browser_state.title) for step in actions]
    for index in range(len(states) - window_size + 1):
        if len(set(states[index : index + window_size])) == 1:
            return actions[index].step_number
    return None


def _recovery_count(actions: Sequence[AgentStep]) -> int:
    count = 0
    for previous, current in pairwise(actions):
        tool_recovered = not previous.tool_result.success and current.tool_result.success
        previous_state = (
            previous.browser_state.title + " " + previous.browser_state.dom_summary
        ).casefold()
        current_state = (
            current.browser_state.title + " " + current.browser_state.dom_summary
        ).casefold()
        page_recovered = any(
            marker in previous_state and marker not in current_state
            for marker in ("transient", "temporary error", "unavailable")
        )
        count += tool_recovered or page_recovered
    return count


def _bucket(values: Sequence[TaskEvaluation]) -> HorizonBucket:
    if not values:
        return HorizonBucket(task_count=0)
    return HorizonBucket(
        task_count=len(values),
        success_rate=_success_rate(values),
        mean_score=sum(item.score for item in values) / len(values),
    )


def _success_rate(values: Sequence[TaskEvaluation]) -> float:
    return sum(item.passed for item in values) / len(values)


__all__ = [
    "HorizonBucket",
    "LongHorizonAnalysis",
    "TrajectoryDiagnostics",
    "analyze_long_horizon",
    "trajectory_diagnostics",
]

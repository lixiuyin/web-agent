"""Evidence-bounded coverage diagnostics for general web-agent claims."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from webagent.evaluation.models import TaskEvaluation

_REQUIRED_SCENARIOS = frozenset(
    {
        "search_discovery",
        "spa_interaction",
        "authenticated_session",
        "cross_origin_form",
        "file_workflow",
        "sandbox_transaction",
        "recovery",
    }
)
_REQUIRED_SPLITS = frozenset({"development", "held_out_task", "held_out_setting"})


class GeneralityAnalysis(BaseModel):
    """Observed task-landscape coverage without extrapolating beyond it."""

    schema_version: int = 1
    status: Literal["ready", "insufficient"]
    task_count: int = Field(ge=0)
    category_count: int = Field(ge=0)
    source_origin_count: int = Field(ge=0)
    discovery_task_count: int = Field(ge=0)
    scenario_counts: dict[str, int]
    environment_counts: dict[str, int]
    split_counts: dict[str, int]
    source_origins: list[str]
    missing_requirements: list[str]
    interpretation_notice: str = (
        "Readiness means the retained task set crosses a preregistered coverage floor. It does "
        "not by itself prove performance, robustness, or transfer across the covered settings."
    )


def analyze_generality(evaluations: Sequence[TaskEvaluation]) -> GeneralityAnalysis:
    """Assess whether evaluations cover a minimally broad web-task landscape."""
    scenarios = Counter(item.scenario for item in evaluations)
    environments = Counter(item.environment for item in evaluations)
    splits = Counter(item.split for item in evaluations)
    categories = {item.category for item in evaluations}
    origins = sorted({origin for item in evaluations for origin in item.source_origins})
    missing: list[str] = []
    if len(evaluations) < 30:
        missing.append("requires at least 30 evaluated tasks")
    if len(categories) < 8:
        missing.append("requires at least 8 task categories")
    if len(origins) < 8:
        missing.append("requires at least 8 distinct public source origins")
    absent_scenarios = sorted(_REQUIRED_SCENARIOS - scenarios.keys())
    if absent_scenarios:
        missing.append("missing scenario coverage: " + ", ".join(absent_scenarios))
    absent_splits = sorted(_REQUIRED_SPLITS - splits.keys())
    if absent_splits:
        missing.append("missing research splits: " + ", ".join(absent_splits))
    if environments.get("public_web", 0) == 0 or environments.get("sandbox", 0) == 0:
        missing.append("requires both public_web and sandbox evidence")
    discovery_count = scenarios.get("search_discovery", 0)
    if discovery_count < 5:
        missing.append("requires at least 5 genuine search-discovery tasks")
    return GeneralityAnalysis(
        status="insufficient" if missing else "ready",
        task_count=len(evaluations),
        category_count=len(categories),
        source_origin_count=len(origins),
        discovery_task_count=discovery_count,
        scenario_counts=dict(sorted(scenarios.items())),
        environment_counts=dict(sorted(environments.items())),
        split_counts=dict(sorted(splits.items())),
        source_origins=origins,
        missing_requirements=missing,
    )


__all__ = ["GeneralityAnalysis", "analyze_generality"]

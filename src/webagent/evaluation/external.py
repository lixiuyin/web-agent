"""Typed reports for externally maintained BrowserGym benchmarks."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ExternalTaskResult(BaseModel):
    """One BrowserGym episode, preserving benchmark reward and system errors."""

    task_name: str
    task_id: int = Field(ge=0)
    task_seed: int = Field(ge=0)
    reward: float = Field(ge=0.0)
    success: bool
    steps: int = Field(ge=0)
    terminated: bool
    truncated: bool
    error: str | None = None
    evidence_path: str


class ExternalBenchmarkSummary(BaseModel):
    """Coverage-aware aggregate; incomplete runs never receive an official score."""

    expected_tasks: int = Field(ge=1)
    attempted_tasks: int = Field(ge=0)
    scored_tasks: int = Field(ge=0)
    successful_tasks: int = Field(ge=0)
    system_error_tasks: int = Field(ge=0)
    coverage: float = Field(ge=0.0, le=1.0)
    mean_reward: float | None = Field(default=None, ge=0.0)
    success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    success_rate_ci95: tuple[float, float] | None = None


class ExternalBenchmarkReport(BaseModel):
    """Independent external-layer report; never merged into internal scores."""

    schema_version: Literal[1] = 1
    layer: Literal["external"] = "external"
    interface: Literal["browsergym"] = "browsergym"
    benchmark: Literal["webarena_verified", "visualwebarena"]
    profile: str
    protocol_status: Literal["official", "custom", "incomplete"]
    provider: str
    model: str
    created_at: str
    max_steps: int = Field(ge=1)
    headless: bool
    task_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    backend_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_versions: dict[str, str]
    summary: ExternalBenchmarkSummary
    tasks: list[ExternalTaskResult]
    interpretation_notice: str = (
        "This is an external BrowserGym benchmark report. Its reward and success rate must be "
        "reported separately from the repository's diagnostic benchmark metrics."
    )


def summarize_external_results(
    tasks: Sequence[ExternalTaskResult],
    *,
    expected_tasks: int,
) -> ExternalBenchmarkSummary:
    """Aggregate completed episodes without hiding environment or planner errors."""
    if expected_tasks < 1:
        raise ValueError("expected_tasks must be positive")
    attempted = len(tasks)
    scored = [task for task in tasks if task.error is None]
    successful = sum(task.success for task in scored)
    rate = successful / len(scored) if scored else None
    return ExternalBenchmarkSummary(
        expected_tasks=expected_tasks,
        attempted_tasks=attempted,
        scored_tasks=len(scored),
        successful_tasks=successful,
        system_error_tasks=attempted - len(scored),
        coverage=min(1.0, attempted / expected_tasks),
        mean_reward=(sum(task.reward for task in scored) / len(scored) if scored else None),
        success_rate=rate,
        success_rate_ci95=_wilson_interval(successful, len(scored)) if scored else None,
    )


def new_external_report(
    *,
    benchmark: Literal["webarena_verified", "visualwebarena"],
    profile: str,
    official_protocol: bool,
    provider: str,
    model: str,
    max_steps: int,
    headless: bool,
    task_set_sha256: str,
    backend_configuration_sha256: str,
    agent_source_sha256: str,
    adapter_source_sha256: str,
    package_versions: dict[str, str],
    expected_tasks: int,
    tasks: Sequence[ExternalTaskResult],
) -> ExternalBenchmarkReport:
    """Build a report and fail closed when the selected task set is incomplete."""
    summary = summarize_external_results(tasks, expected_tasks=expected_tasks)
    complete = summary.attempted_tasks == expected_tasks and summary.scored_tasks == expected_tasks
    status: Literal["official", "custom", "incomplete"] = (
        "incomplete" if not complete else "official" if official_protocol else "custom"
    )
    return ExternalBenchmarkReport(
        benchmark=benchmark,
        profile=profile,
        protocol_status=status,
        provider=provider,
        model=model,
        created_at=datetime.now(UTC).isoformat(),
        max_steps=max_steps,
        headless=headless,
        task_set_sha256=task_set_sha256,
        backend_configuration_sha256=backend_configuration_sha256,
        agent_source_sha256=agent_source_sha256,
        adapter_source_sha256=adapter_source_sha256,
        package_versions=dict(sorted(package_versions.items())),
        summary=summary,
        tasks=list(tasks),
    )


def _wilson_interval(successes: int, count: int) -> tuple[float, float]:
    """Return the two-sided 95% Wilson score interval for a Bernoulli rate."""
    if count < 1 or not 0 <= successes <= count:
        raise ValueError("Wilson interval requires 0 <= successes <= count and count > 0")
    z = 1.959963984540054
    proportion = successes / count
    denominator = 1.0 + z * z / count
    centre = (proportion + z * z / (2.0 * count)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1.0 - proportion) / count + z * z / (4.0 * count * count))
        / denominator
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


__all__ = [
    "ExternalBenchmarkReport",
    "ExternalBenchmarkSummary",
    "ExternalTaskResult",
    "new_external_report",
    "summarize_external_results",
]

"""Calibration metrics for pre-judgment task-success probabilities."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from webagent.evaluation.models import TaskEvaluation


class CalibrationBin(BaseModel):
    """One equal-width reliability bin."""

    lower: float = Field(ge=0.0, le=1.0)
    upper: float = Field(ge=0.0, le=1.0)
    count: int = Field(ge=0)
    mean_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    empirical_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class RiskCoveragePoint(BaseModel):
    """Observed failure risk when retaining the most-confident task subset."""

    coverage: float = Field(gt=0.0, le=1.0)
    selective_risk: float = Field(ge=0.0, le=1.0)
    minimum_confidence: float = Field(ge=0.0, le=1.0)


class CalibrationAnalysis(BaseModel):
    """Coverage-aware calibration report; missing confidence is never imputed."""

    schema_version: int = 1
    status: Literal["available", "unavailable"]
    reason: str | None = None
    task_count: int = Field(ge=0)
    confidence_count: int = Field(ge=0)
    confidence_coverage: float = Field(ge=0.0, le=1.0)
    brier_score: float | None = Field(default=None, ge=0.0, le=1.0)
    log_loss: float | None = Field(default=None, ge=0.0)
    expected_calibration_error: float | None = Field(default=None, ge=0.0, le=1.0)
    bins: list[CalibrationBin] = Field(default_factory=list)
    risk_coverage_curve: list[RiskCoveragePoint] = Field(default_factory=list)
    area_under_risk_coverage_curve: float | None = Field(default=None, ge=0.0, le=1.0)
    interpretation_notice: str = (
        "Metrics cover only tasks with a self-reported success_probability recorded before "
        "external judging; missing values are reported, not imputed."
    )


def analyze_calibration(
    evaluations: Sequence[TaskEvaluation], *, bin_count: int = 10
) -> CalibrationAnalysis:
    """Compute Brier, log-loss, and ECE over available task-success probabilities."""
    if bin_count < 1:
        raise ValueError("bin_count must be positive")
    samples = [
        (float(item.success_probability), float(item.passed))
        for item in evaluations
        if item.success_probability is not None
    ]
    coverage = len(samples) / len(evaluations) if evaluations else 0.0
    if not samples:
        return CalibrationAnalysis(
            status="unavailable",
            reason="no task-success confidence was recorded",
            task_count=len(evaluations),
            confidence_count=0,
            confidence_coverage=coverage,
            bins=_empty_bins(bin_count),
        )

    brier = sum((confidence - outcome) ** 2 for confidence, outcome in samples) / len(samples)
    epsilon = 1e-15
    log_loss = -sum(
        outcome * math.log(min(1.0 - epsilon, max(epsilon, confidence)))
        + (1.0 - outcome) * math.log(min(1.0 - epsilon, max(epsilon, 1.0 - confidence)))
        for confidence, outcome in samples
    ) / len(samples)
    bins = _build_bins(samples, bin_count)
    risk_coverage = _risk_coverage(samples)
    ece = 0.0
    for item in bins:
        if item.mean_confidence is None or item.empirical_success_rate is None:
            continue
        ece += item.count / len(samples) * abs(item.mean_confidence - item.empirical_success_rate)
    return CalibrationAnalysis(
        status="available",
        task_count=len(evaluations),
        confidence_count=len(samples),
        confidence_coverage=coverage,
        brier_score=brier,
        log_loss=log_loss,
        expected_calibration_error=ece,
        bins=bins,
        risk_coverage_curve=risk_coverage,
        area_under_risk_coverage_curve=(
            sum(item.selective_risk for item in risk_coverage) / len(risk_coverage)
        ),
    )


def _empty_bins(bin_count: int) -> list[CalibrationBin]:
    return [
        CalibrationBin(lower=index / bin_count, upper=(index + 1) / bin_count, count=0)
        for index in range(bin_count)
    ]


def _build_bins(samples: list[tuple[float, float]], bin_count: int) -> list[CalibrationBin]:
    grouped: list[list[tuple[float, float]]] = [[] for _ in range(bin_count)]
    for confidence, outcome in samples:
        index = min(bin_count - 1, int(confidence * bin_count))
        grouped[index].append((confidence, outcome))
    bins: list[CalibrationBin] = []
    for index, values in enumerate(grouped):
        bins.append(
            CalibrationBin(
                lower=index / bin_count,
                upper=(index + 1) / bin_count,
                count=len(values),
                mean_confidence=(
                    sum(confidence for confidence, _ in values) / len(values) if values else None
                ),
                empirical_success_rate=(
                    sum(outcome for _, outcome in values) / len(values) if values else None
                ),
            )
        )
    return bins


def _risk_coverage(samples: list[tuple[float, float]]) -> list[RiskCoveragePoint]:
    ordered = sorted(samples, key=lambda item: item[0], reverse=True)
    failures = 0.0
    points: list[RiskCoveragePoint] = []
    for index, (confidence, outcome) in enumerate(ordered, start=1):
        failures += 1.0 - outcome
        points.append(
            RiskCoveragePoint(
                coverage=index / len(ordered),
                selective_risk=failures / index,
                minimum_confidence=confidence,
            )
        )
    return points


__all__ = [
    "CalibrationAnalysis",
    "CalibrationBin",
    "RiskCoveragePoint",
    "analyze_calibration",
]

"""Descriptive development-to-held-out transfer diagnostics."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from webagent.evaluation.models import TaskEvaluation
    from webagent.evaluation.studies import StudyRunRecord

_SPLITS = ("development", "validation", "held_out_task", "held_out_setting")


class SplitPerformance(BaseModel):
    """Outcome metrics for one predeclared task split."""

    status: Literal["available", "unavailable"]
    reason: str | None = None
    task_count: int = Field(ge=0)
    success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_score: float | None = Field(default=None, ge=0.0, le=1.0)


class TransferAnalysis(BaseModel):
    """Held-out performance without unsupported causal transfer claims."""

    schema_version: int = 1
    status: Literal["available", "unavailable"]
    reason: str | None = None
    splits: dict[str, SplitPerformance]
    held_out_task_success_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    held_out_setting_success_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    pooled_held_out_success_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    leakage_groups: list[str] = Field(default_factory=list)
    interpretation_notice: str = (
        "Deltas are descriptive held-out minus development success rates for one condition. "
        "They do not establish that an intervention transfers without a paired baseline."
    )


class PairedConditionEffect(BaseModel):
    """Success effect for condition-matched study cells in one split."""

    status: Literal["available", "unavailable"]
    reason: str | None = None
    baseline_count: int = Field(ge=0)
    intervention_count: int = Field(ge=0)
    paired_count: int = Field(ge=0)
    baseline_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    intervention_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    success_delta: float | None = Field(default=None, ge=-1.0, le=1.0)


class InterventionTransferAnalysis(BaseModel):
    """Paired baseline/intervention effects across development and holdouts."""

    schema_version: int = 1
    status: Literal["available", "unavailable"]
    reason: str | None = None
    baseline_condition_id: str
    intervention_condition_id: str
    study_ids: list[str] = Field(default_factory=list)
    effects: dict[str, PairedConditionEffect]
    held_out_task_transfer_gap: float | None = Field(default=None, ge=-2.0, le=2.0)
    held_out_setting_transfer_gap: float | None = Field(default=None, ge=-2.0, le=2.0)
    interpretation_notice: str = (
        "Effects are paired intervention-minus-baseline success differences. Transfer gaps are "
        "held-out effect minus development effect. They are descriptive unless the study design "
        "supports causal attribution."
    )


def analyze_transfer(evaluations: Sequence[TaskEvaluation]) -> TransferAnalysis:
    """Compare predeclared held-out splits while rejecting leakage."""
    grouped: defaultdict[str, list[TaskEvaluation]] = defaultdict(list)
    for evaluation in evaluations:
        grouped[evaluation.split].append(evaluation)
    splits = {name: _split_performance(grouped[name], name) for name in _SPLITS}

    development = grouped["development"]
    held_out_task = grouped["held_out_task"]
    held_out_setting = grouped["held_out_setting"]
    held_out = [*held_out_task, *held_out_setting]
    leakage = _leakage_groups(evaluations)
    if leakage:
        return TransferAnalysis(
            status="unavailable",
            reason="leakage_group crosses development and held-out splits",
            splits=splits,
            leakage_groups=leakage,
        )
    if not development:
        return TransferAnalysis(
            status="unavailable",
            reason="development split is empty",
            splits=splits,
        )
    if not held_out:
        return TransferAnalysis(
            status="unavailable",
            reason="no held-out task or setting evaluations were recorded",
            splits=splits,
        )

    development_rate = _success_rate(development)
    return TransferAnalysis(
        status="available",
        splits=splits,
        held_out_task_success_delta=(
            _success_rate(held_out_task) - development_rate if held_out_task else None
        ),
        held_out_setting_success_delta=(
            _success_rate(held_out_setting) - development_rate if held_out_setting else None
        ),
        pooled_held_out_success_delta=_success_rate(held_out) - development_rate,
    )


def analyze_intervention_transfer(
    records: Sequence[StudyRunRecord],
    *,
    baseline_condition_id: str,
    intervention_condition_id: str,
) -> InterventionTransferAnalysis:
    """Compare conditions only within matching task/model/date/repetition cells."""
    if baseline_condition_id == intervention_condition_id:
        raise ValueError("baseline and intervention condition ids must differ")
    study_ids = sorted({record.study_id for record in records})
    effects = {
        split: _paired_condition_effect(
            [record for record in records if record.split == split],
            baseline_condition_id=baseline_condition_id,
            intervention_condition_id=intervention_condition_id,
        )
        for split in _SPLITS
    }
    if len(study_ids) != 1:
        return InterventionTransferAnalysis(
            status="unavailable",
            reason="records must belong to exactly one study",
            baseline_condition_id=baseline_condition_id,
            intervention_condition_id=intervention_condition_id,
            study_ids=study_ids,
            effects=effects,
        )
    development = effects["development"]
    held_out = [effects["held_out_task"], effects["held_out_setting"]]
    if development.status != "available":
        return InterventionTransferAnalysis(
            status="unavailable",
            reason="paired development condition records are unavailable",
            baseline_condition_id=baseline_condition_id,
            intervention_condition_id=intervention_condition_id,
            study_ids=study_ids,
            effects=effects,
        )
    if all(effect.status != "available" for effect in held_out):
        return InterventionTransferAnalysis(
            status="unavailable",
            reason="no paired held-out condition records are available",
            baseline_condition_id=baseline_condition_id,
            intervention_condition_id=intervention_condition_id,
            study_ids=study_ids,
            effects=effects,
        )
    assert development.success_delta is not None
    task_delta = effects["held_out_task"].success_delta
    setting_delta = effects["held_out_setting"].success_delta
    return InterventionTransferAnalysis(
        status="available",
        baseline_condition_id=baseline_condition_id,
        intervention_condition_id=intervention_condition_id,
        study_ids=study_ids,
        effects=effects,
        held_out_task_transfer_gap=(
            task_delta - development.success_delta if task_delta is not None else None
        ),
        held_out_setting_transfer_gap=(
            setting_delta - development.success_delta if setting_delta is not None else None
        ),
    )


def analyze_study_intervention_transfer(
    study_root: Path,
    *,
    baseline_condition_id: str,
    intervention_condition_id: str,
) -> InterventionTransferAnalysis:
    """Verify retained study evidence before calculating condition transfer."""
    from webagent.evaluation.studies import load_study_records

    return analyze_intervention_transfer(
        load_study_records(study_root),
        baseline_condition_id=baseline_condition_id,
        intervention_condition_id=intervention_condition_id,
    )


def _paired_condition_effect(
    records: Sequence[StudyRunRecord],
    *,
    baseline_condition_id: str,
    intervention_condition_id: str,
) -> PairedConditionEffect:
    baseline = [record for record in records if record.condition_id == baseline_condition_id]
    intervention = [
        record for record in records if record.condition_id == intervention_condition_id
    ]
    baseline_cells, baseline_duplicate = _condition_cells(baseline)
    intervention_cells, intervention_duplicate = _condition_cells(intervention)
    if baseline_duplicate or intervention_duplicate:
        return PairedConditionEffect(
            status="unavailable",
            reason="duplicate condition records exist for one pairing cell",
            baseline_count=len(baseline),
            intervention_count=len(intervention),
            paired_count=0,
        )
    paired = sorted(baseline_cells.keys() & intervention_cells.keys())
    if not paired:
        return PairedConditionEffect(
            status="unavailable",
            reason="no matching task/model/date/repetition cells",
            baseline_count=len(baseline),
            intervention_count=len(intervention),
            paired_count=0,
        )
    baseline_rate = sum(baseline_cells[key].success for key in paired) / len(paired)
    intervention_rate = sum(intervention_cells[key].success for key in paired) / len(paired)
    return PairedConditionEffect(
        status="available",
        baseline_count=len(baseline),
        intervention_count=len(intervention),
        paired_count=len(paired),
        baseline_success_rate=baseline_rate,
        intervention_success_rate=intervention_rate,
        success_delta=intervention_rate - baseline_rate,
    )


def _condition_cells(
    records: Sequence[StudyRunRecord],
) -> tuple[dict[tuple[object, ...], StudyRunRecord], bool]:
    cells: dict[tuple[object, ...], StudyRunRecord] = {}
    duplicate = False
    for record in records:
        key = (
            record.study_id,
            record.task_id,
            record.setting_id,
            record.provider,
            record.model,
            record.collection_date,
            record.repetition,
        )
        duplicate |= key in cells
        cells[key] = record
    return cells, duplicate


def _split_performance(values: Sequence[TaskEvaluation], name: str) -> SplitPerformance:
    if not values:
        return SplitPerformance(
            status="unavailable",
            reason=f"{name} split is empty",
            task_count=0,
        )
    return SplitPerformance(
        status="available",
        task_count=len(values),
        success_rate=_success_rate(values),
        mean_score=sum(item.score for item in values) / len(values),
    )


def _success_rate(values: Sequence[TaskEvaluation]) -> float:
    return sum(item.passed for item in values) / len(values)


def _leakage_groups(evaluations: Sequence[TaskEvaluation]) -> list[str]:
    groups: defaultdict[str, set[str]] = defaultdict(set)
    for evaluation in evaluations:
        groups[str(evaluation.leakage_group)].add(evaluation.split)
    return sorted(
        group
        for group, splits in groups.items()
        if "development" in splits
        and bool({"held_out_task", "held_out_setting"}.intersection(splits))
    )


__all__ = [
    "InterventionTransferAnalysis",
    "PairedConditionEffect",
    "SplitPerformance",
    "TransferAnalysis",
    "analyze_intervention_transfer",
    "analyze_study_intervention_transfer",
    "analyze_transfer",
]

"""Cross-suite empirical evidence portfolio for bounded generality claims."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from webagent.evaluation.generality import GeneralityAnalysis, analyze_generality
from webagent.evaluation.long_horizon import LongHorizonAnalysis, analyze_long_horizon
from webagent.evaluation.models import BenchmarkReport, TaskEvaluation


class PortfolioInput(BaseModel):
    """One locally retained benchmark report and its content identity."""

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    suite: str
    date: str
    provider: str
    model: str
    task_count: int = Field(ge=0)


class PortfolioCell(BaseModel):
    """Coverage and outcomes for one provider/model/date cell."""

    provider: str
    model: str
    date: str
    report_count: int = Field(ge=0)
    suite_count: int = Field(ge=0)
    task_count: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    generality: GeneralityAnalysis
    long_horizon: LongHorizonAnalysis
    ready: bool
    reasons: list[str]


class EmpiricalPortfolio(BaseModel):
    """Fail-closed evidence status for a multi-model, multi-date agent portfolio."""

    schema_version: int = 1
    status: Literal["ready", "insufficient"]
    input_reports: list[PortfolioInput]
    endpoint_count: int = Field(ge=0)
    distinct_dates: list[str]
    common_complete_dates: list[str]
    cells: list[PortfolioCell]
    overall_success_rate: float = Field(ge=0.0, le=1.0)
    scenario_success_rate: dict[str, float]
    missing_requirements: list[str]
    evidence_notice: str = (
        "Inputs are schema-validated and content-hashed local reports. Local timestamps are not "
        "independent wall-clock attestation, and coverage readiness is not a performance claim."
    )


def load_empirical_portfolio(
    paths: Sequence[Path],
    *,
    minimum_models: int = 2,
    minimum_dates: int = 3,
) -> EmpiricalPortfolio:
    """Load retained reports and require complete generality/long-horizon cells."""
    if minimum_models < 2 or minimum_models > 3:
        raise ValueError("minimum_models must be 2 or 3")
    if minimum_dates < 3:
        raise ValueError("minimum_dates must be at least 3")
    runs: list[tuple[PortfolioInput, list[TaskEvaluation]]] = []
    seen_run_ids: set[str] = set()
    for source in paths:
        resolved = source.expanduser().resolve()
        raw = resolved.read_bytes()
        report = BenchmarkReport.model_validate_json(raw)
        if report.summary.task_count != len(report.tasks):
            raise ValueError(f"{resolved}: summary task_count differs from tasks")
        metadata = report.metadata
        run_id = _known(metadata.get("run_id"), "run_id", resolved)
        if run_id in seen_run_ids:
            raise ValueError(f"duplicate run_id in empirical portfolio: {run_id}")
        seen_run_ids.add(run_id)
        provider = _known(metadata.get("provider"), "provider", resolved)
        model = _known(metadata.get("model"), "model", resolved)
        mode = str(metadata.get("mode", ""))
        if "scripted" in mode or model == "scripted-harness-baseline":
            raise ValueError(f"{resolved}: scripted harness results are not agent evidence")
        timestamp = datetime.fromisoformat(report.created_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError(f"{resolved}: created_at must include a timezone")
        day = timestamp.astimezone(UTC).date().isoformat()
        evidence = PortfolioInput(
            path=str(resolved),
            sha256=hashlib.sha256(raw).hexdigest(),
            run_id=run_id,
            suite=report.suite,
            date=day,
            provider=provider,
            model=model,
            task_count=len(report.tasks),
        )
        runs.append((evidence, report.tasks))
    return analyze_empirical_portfolio(
        runs,
        minimum_models=minimum_models,
        minimum_dates=minimum_dates,
    )


def analyze_empirical_portfolio(
    runs: Sequence[tuple[PortfolioInput, Sequence[TaskEvaluation]]],
    *,
    minimum_models: int = 2,
    minimum_dates: int = 3,
) -> EmpiricalPortfolio:
    """Analyze already validated report/task bindings."""
    by_cell: defaultdict[
        tuple[str, str, str], list[tuple[PortfolioInput, Sequence[TaskEvaluation]]]
    ] = defaultdict(list)
    for evidence, tasks in runs:
        by_cell[(evidence.provider, evidence.model, evidence.date)].append((evidence, tasks))
    endpoints = sorted({(item.provider, item.model) for item, _tasks in runs})
    cells: list[PortfolioCell] = []
    complete_dates: dict[tuple[str, str], set[str]] = defaultdict(set)
    all_tasks: list[TaskEvaluation] = []
    for (provider, model, day), cell_runs in sorted(by_cell.items()):
        tasks = _unique_tasks([task for _evidence, values in cell_runs for task in values])
        all_tasks.extend(tasks)
        generality = analyze_generality(tasks)
        long_horizon = analyze_long_horizon(tasks)
        reasons: list[str] = []
        if generality.status != "ready":
            reasons.extend(generality.missing_requirements)
        if long_horizon.status != "available":
            reasons.append(long_horizon.reason or "long-horizon evidence is unavailable")
        suites = {evidence.suite for evidence, _values in cell_runs}
        if len(suites) < 3:
            reasons.append("requires at least 3 complementary suites per model/date cell")
        ready = not reasons
        if ready:
            complete_dates[(provider, model)].add(day)
        cells.append(
            PortfolioCell(
                provider=provider,
                model=model,
                date=day,
                report_count=len(cell_runs),
                suite_count=len(suites),
                task_count=len(tasks),
                success_rate=_success_rate(tasks),
                generality=generality,
                long_horizon=long_horizon,
                ready=ready,
                reasons=reasons,
            )
        )
    missing: list[str] = []
    if len(endpoints) < minimum_models:
        missing.append(f"requires at least {minimum_models} provider/model endpoints")
    if len(endpoints) > 3:
        missing.append("allows at most 3 provider/model endpoints")
    common_dates = (
        sorted(set.intersection(*(complete_dates[endpoint] for endpoint in endpoints)))
        if endpoints and all(endpoint in complete_dates for endpoint in endpoints)
        else []
    )
    if len(common_dates) < minimum_dates:
        missing.append(
            f"requires {minimum_dates} common dates with complete generality and long-horizon cells"
        )
    incomplete = [f"{cell.provider}::{cell.model}@{cell.date}" for cell in cells if not cell.ready]
    if incomplete:
        missing.append("incomplete cells: " + ", ".join(incomplete))
    scenario_groups: defaultdict[str, list[TaskEvaluation]] = defaultdict(list)
    for item in all_tasks:
        scenario_groups[item.scenario].append(item)
    return EmpiricalPortfolio(
        status="insufficient" if missing else "ready",
        input_reports=[item for item, _tasks in runs],
        endpoint_count=len(endpoints),
        distinct_dates=sorted({item.date for item, _tasks in runs}),
        common_complete_dates=common_dates,
        cells=cells,
        overall_success_rate=_success_rate(all_tasks),
        scenario_success_rate={
            scenario: _success_rate(values) for scenario, values in sorted(scenario_groups.items())
        },
        missing_requirements=missing,
    )


def write_empirical_portfolio(report: EmpiricalPortfolio, path: Path) -> None:
    """Atomically publish one portfolio report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def _unique_tasks(values: Sequence[TaskEvaluation]) -> list[TaskEvaluation]:
    unique: dict[tuple[str, str, str], TaskEvaluation] = {}
    for item in values:
        key = (item.task_id, str(item.setting_id), item.scenario)
        if key in unique:
            raise ValueError(f"duplicate task evaluation within one model/date cell: {key}")
        unique[key] = item
    return list(unique.values())


def _known(value: object, field: str, path: Path) -> str:
    text = str(value or "").strip()
    if not text or text == "unknown":
        raise ValueError(f"{path}: empirical portfolio requires known metadata.{field}")
    return text


def _success_rate(values: Sequence[TaskEvaluation]) -> float:
    return sum(item.passed for item in values) / len(values) if values else 0.0


__all__ = [
    "EmpiricalPortfolio",
    "PortfolioCell",
    "PortfolioInput",
    "analyze_empirical_portfolio",
    "load_empirical_portfolio",
    "write_empirical_portfolio",
]

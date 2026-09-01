"""Evidence-preserving synthesis of diagnostic and external benchmark layers."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from webagent.evaluation.external import ExternalBenchmarkReport
from webagent.evaluation.portfolio import EmpiricalPortfolio


class LayerEvidence(BaseModel):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LayeredModelResult(BaseModel):
    provider: str
    model: str
    diagnostic_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    external_success_rate: dict[str, float | None]
    ready: bool
    reasons: list[str]


class TwoLayerPortfolio(BaseModel):
    """Readiness across layers, intentionally without an invalid pooled score."""

    schema_version: Literal[1] = 1
    status: Literal["ready", "insufficient"]
    diagnostic: LayerEvidence
    external: list[LayerEvidence]
    models: list[LayeredModelResult]
    missing_requirements: list[str]
    interpretation_notice: str = (
        "Diagnostic and external scores answer different questions and are never averaged. "
        "Readiness requires complete evidence in both layers for every compared endpoint."
    )


def load_two_layer_portfolio(
    diagnostic_path: Path,
    external_paths: Sequence[Path],
) -> TwoLayerPortfolio:
    """Load, hash, and cross-check one internal and two external suites per model."""
    diagnostic_raw = diagnostic_path.expanduser().resolve().read_bytes()
    diagnostic = EmpiricalPortfolio.model_validate_json(diagnostic_raw)
    external_reports: list[tuple[LayerEvidence, ExternalBenchmarkReport]] = []
    for path in external_paths:
        resolved = path.expanduser().resolve()
        raw = resolved.read_bytes()
        external_reports.append(
            (
                LayerEvidence(path=str(resolved), sha256=hashlib.sha256(raw).hexdigest()),
                ExternalBenchmarkReport.model_validate_json(raw),
            )
        )
    return analyze_two_layer_portfolio(
        diagnostic,
        diagnostic_evidence=LayerEvidence(
            path=str(diagnostic_path.expanduser().resolve()),
            sha256=hashlib.sha256(diagnostic_raw).hexdigest(),
        ),
        external_reports=external_reports,
    )


def analyze_two_layer_portfolio(
    diagnostic: EmpiricalPortfolio,
    *,
    diagnostic_evidence: LayerEvidence,
    external_reports: Sequence[tuple[LayerEvidence, ExternalBenchmarkReport]],
) -> TwoLayerPortfolio:
    """Require complete WebArena-Verified Hard and VisualWebArena evidence."""
    endpoints = sorted(
        {
            (cell.provider, cell.model)
            for cell in diagnostic.cells
            if cell.endpoint_status == "available"
        }
    )
    grouped: defaultdict[tuple[str, str], dict[str, ExternalBenchmarkReport]] = defaultdict(dict)
    duplicate: list[str] = []
    for _evidence, report in external_reports:
        key = (report.provider, report.model)
        if report.benchmark in grouped[key]:
            duplicate.append(f"{report.provider}::{report.model}:{report.benchmark}")
        grouped[key][report.benchmark] = report

    global_missing: list[str] = []
    if diagnostic.status != "ready":
        global_missing.append("diagnostic portfolio is not ready")
    if duplicate:
        global_missing.append("duplicate external reports: " + ", ".join(sorted(duplicate)))
    unexpected = sorted(set(grouped) - set(endpoints))
    if unexpected:
        global_missing.append(
            "external reports contain endpoints absent from diagnostic evidence: "
            + ", ".join(f"{provider}::{model}" for provider, model in unexpected)
        )
    for benchmark in ("webarena_verified", "visualwebarena"):
        comparable = [
            report
            for _evidence, report in external_reports
            if report.benchmark == benchmark and (report.provider, report.model) in endpoints
        ]
        if len({report.backend_configuration_sha256 for report in comparable}) > 1:
            global_missing.append(f"{benchmark} reports use different backend configurations")
        package_sets = {tuple(sorted(report.package_versions.items())) for report in comparable}
        if len(package_sets) > 1:
            global_missing.append(f"{benchmark} reports use different package versions")

    model_results: list[LayeredModelResult] = []
    required = {"webarena_verified": "hard", "visualwebarena": "full"}
    for provider, model in endpoints:
        reports = grouped[(provider, model)]
        reasons: list[str] = []
        rates: dict[str, float | None] = {}
        for benchmark, profile in required.items():
            candidate = reports.get(benchmark)
            if candidate is None:
                reasons.append(f"missing {benchmark}:{profile} report")
                rates[benchmark] = None
                continue
            rates[benchmark] = candidate.summary.success_rate
            if candidate.profile != profile or candidate.protocol_status != "official":
                reasons.append(f"{benchmark} is not a complete official {profile} run")
            if candidate.summary.scored_tasks != candidate.summary.expected_tasks:
                reasons.append(f"{benchmark} contains unscored system-error tasks")
            if diagnostic.agent_source_sha256s != [candidate.agent_source_sha256]:
                reasons.append(f"{benchmark} agent source differs from diagnostic layer")
            if diagnostic.benchmark_source_sha256s != [candidate.adapter_source_sha256]:
                reasons.append(f"{benchmark} adapter source differs from diagnostic layer")
        diagnostic_cells = [
            cell
            for cell in diagnostic.cells
            if (cell.provider, cell.model) == (provider, model)
            and cell.endpoint_status == "available"
            and cell.success_rate is not None
        ]
        diagnostic_rates = [
            rate for cell in diagnostic_cells if (rate := cell.success_rate) is not None
        ]
        diagnostic_rate = (
            sum(diagnostic_rates) / len(diagnostic_rates) if diagnostic_rates else None
        )
        model_results.append(
            LayeredModelResult(
                provider=provider,
                model=model,
                diagnostic_success_rate=diagnostic_rate,
                external_success_rate=rates,
                ready=not reasons,
                reasons=reasons,
            )
        )
    if not endpoints:
        global_missing.append("diagnostic portfolio has no available endpoints")
    incomplete = [f"{item.provider}::{item.model}" for item in model_results if not item.ready]
    if incomplete:
        global_missing.append("incomplete two-layer endpoints: " + ", ".join(incomplete))
    return TwoLayerPortfolio(
        status="insufficient" if global_missing else "ready",
        diagnostic=diagnostic_evidence,
        external=[evidence for evidence, _report in external_reports],
        models=model_results,
        missing_requirements=global_missing,
    )


__all__ = [
    "LayerEvidence",
    "LayeredModelResult",
    "TwoLayerPortfolio",
    "analyze_two_layer_portfolio",
    "load_two_layer_portfolio",
]

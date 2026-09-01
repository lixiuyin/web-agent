from types import SimpleNamespace

from webagent.evaluation.external import ExternalTaskResult, new_external_report
from webagent.evaluation.layers import LayerEvidence, analyze_two_layer_portfolio
from webagent.evaluation.portfolio import EmpiricalPortfolio


def _diagnostic() -> EmpiricalPortfolio:
    return EmpiricalPortfolio.model_construct(
        status="ready",
        cells=[
            SimpleNamespace(
                provider="openrouter",
                model="model-a",
                endpoint_status="available",
                success_rate=0.75,
            )
        ],
        agent_source_sha256s=["a" * 64],
        benchmark_source_sha256s=["b" * 64],
    )


def _external(benchmark: str, profile: str):
    task = ExternalTaskResult(
        task_name=(
            "webarena_verified.1.1.1" if benchmark == "webarena_verified" else "visualwebarena.1"
        ),
        task_id=1,
        task_seed=28,
        reward=1.0,
        success=True,
        steps=2,
        terminated=True,
        truncated=False,
        evidence_path="runs/1",
    )
    return new_external_report(
        benchmark=benchmark,
        profile=profile,
        official_protocol=True,
        provider="openrouter",
        model="model-a",
        max_steps=30,
        headless=True,
        task_set_sha256="c" * 64,
        backend_configuration_sha256="d" * 64,
        agent_source_sha256="a" * 64,
        adapter_source_sha256="b" * 64,
        package_versions={},
        expected_tasks=1,
        tasks=[task],
    )


def test_two_layer_portfolio_keeps_scores_separate_and_requires_both_suites() -> None:
    evidence = LayerEvidence(path="report.json", sha256="d" * 64)
    report = analyze_two_layer_portfolio(
        _diagnostic(),
        diagnostic_evidence=evidence,
        external_reports=[
            (evidence, _external("webarena_verified", "hard")),
            (evidence, _external("visualwebarena", "full")),
        ],
    )

    assert report.status == "ready"
    assert report.models[0].diagnostic_success_rate == 0.75
    assert report.models[0].external_success_rate == {
        "webarena_verified": 1.0,
        "visualwebarena": 1.0,
    }
    assert not hasattr(report.models[0], "overall_score")


def test_two_layer_portfolio_fails_closed_when_visual_layer_is_missing() -> None:
    evidence = LayerEvidence(path="report.json", sha256="d" * 64)
    report = analyze_two_layer_portfolio(
        _diagnostic(),
        diagnostic_evidence=evidence,
        external_reports=[(evidence, _external("webarena_verified", "hard"))],
    )

    assert report.status == "insufficient"
    assert "missing visualwebarena:full report" in report.models[0].reasons

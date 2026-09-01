import pytest
from benchmarks.studies.browsergym_matrix import (
    _aggregate_matrix,
    _mcnemar_exact,
    _paired_comparison,
)

from webagent.evaluation.external import ExternalTaskResult, new_external_report


def _report(
    model: str,
    outcomes: list[bool],
    *,
    benchmark: str = "visualwebarena",
    backend_digest: str = "d" * 64,
):
    tasks = [
        ExternalTaskResult(
            task_name=(
                f"visualwebarena.{index}"
                if benchmark == "visualwebarena"
                else f"webarena_verified.1.{index}.2"
            ),
            task_id=index,
            task_seed=28 + index,
            reward=float(success),
            success=success,
            steps=3,
            terminated=True,
            truncated=False,
            evidence_path=f"runs/{index}",
        )
        for index, success in enumerate(outcomes)
    ]
    return new_external_report(
        benchmark=benchmark,
        profile="full" if benchmark == "visualwebarena" else "hard",
        official_protocol=True,
        provider="openrouter",
        model=model,
        max_steps=30,
        headless=True,
        task_set_sha256="a" * 64,
        backend_configuration_sha256=backend_digest,
        agent_source_sha256="b" * 64,
        adapter_source_sha256="c" * 64,
        package_versions={},
        expected_tasks=len(tasks),
        tasks=tasks,
    )


def test_paired_comparison_uses_task_level_disagreements() -> None:
    comparison = _paired_comparison(
        _report("left", [True, True, False, False]),
        _report("right", [True, False, True, False]),
    )

    assert comparison["paired_tasks"] == 4
    assert comparison["left_only_successes"] == 1
    assert comparison["right_only_successes"] == 1
    assert comparison["success_rate_delta"] == 0.0
    assert comparison["mcnemar_exact_p_value"] == 1.0


def test_exact_mcnemar_handles_one_sided_disagreement() -> None:
    assert _mcnemar_exact(4, 0) == 0.125
    assert _mcnemar_exact(0, 0) is None


def test_matrix_rejects_different_backend_instances() -> None:
    reports = [
        _report(model, [True], benchmark=benchmark, backend_digest=backend)
        for model, backend in (("left", "d" * 64), ("right", "e" * 64))
        for benchmark in ("webarena_verified", "visualwebarena")
    ]

    with pytest.raises(ValueError, match="different backend configurations"):
        _aggregate_matrix("openrouter", ["left", "right"], reports)

from webagent.evaluation.external import ExternalTaskResult, new_external_report


def _task(task_id: int, *, success: bool, error: str | None = None) -> ExternalTaskResult:
    return ExternalTaskResult(
        task_name=f"visualwebarena.{task_id}",
        task_id=task_id,
        task_seed=28,
        reward=1.0 if success else 0.0,
        success=success,
        steps=4,
        terminated=True,
        truncated=False,
        error=error,
        evidence_path=f"runs/{task_id}",
    )


def test_external_report_is_official_only_when_complete() -> None:
    report = new_external_report(
        benchmark="visualwebarena",
        profile="full",
        official_protocol=True,
        provider="openrouter",
        model="model-a",
        max_steps=30,
        headless=True,
        evaluator_device="cuda",
        task_set_sha256="a" * 64,
        backend_configuration_sha256="d" * 64,
        agent_source_sha256="b" * 64,
        adapter_source_sha256="c" * 64,
        package_versions={"browsergym-core": "0.14.3"},
        expected_tasks=2,
        tasks=[_task(1, success=True), _task(2, success=False)],
    )

    assert report.protocol_status == "official"
    assert report.evaluator_device == "cuda"
    assert report.summary.success_rate == 0.5
    assert report.summary.mean_reward == 0.5
    assert report.summary.success_rate_ci95 is not None
    low, high = report.summary.success_rate_ci95
    assert 0.0 < low < 0.5 < high < 1.0


def test_external_report_exposes_system_errors_and_incomplete_coverage() -> None:
    report = new_external_report(
        benchmark="visualwebarena",
        profile="full",
        official_protocol=True,
        provider="openrouter",
        model="model-a",
        max_steps=30,
        headless=True,
        task_set_sha256="a" * 64,
        backend_configuration_sha256="d" * 64,
        agent_source_sha256="b" * 64,
        adapter_source_sha256="c" * 64,
        package_versions={},
        expected_tasks=3,
        tasks=[_task(1, success=False, error="environment failed")],
    )

    assert report.protocol_status == "incomplete"
    assert report.summary.coverage == 1 / 3
    assert report.summary.scored_tasks == 0
    assert report.summary.system_error_tasks == 1
    assert report.summary.success_rate is None


def test_system_error_prevents_official_protocol_status_at_full_coverage() -> None:
    report = new_external_report(
        benchmark="webarena_verified",
        profile="hard",
        official_protocol=True,
        provider="openrouter",
        model="model-a",
        max_steps=30,
        headless=True,
        task_set_sha256="a" * 64,
        backend_configuration_sha256="d" * 64,
        agent_source_sha256="b" * 64,
        adapter_source_sha256="c" * 64,
        package_versions={},
        expected_tasks=1,
        tasks=[_task(1, success=False, error="environment failed")],
    )

    assert report.summary.coverage == 1.0
    assert report.summary.scored_tasks == 0
    assert report.protocol_status == "incomplete"

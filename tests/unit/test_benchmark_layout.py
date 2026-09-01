"""Tests for research-oriented benchmark module and output organization."""

from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from benchmarks import open_web as legacy_open_web
from benchmarks import sandbox_interaction as legacy_sandbox
from benchmarks import web_interaction as legacy_general
from benchmarks.core import (
    allocate_execution_dir,
    default_campaign_dir,
    default_study_dir,
    execution_model_label,
    packaged_manifest_path,
    task_run_dir,
)
from benchmarks.studies import open_web_matrix
from benchmarks.suites.controlled_web import general, sandbox
from benchmarks.suites.open_web import parallel, runner

from webagent.core.config import AgentConfig


def test_default_study_and_task_run_paths_are_purpose_scoped() -> None:
    study = default_study_dir("open-web-general-v2")

    assert study == Path("outputs/studies/open-web-general-v2")
    assert task_run_dir(study, "discover-source") == study / "runs" / "discover-source"
    assert default_campaign_dir("Generality Campaign V2") == Path(
        "outputs/campaigns/generality-campaign-v2"
    )


@pytest.mark.parametrize("value", ("", ".", "..", "nested/task", "nested\\task", "..\\task"))
def test_task_run_path_rejects_ambiguous_components(value: str) -> None:
    with pytest.raises(ValueError):
        task_run_dir(Path("study"), value)


def test_execution_path_is_unique_and_research_scoped() -> None:
    study = default_study_dir("open-web-general-v2")
    execution = allocate_execution_dir(
        study,
        model="provider/model-a",
        condition="loop-detection-ablation",
        now=datetime(2026, 8, 30, 1, 2, 3, 456, tzinfo=UTC),
        execution_id="rep-1",
    )

    assert execution.relative_to(study.resolve()).parts == (
        "executions",
        "2026-08-30",
        "provider-model-a",
        "loop-detection-ablation",
        "010203-000456-rep-1",
    )


def test_packaged_manifest_defaults_do_not_depend_on_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    direct = runner.parse_args([]).manifest
    sharded = parallel.parse_args(["--model", "provider/model-a"]).manifest
    matrix = open_web_matrix.parse_args(
        ["--models", "model-a", "model-b", "--provider", "provider-a"]
    ).manifest

    assert direct == packaged_manifest_path("open_web_general.json")
    assert direct.is_file()
    assert sharded == direct
    assert matrix == direct


def test_harness_baseline_is_the_default_mode() -> None:
    assert general.parse_args([]).mode == "scripted-harness-baseline"
    assert sandbox.parse_args([]).mode == "scripted-harness-baseline"
    assert general.parse_args([]).output is None
    assert sandbox.parse_args([]).output is None


def test_sandbox_transport_evidence_records_nonsecret_endpoint_condition() -> None:
    cfg = AgentConfig(
        _env_file=None,
        endpoint_access_mode="byok",
        api_transient_retries=3,
        api_retry_base_seconds=10,
        api_retry_max_seconds=60,
        max_steps=20,
        task_timeout=600,
        browser_timeout=5000,
    )

    assert sandbox._transport_evidence(cfg) == {
        "declared_endpoint_access_mode": "byok",
        "api_transient_retries": 3,
        "api_retry_base_seconds": 10.0,
        "api_retry_max_seconds": 60.0,
        "max_steps_per_task": 20,
        "task_timeout_seconds": 600,
        "browser_timeout_ms": 5000,
    }


def test_sandbox_performance_defaults_are_recordable_and_model_independent() -> None:
    args = sandbox.parse_args([])

    assert args.max_steps_per_task == 20
    assert args.task_timeout_seconds == 600
    assert args.browser_timeout_ms == 5000


def test_execution_model_label_never_uses_a_placeholder_for_agent_mode() -> None:
    assert (
        execution_model_label(mode="agent", configured_model="provider/model-a")
        == "provider/model-a"
    )
    assert (
        execution_model_label(
            mode="scripted-harness-baseline",
            configured_model="provider/model-a",
        )
        == "scripted-harness-baseline"
    )

    with pytest.raises(ValueError):
        execution_model_label(mode="agent", configured_model="")


@pytest.mark.asyncio
@pytest.mark.parametrize("suite", (general, sandbox))
async def test_controlled_agent_default_output_uses_configured_model(
    suite: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def allocate(_study: Path, *, model: str, condition: str) -> Path:
        captured.update(model=model, condition=condition)
        return tmp_path / suite.__name__.replace(".", "-")

    def stop_before_browser(_config: object) -> None:
        raise RuntimeError("planner stop")

    monkeypatch.setenv("AGENT_MODEL_NAME", "provider/configured-default")
    monkeypatch.setattr(suite, "allocate_execution_dir", allocate)
    monkeypatch.setattr(suite, "_build_planner", stop_before_browser)

    with pytest.raises(RuntimeError, match="planner stop"):
        await suite.run_benchmark(suite.parse_args(["--mode", "agent"]))

    assert captured == {"model": "provider/configured-default", "condition": "agent"}


def test_flat_modules_remain_thin_import_compatible_wrappers() -> None:
    assert legacy_open_web.run_benchmark is runner.run_benchmark
    assert legacy_general.run_benchmark is general.run_benchmark
    assert legacy_sandbox.run_benchmark is sandbox.run_benchmark

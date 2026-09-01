"""Tests for exact parallel-shard report merging."""

import hashlib
import json
from pathlib import Path

import pytest
from benchmarks.suites.open_web.parallel import (
    bind_study_identity,
    merge_shard_reports,
    publish_shard_task_runs,
)

from webagent.evaluation import (
    AssertionOutcome,
    BenchmarkAssertion,
    StudyExecutionLayout,
    StudyLayout,
    StudyRunContext,
    TaskEvaluation,
)


def _task(task_id: str, passed: bool) -> dict[str, object]:
    assertion = BenchmarkAssertion(kind="answer_contains", expected="fact")
    return TaskEvaluation(
        task_id=task_id,
        category="docs",
        goal="read",
        passed=passed,
        score=float(passed),
        agent_reported_success=passed,
        agent_status="completed" if passed else "max_steps_reached",
        duration_seconds=1,
        steps=1,
        action_count=0,
        failed_action_count=0,
        planner_attempt_count=1,
        planner_failure_count=0,
        planner_tokens=10,
        answer_assertion_count=1,
        answer_assertion_passed=int(passed),
        assertions=[AssertionOutcome(assertion=assertion, passed=passed)],
    ).model_dump(mode="json")


def _report(*tasks: dict[str, object]) -> dict[str, object]:
    return {
        "suite": "open",
        "metadata": {
            "manifest_sha256": "a" * 64,
            "benchmark_config": {
                "manifest_sha256": "a" * 64,
                "provider": "unknown",
                "study_manifest_sha256": "unknown",
            },
            "model": "model-a",
            "stealth_mode": False,
            "agent_source_sha256": "b" * 64,
        },
        "tasks": list(tasks),
    }


def test_merge_shards_checks_exact_coverage_and_recomputes_summary() -> None:
    merged = merge_shard_reports(
        [_report(_task("a", True)), _report(_task("b", False))],
        expected_task_ids={"a", "b"},
    )

    assert merged.summary.task_count == 2
    assert merged.summary.success_rate == 0.5
    assert merged.metadata["parallel_shards"] == 2
    assert merged.metadata["canonical_task_run_paths"] == {"a": "runs/a", "b": "runs/b"}
    assert {task.task_id for task in merged.tasks} == {"a", "b"}


def test_merge_shards_rejects_duplicate_or_missing_tasks() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        merge_shard_reports(
            [_report(_task("a", True)), _report(_task("a", True))],
            expected_task_ids={"a"},
        )
    with pytest.raises(ValueError, match="coverage mismatch"):
        merge_shard_reports([_report(_task("a", True))], expected_task_ids={"a", "b"})


def test_merge_shards_rejects_provenance_mismatch() -> None:
    other = _report(_task("b", True))
    other["metadata"]["stealth_mode"] = True  # type: ignore[index]

    with pytest.raises(ValueError, match="provenance"):
        merge_shard_reports(
            [_report(_task("a", True)), other],
            expected_task_ids={"a", "b"},
        )


def test_merge_shards_rejects_source_fingerprint_mismatch() -> None:
    other = _report(_task("b", True))
    other["metadata"]["agent_source_sha256"] = "c" * 64  # type: ignore[index]

    with pytest.raises(ValueError, match="provenance"):
        merge_shard_reports(
            [_report(_task("a", True)), other],
            expected_task_ids={"a", "b"},
        )


def test_merged_report_binds_provider_and_retained_study_manifest(tmp_path: Path) -> None:
    study = StudyLayout.from_root(tmp_path / "study")
    study.prepare()
    study.manifest_path.write_text('{"study_id":"study"}\n', encoding="utf-8")
    execution = StudyExecutionLayout.from_root(study.executions_dir / "execution")
    execution.prepare()
    merged = merge_shard_reports(
        [_report(_task("a", True))],
        expected_task_ids={"a"},
    )

    bound = bind_study_identity(
        merged,
        context=StudyRunContext(
            study_root=study.root,
            study_id="study",
            provider="openrouter",
            model="model-a",
            condition_id="browser-grounded",
            repetition=1,
            task_manifest_sha256="a" * 64,
            task_set_sha256="b" * 64,
        ),
        output_dir=execution.root,
    )

    retained = execution.root / str(bound.metadata["study_manifest"])
    digest = hashlib.sha256(study.manifest_path.read_bytes()).hexdigest()
    assert retained.is_file()
    assert retained.is_relative_to(execution.inputs_dir)
    assert bound.metadata["provider"] == "openrouter"
    assert bound.metadata["study_manifest_sha256"] == digest
    assert bound.metadata["benchmark_config"]["provider"] == "openrouter"
    assert bound.metadata["benchmark_config"]["study_manifest_sha256"] == digest


def test_publish_shard_runs_copies_complete_evidence_and_refuses_conflicts(
    tmp_path: Path,
) -> None:
    layout = StudyExecutionLayout.from_root(tmp_path / "execution")
    layout.prepare()
    reports = [_report(_task("a", True)), _report(_task("b", False))]
    for index, report in enumerate(reports):
        task = report["tasks"][0]
        task_id = task["task_id"]
        source = layout.shards_dir / f"shard-{index:02d}" / "runs" / task_id
        evaluation = source / "evaluation" / "task.json"
        evaluation.parent.mkdir(parents=True)
        evaluation.write_text(json.dumps(task), encoding="utf-8")
        (source / "trajectory").mkdir()
        (source / "trajectory" / "trace.json").write_text("{}", encoding="utf-8")

    publish_shard_task_runs(layout, reports)

    assert (layout.task_run("a").trace_path).is_file()
    assert (layout.task_run("b").evaluation_dir / "task.json").is_file()
    with pytest.raises(FileExistsError, match="canonical task run already exists"):
        publish_shard_task_runs(layout, reports)

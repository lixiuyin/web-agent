"""Tests for immutable study contracts and run ledgers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks.core import initialize_matrix_study, study_context_from_args
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from webagent.evaluation import (
    AssertionOutcome,
    BenchmarkAssertion,
    BenchmarkRunner,
    BenchmarkTask,
    StudyLayout,
    TaskEvaluation,
)
from webagent.evaluation import studies as studies_module
from webagent.evaluation.studies import (
    STUDY_MANIFEST_SCHEMA_ID,
    STUDY_MANIFEST_SCHEMA_VERSION,
    STUDY_RUN_RECORD_SCHEMA_ID,
    STUDY_RUN_RECORD_SCHEMA_VERSION,
    StudyBudgets,
    StudyCondition,
    StudyManifest,
    StudyModel,
    StudyRunContext,
    StudyRunRecord,
    append_study_record,
    load_study_records,
    publish_study_run_records,
    validate_study_task_set,
    write_study_manifest,
)
from webagent.evaluation.task_binding import task_set_sha256

_DIGEST = "a" * 64


def _manifest() -> StudyManifest:
    return StudyManifest(
        study_id="failure-transfer-v1",
        title="Failure recurrence and held-out transfer",
        research_questions=("Do failure reductions transfer?",),
        suite="open-web-general-v3",
        task_manifest_sha256=_DIGEST,
        task_split_counts={"development": 18, "held_out_task": 6, "held_out_setting": 6},
        models=(
            StudyModel(provider="openrouter", model="z-ai/glm-5.3-flash"),
            StudyModel(provider="openrouter", model="qwen/qwen3.8-flash"),
        ),
        conditions=(
            StudyCondition(id="baseline", kind="baseline", description="Current controller"),
            StudyCondition(
                id="memory-change",
                kind="intervention",
                description="Targeted memory mechanism",
            ),
        ),
        collection_dates=(date(2026, 8, 30),),
        repetitions=3,
        budgets=StudyBudgets(
            max_steps=30,
            task_timeout_seconds=1200,
            tool_timeout_seconds=300,
            planner_max_tokens=6000,
        ),
        primary_metrics=("success_rate", "held_out_transfer"),
        confidence_target="task_success",
        source_sha256=_DIGEST,
    )


def _record() -> StudyRunRecord:
    return StudyRunRecord(
        study_id="failure-transfer-v1",
        task_id="heldout-task",
        split="held_out_task",
        setting_id="site-v2",
        provider="openrouter",
        model="qwen/qwen3.8-flash",
        condition_id="memory-change",
        failure_taxonomy_version="1",
        collection_date=date(2026, 8, 30),
        repetition=1,
        run_path=(
            "executions/2026-08-30/qwen-qwen3-8-flash/memory-change/execution-1/runs/heldout-task"
        ),
        report_path=(
            "executions/2026-08-30/qwen-qwen3-8-flash/memory-change/"
            "execution-1/runs/heldout-task/evaluation/task.json"
        ),
        report_sha256=_DIGEST,
        success=True,
        success_probability=0.8,
    )


def _packaged_schema(filename: str) -> dict[str, object]:
    return json.loads(
        resources.files("webagent.schemas").joinpath(filename).read_text(encoding="utf-8")
    )


def test_study_manifest_is_immutable_and_rejects_duplicate_comparators(tmp_path: Path) -> None:
    manifest = _manifest()
    path = tmp_path / "study.json"

    assert write_study_manifest(path, manifest) == path.resolve()
    assert write_study_manifest(path, manifest) == path.resolve()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["$schema"] == STUDY_MANIFEST_SCHEMA_ID
    assert stored["schema_version"] == STUDY_MANIFEST_SCHEMA_VERSION
    with pytest.raises(FileExistsError, match="different bytes"):
        write_study_manifest(path, manifest.model_copy(update={"title": "Changed later"}))

    duplicate_payload = manifest.model_dump(mode="python")
    duplicate_payload["models"] = [manifest.models[0], manifest.models[0]]
    with pytest.raises(ValidationError, match="models must be unique"):
        StudyManifest.model_validate(duplicate_payload)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StudyManifest.model_validate({**manifest.model_dump(), "unversioned_field": True})


def test_study_artifacts_validate_against_packaged_versioned_schemas() -> None:
    manifest_payload = _manifest().model_dump(mode="json", by_alias=True)
    record_payload = _record().model_dump(mode="json", by_alias=True)

    manifest_schema = _packaged_schema("study-manifest-v1.schema.json")
    record_schema = _packaged_schema("study-run-record-v1.schema.json")
    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator.check_schema(record_schema)
    Draft202012Validator(manifest_schema).validate(manifest_payload)
    Draft202012Validator(record_schema).validate(record_payload)

    assert manifest_payload["$schema"] == STUDY_MANIFEST_SCHEMA_ID
    assert manifest_payload["schema_version"] == STUDY_MANIFEST_SCHEMA_VERSION
    assert record_payload["$schema"] == STUDY_RUN_RECORD_SCHEMA_ID
    assert record_payload["schema_version"] == STUDY_RUN_RECORD_SCHEMA_VERSION
    assert record_payload["kind"] == "webagent-study-run"


def test_study_ledger_retains_split_condition_and_confidence(tmp_path: Path) -> None:
    record = _record()
    path = append_study_record(tmp_path / "ledger" / "runs.jsonl", record)
    append_study_record(path, record.model_copy(update={"repetition": 2}))

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["repetition"] for row in rows] == [1, 2]
    assert rows[0]["$schema"] == STUDY_RUN_RECORD_SCHEMA_ID
    assert rows[0]["kind"] == "webagent-study-run"
    assert rows[0]["split"] == "held_out_task"
    assert rows[0]["success_probability"] == 0.8

    with pytest.raises(ValueError, match="already contains task-run identity"):
        append_study_record(path, record)


def test_study_ledger_retries_short_writes_until_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = studies_module.os.write

    def short_write(descriptor: int, payload: bytes) -> int:
        return real_write(descriptor, payload[:7])

    monkeypatch.setattr(studies_module.os, "write", short_write)
    path = append_study_record(tmp_path / "ledger" / "runs.jsonl", _record())

    assert StudyRunRecord.model_validate_json(path.read_bytes()) == _record()


def test_windows_lock_backend_is_loaded_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, int]] = []
    fake = SimpleNamespace(
        LK_LOCK=1,
        LK_UNLCK=2,
        locking=lambda _fd, operation, length: calls.append((operation, length)),
    )
    monkeypatch.setattr(studies_module.os, "name", "nt")
    monkeypatch.setattr(studies_module, "import_module", lambda _name: fake)

    with (tmp_path / "lock").open("w+b") as handle:
        studies_module._lock_handle(handle)
        studies_module._unlock_handle(handle)

    assert calls == [(1, 1), (2, 1)]


def test_published_task_record_is_hash_bound_to_preregistered_study(tmp_path: Path) -> None:
    study_root = tmp_path / "failure-transfer-v1"
    assertion = BenchmarkAssertion(kind="text_contains", expected="done")
    task = BenchmarkTask(
        id="heldout-task",
        category="controlled",
        goal="finish",
        start_url="https://example.test/task",
        assertions=[assertion],
        max_steps=30,
        split="held_out_task",
        setting_id="site-v2",
    )
    task_manifest_bytes = json.dumps(
        [task.model_dump(mode="json")],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    task_manifest_sha256 = hashlib.sha256(task_manifest_bytes).hexdigest()
    manifest = _manifest().model_copy(
        update={
            "task_manifest_sha256": task_manifest_sha256,
            "task_split_counts": {"held_out_task": 1},
            "models": (StudyModel(provider="openrouter", model="qwen/qwen3.8-flash"),),
            "conditions": (StudyCondition(id="baseline", kind="baseline", description="Baseline"),),
        }
    )
    from webagent.evaluation import StudyLayout

    initialize_matrix_study(
        study_root,
        manifest,
        task_manifest_bytes=task_manifest_bytes,
    )
    execution = StudyLayout.from_root(study_root).allocate_execution(
        model="qwen/qwen3.8-flash",
        condition="baseline",
        now=datetime(2026, 8, 30, 1, tzinfo=UTC),
    )
    registered_task_set_sha256 = task_set_sha256([task])
    execution.prepare(
        study_id=manifest.study_id,
        task_manifest_sha256=task_manifest_sha256,
        task_set_sha256=registered_task_set_sha256,
    )
    evaluation = TaskEvaluation(
        task_id="heldout-task",
        category="controlled",
        goal="finish",
        passed=True,
        score=1.0,
        agent_reported_success=True,
        agent_status="completed",
        duration_seconds=1,
        steps=1,
        action_count=1,
        failed_action_count=0,
        planner_attempt_count=1,
        planner_failure_count=0,
        planner_tokens=10,
        split="held_out_task",
        setting_id="site-v2",
        success_probability=0.8,
        assertions=[AssertionOutcome(assertion=assertion, passed=True)],
    )
    report_path = execution.task_run(evaluation.task_id).evaluation_dir / "task.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(evaluation.model_dump_json(), encoding="utf-8")
    context = StudyRunContext(
        study_root=study_root,
        study_id=manifest.study_id,
        provider="openrouter",
        model="qwen/qwen3.8-flash",
        condition_id="baseline",
        repetition=1,
        task_manifest_sha256=task_manifest_sha256,
        task_set_sha256=registered_task_set_sha256,
    )

    records = publish_study_run_records(
        context,
        execution=execution,
        suite=manifest.suite,
        created_at="2026-08-30T01:00:00+00:00",
        evaluations=[evaluation],
    )

    assert records[0].report_path.endswith("evaluation/task.json")
    assert records[0].report_sha256 == hashlib.sha256(report_path.read_bytes()).hexdigest()
    stored = StudyRunRecord.model_validate_json(
        StudyLayout.from_root(study_root).ledger_path.read_bytes()
    )
    assert stored == records[0]
    assert load_study_records(study_root) == records

    report_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="report_sha256"):
        load_study_records(study_root)


def test_matrix_study_retains_bytes_named_by_manifest_hash(tmp_path: Path) -> None:
    payload = b'{"suite":"open","tasks":[]}\n'
    digest = hashlib.sha256(payload).hexdigest()
    manifest = _manifest().model_copy(update={"task_manifest_sha256": digest})
    root = tmp_path / manifest.study_id

    initialize_matrix_study(root, manifest, task_manifest_bytes=payload)
    retained = root / "inputs" / "task-manifests" / f"{digest}.json"

    assert retained.read_bytes() == payload
    assert json.loads((root / "study.json").read_text())["task_manifest_sha256"] == digest
    with pytest.raises(ValueError, match="do not match"):
        initialize_matrix_study(root, manifest, task_manifest_bytes=b"different")


def test_direct_study_context_is_bound_to_retained_task_bytes(tmp_path: Path) -> None:
    task = BenchmarkTask(
        id="task-a",
        category="controlled",
        goal="finish",
        start_url="https://example.test",
        assertions=[BenchmarkAssertion(kind="text_contains", expected="done")],
    )
    payload = json.dumps([task.model_dump(mode="json")]).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    manifest = _manifest().model_copy(update={"task_manifest_sha256": digest})
    root = tmp_path / manifest.study_id
    initialize_matrix_study(root, manifest, task_manifest_bytes=payload)
    args = SimpleNamespace(
        study_root=root,
        study_id=manifest.study_id,
        provider="openrouter",
        condition_id="baseline",
        repetition=1,
    )

    context = study_context_from_args(args, model="z-ai/glm-5.3-flash")

    assert context is not None
    assert context.task_manifest_sha256 == digest
    assert context.task_set_sha256 == task_set_sha256([task])
    args.study_id = "different-study"
    with pytest.raises(ValueError, match="context id"):
        study_context_from_args(args, model="z-ai/glm-5.3-flash")


@pytest.mark.asyncio
async def test_study_runner_rejects_subset_or_changed_tasks_before_execution(
    tmp_path: Path,
) -> None:
    tasks = [
        BenchmarkTask(
            id=f"task-{suffix}",
            category="controlled",
            goal=f"complete {suffix}",
            start_url=f"https://example.test/{suffix}",
            assertions=[BenchmarkAssertion(kind="text_contains", expected=suffix)],
            max_steps=30,
        )
        for suffix in ("a", "b")
    ]
    payload = json.dumps(
        [task.model_dump(mode="json") for task in tasks],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    manifest = _manifest().model_copy(
        update={
            "task_manifest_sha256": digest,
            "task_split_counts": {"development": 2},
            "models": (StudyModel(provider="openrouter", model="model-a"),),
            "conditions": (StudyCondition(id="baseline", kind="baseline", description="Baseline"),),
        }
    )
    study_root = tmp_path / manifest.study_id
    initialize_matrix_study(study_root, manifest, task_manifest_bytes=payload)
    task_digest = task_set_sha256(tasks)
    execution = StudyLayout.from_root(study_root).allocate_execution(
        model="model-a",
        condition="baseline",
    )
    execution.prepare(
        study_id=manifest.study_id,
        task_manifest_sha256=digest,
        task_set_sha256=task_digest,
    )
    context = StudyRunContext(
        study_root=study_root,
        study_id=manifest.study_id,
        provider="openrouter",
        model="model-a",
        condition_id="baseline",
        repetition=1,
        task_manifest_sha256=digest,
        task_set_sha256=task_digest,
    )
    called = False

    async def execute(_task: BenchmarkTask) -> None:
        nonlocal called
        called = True

    runner = BenchmarkRunner(  # type: ignore[arg-type]
        object(),
        execute,
        output_dir=execution.root,
        execution_prepared=True,
        study_context=context,
    )

    with pytest.raises(ValueError, match="subsets and reordered"):
        await runner.run(manifest.suite, tasks[:1])
    changed = [tasks[0].model_copy(update={"goal": "different goal"}), tasks[1]]
    with pytest.raises(ValueError, match="complete preregistered"):
        await runner.run(manifest.suite, changed)
    assert called is False


def test_study_task_set_enforces_preregistered_task_class_budgets(tmp_path: Path) -> None:
    tasks = [
        BenchmarkTask(
            id="direct-task",
            category="document",
            goal="read a known page",
            start_url="https://example.test/direct",
            assertions=[BenchmarkAssertion(kind="text_contains", expected="done")],
            max_steps=20,
        ),
        BenchmarkTask(
            id="discovery-task",
            category="discovery",
            goal="find a page",
            start_url="about:blank",
            assertions=[
                BenchmarkAssertion(kind="certificate_valid", expected=True),
                BenchmarkAssertion(
                    kind="history_url_observed",
                    expected="https://example.test/found",
                ),
                BenchmarkAssertion(
                    kind="answer_contains",
                    expected="https://example.test/found",
                ),
            ],
            max_steps=20,
            network_required=True,
            discovery_required=True,
            source_urls=["https://example.test/found"],
            snapshot_id="discovery-snapshot",
            valid_from="2026-01-01",
            valid_until="2026-12-31",
        ),
    ]
    payload = json.dumps([task.model_dump(mode="json") for task in tasks]).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    manifest = _manifest().model_copy(
        update={
            "task_manifest_sha256": digest,
            "task_split_counts": {"development": 2},
            "models": (StudyModel(provider="openrouter", model="model-a"),),
            "conditions": (
                StudyCondition(
                    id="baseline",
                    kind="baseline",
                    description="Class-specific task budgets",
                    config_overrides={
                        "task_step_budgets": {"default": 8, "discovery_required": 12}
                    },
                ),
            ),
            "budgets": StudyBudgets(
                max_steps=12,
                task_timeout_seconds=1200,
                tool_timeout_seconds=300,
                planner_max_tokens=6000,
            ),
        }
    )
    root = tmp_path / manifest.study_id
    initialize_matrix_study(root, manifest, task_manifest_bytes=payload)
    execution = StudyLayout.from_root(root).allocate_execution(
        model="model-a",
        condition="baseline",
    )
    task_digest = task_set_sha256(tasks)
    execution.prepare(
        study_id=manifest.study_id,
        task_manifest_sha256=digest,
        task_set_sha256=task_digest,
    )
    context = StudyRunContext(
        study_root=root,
        study_id=manifest.study_id,
        provider="openrouter",
        model="model-a",
        condition_id="baseline",
        repetition=1,
        task_manifest_sha256=digest,
        task_set_sha256=task_digest,
    )
    planned = [
        tasks[0].model_copy(update={"max_steps": 8}),
        tasks[1].model_copy(update={"max_steps": 12}),
    ]

    validate_study_task_set(
        context,
        execution=execution,
        suite=manifest.suite,
        tasks=planned,
    )
    with pytest.raises(ValueError, match="max_steps differs"):
        validate_study_task_set(
            context,
            execution=execution,
            suite=manifest.suite,
            tasks=[planned[0].model_copy(update={"max_steps": 7}), planned[1]],
        )

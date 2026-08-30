"""Tests for the ownership-aware run and workspace layouts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from webagent.evaluation.artifacts import (
    RUN_LAYOUT_VERSION,
    RUN_MANIFEST_FORMAT,
    RUN_MANIFEST_SCHEMA_URI,
    RUN_MANIFEST_SCHEMA_VERSION,
    STUDY_EXECUTION_FORMAT,
    OutputWorkspace,
    RunLayout,
    RunOwnershipError,
    StudyExecutionLayout,
    StudyLayout,
)


def test_run_layout_exposes_separated_research_outputs(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "run")

    assert layout.manifest_path == layout.root / "manifest.json"
    assert layout.trace_path == layout.root / "trajectory" / "trace.json"
    assert layout.turn_trace_path(1) == layout.root / "trajectory" / "turns" / "turn-001.json"
    assert layout.verification_path == layout.root / "trajectory" / "verification.json"
    assert layout.screenshots_dir == layout.root / "observations" / "screenshots"
    assert layout.checkpoint_path == layout.root / "control" / "checkpoints" / "latest.json"
    assert layout.artifacts_dir == layout.root / "artifacts"
    assert layout.summary_path == layout.root / "result" / "summary.txt"
    assert layout.attachments_dir == layout.root / "result" / "attachments"
    assert (
        layout.turn_summary_path(2) == layout.root / "result" / "turns" / "turn-002" / "summary.txt"
    )
    assert layout.turn_attachments_dir(2) == (
        layout.root / "result" / "turns" / "turn-002" / "attachments"
    )
    assert layout.evaluation_dir == layout.root / "evaluation"

    with pytest.raises(ValueError, match="turn_index"):
        layout.turn_trace_path(0)


def test_workspace_allocates_unique_dated_model_task_paths(tmp_path: Path) -> None:
    workspace = OutputWorkspace.from_root(tmp_path / "outputs")
    timestamp = datetime(2026, 8, 30, 12, tzinfo=UTC)

    first = workspace.allocate_run(
        task="Find the most recent Qwen report",
        model="qwen/qwen3.8-flash",
        now=timestamp,
        run_id="aaaaaaaa-0000-0000-0000-000000000000",
    )
    second = workspace.allocate_run(
        task="Find the most recent Qwen report",
        model="qwen/qwen3.8-flash",
        now=timestamp,
        run_id="bbbbbbbb-0000-0000-0000-000000000000",
    )

    assert first.root.parent.name == "qwen-qwen3-8-flash"
    assert first.root.parent.parent.name == "2026-08-30"
    assert first.root.name.startswith("find-the-most-recent-qwen-report-")
    assert first.root != second.root
    assert not workspace.root.exists()


def test_study_layout_separates_inputs_runs_ledger_and_analysis(tmp_path: Path) -> None:
    workspace = OutputWorkspace.from_root(tmp_path / "outputs")
    layout = workspace.study("failure-transfer-v1")
    layout.prepare()

    assert isinstance(layout, StudyLayout)
    assert layout.manifest_path == layout.root / "study.json"
    assert layout.task_run("heldout-task").root == layout.runs_dir / "heldout-task"
    assert layout.ledger_path == layout.root / "ledger" / "runs.jsonl"
    assert layout.report_path == layout.root / "results.json"
    assert layout.inputs_dir.is_dir()
    assert layout.executions_dir.is_dir()
    assert layout.evidence_dir.is_dir()
    assert layout.logs_dir.is_dir()
    assert layout.analysis_dir.is_dir()
    assert layout.task_manifests_dir.is_dir()
    assert layout.matrix_snapshots_dir.is_dir()

    execution = layout.allocate_execution(
        model="provider/model-a",
        condition="baseline",
        now=datetime(2026, 8, 30, 12, 34, 56, tzinfo=UTC),
        execution_id="rep-1",
    )
    assert isinstance(execution, StudyExecutionLayout)
    assert execution.root.relative_to(layout.executions_dir).parts == (
        "2026-08-30",
        "provider-model-a",
        "baseline",
        "123456-000000-rep-1",
    )
    execution.prepare()
    assert json.loads(execution.claim_path.read_text())["format"] == STUDY_EXECUTION_FORMAT
    assert execution.ledger_path == execution.root / "ledger" / "time-slices.jsonl"
    assert execution.shards_dir == execution.root / "shards"
    assert execution.browser_profiles_dir == execution.root / "control" / "browser-profiles"
    assert execution.logs_dir == execution.root / "evidence" / "logs"
    execution.require_prepared()
    assert execution.task_run("heldout-task").root == execution.runs_dir / "heldout-task"

    with pytest.raises(ValueError, match="task_id"):
        layout.task_run("../escape")


def test_execution_layout_refuses_to_replace_task_evidence(tmp_path: Path) -> None:
    layout = StudyExecutionLayout.from_root(tmp_path / "execution")
    layout.prepare()
    task_run = layout.task_run("task-a")
    task_run.root.mkdir(parents=True)
    (task_run.root / "sentinel.txt").write_text("retain", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already contains run evidence"):
        layout.prepare()

    assert (task_run.root / "sentinel.txt").read_text(encoding="utf-8") == "retain"


def test_execution_layout_is_claimed_once_and_partial_roots_fail_closed(tmp_path: Path) -> None:
    claimed = StudyExecutionLayout.from_root(tmp_path / "claimed")
    claimed.prepare()
    with pytest.raises(FileExistsError, match="run evidence or a claim"):
        claimed.prepare()

    partial = StudyExecutionLayout.from_root(tmp_path / "partial")
    partial.root.mkdir()
    partial.inputs_dir.mkdir()
    with pytest.raises(FileExistsError, match="run evidence or a claim"):
        partial.prepare()
    with pytest.raises(RunOwnershipError, match="valid claim"):
        partial.require_prepared()


def test_prepare_creates_owned_layout_without_storing_plain_task(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "run")
    layout.prepare(run_id="run-1", task="private research question", model="model-a")

    for directory in (
        layout.trajectory_dir,
        layout.trajectory_turns_dir,
        layout.screenshots_dir,
        layout.checkpoints_dir,
        layout.downloads_dir,
        layout.documents_dir,
        layout.figures_dir,
        layout.files_dir,
        layout.attachments_dir,
        layout.result_turns_dir,
        layout.evaluation_dir,
    ):
        assert directory.is_dir()
    raw = layout.manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert manifest["format"] == RUN_MANIFEST_FORMAT
    assert manifest["$schema"] == RUN_MANIFEST_SCHEMA_URI
    assert manifest["schema_version"] == RUN_MANIFEST_SCHEMA_VERSION
    assert manifest["layout_version"] == RUN_LAYOUT_VERSION
    assert manifest["run_id"] == "run-1"
    assert manifest["model"] == "model-a"
    assert "private research question" not in raw

    schema_path = Path(__file__).parents[2] / "src/webagent/schemas/run-manifest-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=None).validate(manifest)


def test_owned_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "run")
    layout.prepare(run_id="run-1", task="task", model="model")
    manifest = json.loads(layout.manifest_path.read_text())
    manifest["unexpected"] = True
    layout.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RunOwnershipError, match="Invalid run ownership manifest"):
        layout.prepare(run_id="run-2", task="task", model="model")


def test_prepare_rejects_nonempty_unowned_directory_without_deleting_it(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "explicit-run")
    layout.root.mkdir()
    sentinel = layout.root / "manual-notes.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(RunOwnershipError, match="unowned"):
        layout.prepare(run_id="run-1", task="task", model="model")

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_prepare_cleans_only_generated_entries_of_owned_run(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "run")
    layout.prepare(run_id="old", task="old task", model="old-model")
    stale = layout.artifacts_dir / "stale.txt"
    stale.write_text("stale", encoding="utf-8")
    manual = layout.root / "research-notes.md"
    manual.write_text("preserve", encoding="utf-8")

    layout.prepare(run_id="new", task="new task", model="new-model")

    assert not stale.exists()
    assert manual.read_text(encoding="utf-8") == "preserve"
    assert json.loads(layout.manifest_path.read_text())["run_id"] == "new"


def test_prepare_refuses_current_working_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="unsafe output directory"):
        RunLayout.from_root(tmp_path).prepare(run_id="x", task="x", model="x")


def test_current_readers_fall_back_to_legacy_control_and_trace_files(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "legacy")
    layout.artifacts_dir.mkdir(parents=True)
    layout.legacy_trace_path.write_text("{}", encoding="utf-8")
    layout.legacy_verification_path.write_text("{}", encoding="utf-8")
    layout.legacy_checkpoint_path.write_text("{}", encoding="utf-8")

    assert layout.trace_path_for_read() == layout.legacy_trace_path
    assert layout.verification_path_for_read() == layout.legacy_verification_path
    assert layout.checkpoint_path_for_read() == layout.legacy_checkpoint_path
    assert RunLayout.root_from_checkpoint(layout.legacy_checkpoint_path) == layout.root

    layout.ensure_for_resume(run_id="run-1", task="legacy task", model="model/a")
    layout.trace_path.write_text("{}", encoding="utf-8")
    layout.verification_path.write_text("{}", encoding="utf-8")
    layout.checkpoint_path.write_text("{}", encoding="utf-8")
    assert layout.trace_path_for_read() == layout.trace_path
    assert layout.verification_path_for_read() == layout.verification_path
    assert layout.checkpoint_path_for_read() == layout.checkpoint_path
    assert RunLayout.root_from_checkpoint(layout.checkpoint_path) == layout.root


def test_resume_adopts_validated_legacy_run_with_manifest(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path / "legacy")
    layout.legacy_checkpoint_path.parent.mkdir(parents=True)
    layout.legacy_checkpoint_path.write_text("validated elsewhere", encoding="utf-8")

    layout.ensure_for_resume(run_id="legacy-run", task="old task", model="model/a")

    manifest = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "legacy-run"
    assert layout.legacy_checkpoint_path.read_text(encoding="utf-8") == "validated elsewhere"

"""Typed, immutable metadata for repeated and held-out agent studies."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from webagent.evaluation.models import ResearchSplit

if TYPE_CHECKING:
    from webagent.evaluation.artifacts import StudyExecutionLayout, StudyLayout
    from webagent.evaluation.models import BenchmarkTask, TaskEvaluation

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
STUDY_MANIFEST_SCHEMA_VERSION = 1
STUDY_MANIFEST_SCHEMA_ID: Literal[
    "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
    "src/webagent/schemas/study-manifest-v1.schema.json"
] = (
    "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
    "src/webagent/schemas/study-manifest-v1.schema.json"
)
STUDY_RUN_RECORD_SCHEMA_VERSION = 1
STUDY_RUN_RECORD_SCHEMA_ID: Literal[
    "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
    "src/webagent/schemas/study-run-record-v1.schema.json"
] = (
    "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
    "src/webagent/schemas/study-run-record-v1.schema.json"
)


class StudyModel(BaseModel):
    """One provider/model endpoint compared by a study."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_protocol: Literal["openai-compatible", "local-vllm"] = "openai-compatible"
    vision_expected: bool | None = None


class StudyCondition(BaseModel):
    """A baseline, intervention, or ablation fixed before collection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_ID_PATTERN)
    kind: Literal["baseline", "intervention", "ablation"]
    description: str = Field(min_length=1)
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class StudyBudgets(BaseModel):
    """Comparable execution budgets shared by study runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(ge=1)
    task_timeout_seconds: int = Field(ge=1)
    tool_timeout_seconds: int = Field(ge=1)
    planner_max_tokens: int = Field(ge=1)


class StudyManifest(BaseModel):
    """Pre-run comparison contract for a reproducible agent study."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_uri: Literal[
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/study-manifest-v1.schema.json"
    ] = Field(default=STUDY_MANIFEST_SCHEMA_ID, alias="$schema")
    schema_version: Literal[1] = 1
    kind: Literal["webagent-study"] = "webagent-study"
    study_id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1)
    research_questions: tuple[str, ...] = Field(min_length=1)
    hypotheses: tuple[str, ...] = ()
    suite: str = Field(min_length=1)
    task_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    task_split_counts: dict[ResearchSplit, int] = Field(default_factory=dict)
    models: tuple[StudyModel, ...] = Field(min_length=1)
    conditions: tuple[StudyCondition, ...] = Field(min_length=1)
    collection_dates: tuple[date, ...] = ()
    repetitions: int = Field(default=1, ge=1)
    budgets: StudyBudgets
    primary_metrics: tuple[str, ...] = Field(min_length=1)
    secondary_metrics: tuple[str, ...] = ()
    failure_taxonomy_version: str = Field(default="1", min_length=1)
    confidence_target: Literal["task_success", "not_collected"] = "not_collected"
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    preregistration_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_comparison_identity(self) -> StudyManifest:
        model_ids = [(item.provider, item.model) for item in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("study models must be unique by provider and model")
        condition_ids = [item.id for item in self.conditions]
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("study condition ids must be unique")
        if any(count < 0 for count in self.task_split_counts.values()):
            raise ValueError("task split counts cannot be negative")
        if len(self.collection_dates) != len(set(self.collection_dates)):
            raise ValueError("study collection dates must be unique")
        return self


class StudyRunRecord(BaseModel):
    """Hash-bound ledger row for one completed task run."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    schema_uri: Literal[
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/study-run-record-v1.schema.json"
    ] = Field(default=STUDY_RUN_RECORD_SCHEMA_ID, alias="$schema")
    schema_version: Literal[1] = 1
    kind: Literal["webagent-study-run"] = "webagent-study-run"
    study_id: str = Field(pattern=_ID_PATTERN)
    task_id: str = Field(pattern=_ID_PATTERN)
    split: ResearchSplit
    setting_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    condition_id: str = Field(pattern=_ID_PATTERN)
    failure_taxonomy_version: str = Field(min_length=1)
    collection_date: date
    repetition: int = Field(ge=1)
    run_path: str = Field(min_length=1)
    report_path: str = Field(min_length=1)
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    success: bool
    success_probability: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_evidence_paths(self) -> StudyRunRecord:
        run_path = PurePosixPath(self.run_path)
        report_path = PurePosixPath(self.report_path)
        if run_path.is_absolute() or ".." in run_path.parts:
            raise ValueError("study run_path must stay below the study root")
        if report_path.is_absolute() or ".." in report_path.parts:
            raise ValueError("study report_path must stay below the study root")
        if report_path != run_path / "evaluation" / "task.json":
            raise ValueError("study report_path must name the task evaluation below run_path")
        return self


class StudyRunContext(BaseModel):
    """Explicit observed identity required before emitting canonical study rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    study_root: Path
    study_id: str = Field(pattern=_ID_PATTERN)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    condition_id: str = Field(pattern=_ID_PATTERN)
    repetition: int = Field(ge=1)
    task_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    task_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    collection_date: date | None = None


def write_study_manifest(path: Path, manifest: StudyManifest) -> Path:
    """Publish an immutable study contract, allowing only byte-equivalent retries."""
    target = path.expanduser().resolve()
    encoded = _encoded(manifest.model_dump(mode="json", by_alias=True))
    if target.exists():
        if target.read_bytes() != encoded:
            raise FileExistsError(f"study manifest already exists with different bytes: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(target)
    return target


def read_study_manifest(path: Path) -> StudyManifest:
    """Load one typed study contract from disk."""
    target = path.expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read study manifest: {target}") from exc
    return StudyManifest.model_validate(payload)


def load_study_records(study_root: Path) -> tuple[StudyRunRecord, ...]:
    """Load and reverify every canonical task record against retained evidence."""
    from webagent.evaluation.artifacts import StudyLayout

    study = StudyLayout.from_root(study_root)
    manifest = read_study_manifest(study.manifest_path)
    records: list[StudyRunRecord] = []
    identities: set[tuple[object, ...]] = set()
    if not study.ledger_path.is_file():
        raise ValueError(f"cannot read canonical study ledger: {study.ledger_path}")
    with _exclusive_ledger_lock(study.ledger_path):
        try:
            lines = study.ledger_path.read_bytes().splitlines()
        except OSError as exc:
            raise ValueError(f"cannot read canonical study ledger: {study.ledger_path}") from exc
        for line_number, line in enumerate(lines, start=1):
            try:
                record = StudyRunRecord.model_validate_json(line)
                verified = verify_study_record(
                    record,
                    study_root=study.root,
                    manifest=manifest,
                )
            except ValueError as exc:
                raise ValueError(
                    f"invalid canonical study ledger row {study.ledger_path}:{line_number}: {exc}"
                ) from exc
            identity = _record_identity(verified)
            if identity in identities:
                raise ValueError(
                    f"canonical study ledger contains duplicate task-run identity: {identity}"
                )
            identities.add(identity)
            records.append(verified)
    return tuple(records)


def verify_study_record(
    record: StudyRunRecord,
    *,
    study_root: Path,
    manifest: StudyManifest | None = None,
) -> StudyRunRecord:
    """Rehash one task evaluation and validate its preregistered study identity."""
    from webagent.evaluation.artifacts import StudyExecutionLayout, StudyLayout, safe_slug
    from webagent.evaluation.models import TaskEvaluation
    from webagent.evaluation.task_binding import task_set_sha256

    study = StudyLayout.from_root(study_root)
    contract = manifest or read_study_manifest(study.manifest_path)
    if contract.study_id != record.study_id:
        raise ValueError("study record id does not match the retained study manifest")
    if (record.provider, record.model) not in {
        (item.provider, item.model) for item in contract.models
    }:
        raise ValueError("study record provider/model is not preregistered")
    if record.condition_id not in {item.id for item in contract.conditions}:
        raise ValueError("study record condition is not preregistered")
    if record.repetition > contract.repetitions:
        raise ValueError("study record repetition exceeds the preregistered count")
    if contract.collection_dates and record.collection_date not in contract.collection_dates:
        raise ValueError("study record collection date is not preregistered")
    if record.failure_taxonomy_version != contract.failure_taxonomy_version:
        raise ValueError("study record failure taxonomy differs from the study manifest")

    registered_tasks = _retained_study_tasks(study, contract)
    registered_by_id = {task.id: task for task in registered_tasks}
    registered = registered_by_id.get(record.task_id)
    if registered is None:
        raise ValueError("study record task is absent from the preregistered task manifest")

    run_parts = PurePosixPath(record.run_path).parts
    expected_prefix = (
        "executions",
        record.collection_date.isoformat(),
        safe_slug(record.model, fallback="model"),
        safe_slug(record.condition_id, fallback="condition"),
    )
    if (
        len(run_parts) != 7
        or run_parts[:4] != expected_prefix
        or run_parts[5] != "runs"
        or run_parts[6] != record.task_id
    ):
        raise ValueError("study record run_path does not match its declared execution identity")
    StudyExecutionLayout.from_root(study.root.joinpath(*run_parts[:5])).require_prepared(
        study_id=contract.study_id,
        task_manifest_sha256=contract.task_manifest_sha256,
        task_set_sha256=task_set_sha256(registered_tasks),
    )

    root = study.root.resolve()
    run_path = (root / record.run_path).resolve()
    report_path = (root / record.report_path).resolve()
    try:
        run_path.relative_to(root)
        report_path.relative_to(run_path)
    except ValueError as exc:
        raise ValueError("study record evidence path escapes the study root") from exc
    if not run_path.is_dir() or not report_path.is_file():
        raise ValueError("study record retained task evidence is missing")
    report_raw = report_path.read_bytes()
    if _sha256(report_raw) != record.report_sha256:
        raise ValueError("study record report_sha256 does not match retained task evidence")
    try:
        evaluation = TaskEvaluation.model_validate_json(report_raw)
    except ValueError as exc:
        raise ValueError("retained task evidence is not a valid TaskEvaluation") from exc
    if (
        evaluation.task_id != record.task_id
        or evaluation.category != registered.category
        or evaluation.goal != registered.goal
        or evaluation.split != record.split
        or evaluation.split != registered.split
        or evaluation.task_family != registered.task_family
        or str(evaluation.setting_id) != record.setting_id
        or str(evaluation.setting_id) != str(registered.setting_id)
        or evaluation.leakage_group != registered.leakage_group
        or evaluation.target_failure_modes != registered.target_failure_modes
        or evaluation.feedback != registered.feedback
        or evaluation.expected_horizon != registered.expected_horizon
        or [outcome.assertion for outcome in evaluation.assertions] != registered.assertions
        or evaluation.passed != record.success
        or evaluation.success_probability != record.success_probability
    ):
        raise ValueError("study record identity/outcome differs from retained task evidence")
    return record


def append_study_record(path: Path, record: StudyRunRecord) -> Path:
    """Append one unique canonical JSONL row and fsync it before returning."""
    return append_study_records(path, [record])


def append_study_records(path: Path, records: Sequence[StudyRunRecord]) -> Path:
    """Append one conflict-checked batch while holding an exclusive file lock.

    A process crash can leave a final partial JSONL row; readers deliberately
    reject such a ledger instead of silently accepting the valid prefix.
    """
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = list(records)
    pending_keys = [_record_identity(record) for record in pending]
    if len(pending_keys) != len(set(pending_keys)):
        raise ValueError("study record batch contains duplicate task-run identities")
    encoded = b"".join(
        json.dumps(
            record.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
        for record in pending
    )
    with _exclusive_ledger_lock(target):
        descriptor = os.open(target, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            existing_raw = _read_all(descriptor, os.fstat(descriptor).st_size)
            existing_keys: set[tuple[object, ...]] = set()
            for line_number, line in enumerate(existing_raw.splitlines(), start=1):
                try:
                    existing = StudyRunRecord.model_validate_json(line)
                except ValueError as exc:
                    raise ValueError(
                        f"invalid canonical study ledger row {target}:{line_number}"
                    ) from exc
                identity = _record_identity(existing)
                if identity in existing_keys:
                    raise ValueError(
                        f"canonical study ledger already contains duplicate row: {identity}"
                    )
                existing_keys.add(identity)
            collisions = existing_keys.intersection(pending_keys)
            if collisions:
                raise ValueError(
                    f"canonical study ledger already contains task-run identity: {collisions}"
                )
            os.lseek(descriptor, 0, os.SEEK_END)
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return target


def _write_all(descriptor: int, payload: bytes) -> None:
    """Write every byte even when the operating system reports a short write."""
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("ledger write made no progress")
        offset += written


def _read_all(descriptor: int, expected_size: int) -> bytes:
    """Read a complete ledger snapshot while its sidecar lock is held."""
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise OSError("ledger read ended before the expected file size")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@contextmanager
def _exclusive_ledger_lock(target: Path) -> Iterator[None]:
    """Hold a one-byte sidecar lock using the current platform's stdlib."""
    lock_path = target.with_name(f".{target.name}.lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        _lock_handle(handle)
        try:
            yield
        finally:
            handle.seek(0)
            _unlock_handle(handle)


def _lock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = import_module("msvcrt")
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def publish_study_run_records(
    context: StudyRunContext,
    *,
    execution: StudyExecutionLayout,
    suite: str,
    created_at: str,
    evaluations: Sequence[TaskEvaluation],
) -> tuple[StudyRunRecord, ...]:
    """Hash-bind persisted task evaluations to a preregistered study ledger.

    No provider, condition, repetition, or collection date is inferred from a
    model name or directory.  Those values must arrive through ``context``;
    the only derived date is the timezone-aware report timestamp.
    """
    from webagent.evaluation.artifacts import StudyLayout
    from webagent.evaluation.models import TaskEvaluation

    study = StudyLayout.from_root(context.study_root)
    manifest = read_study_manifest(study.manifest_path)
    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("benchmark report created_at must include a timezone")
    observed_date = timestamp.astimezone(UTC).date()
    collection_date = context.collection_date or observed_date
    if collection_date != observed_date:
        raise ValueError("study context collection_date differs from report timestamp")
    validate_study_run_context(
        context,
        execution=execution,
        suite=suite,
        collection_date=collection_date,
    )
    registered_tasks = _retained_study_tasks(study, manifest)
    registered_by_id = {task.id: task for task in registered_tasks}
    evaluated_ids = [evaluation.task_id for evaluation in evaluations]
    registered_ids = [task.id for task in registered_tasks]
    if len(evaluated_ids) != len(set(evaluated_ids)) or set(evaluated_ids) != set(registered_ids):
        raise ValueError(
            "published task evaluations differ from the complete preregistered task set"
        )
    for evaluation in evaluations:
        registered = registered_by_id[evaluation.task_id]
        if evaluation.split != registered.split or str(evaluation.setting_id) != str(
            registered.setting_id
        ):
            raise ValueError(
                "published task evaluation identity differs from the preregistered task: "
                f"{evaluation.task_id}"
            )
    study_root = study.root

    records: list[StudyRunRecord] = []
    for evaluation in evaluations:
        task_root = execution.task_run(evaluation.task_id).root
        report_path = task_root / "evaluation" / "task.json"
        if not report_path.is_file():
            raise ValueError(f"task evaluation evidence is missing: {report_path}")
        report_raw = report_path.read_bytes()
        persisted = TaskEvaluation.model_validate_json(report_raw)
        if persisted != evaluation:
            raise ValueError(
                f"task evaluation differs from retained evidence: {evaluation.task_id}"
            )
        records.append(
            StudyRunRecord(
                study_id=context.study_id,
                task_id=evaluation.task_id,
                split=evaluation.split,
                setting_id=str(evaluation.setting_id),
                provider=context.provider,
                model=context.model,
                condition_id=context.condition_id,
                failure_taxonomy_version=manifest.failure_taxonomy_version,
                collection_date=collection_date,
                repetition=context.repetition,
                run_path=task_root.relative_to(study_root).as_posix(),
                report_path=report_path.relative_to(study_root).as_posix(),
                report_sha256=_sha256(report_raw),
                success=evaluation.passed,
                success_probability=evaluation.success_probability,
            )
        )
    append_study_records(study.ledger_path, records)
    return tuple(records)


def validate_study_run_context(
    context: StudyRunContext,
    *,
    execution: StudyExecutionLayout,
    suite: str,
    collection_date: date | None = None,
) -> None:
    """Fail before execution when a run is outside its typed study contract."""
    from webagent.evaluation.artifacts import StudyLayout

    study = StudyLayout.from_root(context.study_root)
    manifest = read_study_manifest(study.manifest_path)
    if manifest.study_id != context.study_id:
        raise ValueError("study run context does not match study manifest id")
    if manifest.suite != suite:
        raise ValueError("benchmark suite does not match study manifest")
    if manifest.task_manifest_sha256 != context.task_manifest_sha256:
        raise ValueError("study run task manifest hash is not preregistered")
    retained_tasks = _retained_study_tasks(study, manifest)
    from webagent.evaluation.task_binding import task_set_sha256

    if task_set_sha256(retained_tasks) != context.task_set_sha256:
        raise ValueError("study run task-set hash differs from the retained task manifest")
    if (context.provider, context.model) not in {
        (item.provider, item.model) for item in manifest.models
    }:
        raise ValueError("study run provider/model is not preregistered")
    if context.condition_id not in {item.id for item in manifest.conditions}:
        raise ValueError("study run condition is not preregistered")
    if context.repetition > manifest.repetitions:
        raise ValueError("study run repetition exceeds the preregistered count")
    if (
        collection_date is not None
        and manifest.collection_dates
        and collection_date not in manifest.collection_dates
    ):
        raise ValueError("study run collection date is not preregistered")
    try:
        execution.root.relative_to(study.root)
    except ValueError as exc:
        raise ValueError("benchmark execution must stay below its study root") from exc
    execution.require_prepared(
        study_id=context.study_id,
        task_manifest_sha256=context.task_manifest_sha256,
        task_set_sha256=context.task_set_sha256,
    )
    _retain_execution_task_manifest(study, manifest, execution)


def validate_study_task_set(
    context: StudyRunContext,
    *,
    execution: StudyExecutionLayout,
    suite: str,
    tasks: Sequence[BenchmarkTask],
) -> None:
    """Bind the exact complete task sequence to its immutable preregistration."""
    from collections import Counter

    from webagent.evaluation.artifacts import StudyLayout
    from webagent.evaluation.task_binding import task_set_sha256

    validate_study_run_context(context, execution=execution, suite=suite)
    manifest = read_study_manifest(StudyLayout.from_root(context.study_root).manifest_path)
    observed_digest = task_set_sha256(tasks)
    if observed_digest != context.task_set_sha256:
        raise ValueError(
            "executed task set differs from the complete preregistered task set "
            "(subsets and reordered tasks are not valid study runs)"
        )
    condition = next(item for item in manifest.conditions if item.id == context.condition_id)
    step_budgets = _condition_task_step_budgets(condition, manifest.budgets)
    if step_budgets is None:
        mismatched_budget = any(task.max_steps != manifest.budgets.max_steps for task in tasks)
    else:
        mismatched_budget = any(
            task.max_steps
            != step_budgets["discovery_required" if task.discovery_required else "default"]
            for task in tasks
        )
    if mismatched_budget:
        raise ValueError("task max_steps differs from the preregistered study budget")
    observed_splits = dict(Counter(task.split for task in tasks))
    if manifest.task_split_counts and observed_splits != manifest.task_split_counts:
        raise ValueError("executed task split counts differ from the study manifest")


def _condition_task_step_budgets(
    condition: StudyCondition,
    study_budget: StudyBudgets,
) -> dict[str, int] | None:
    """Return an optional per-task-class budget fixed in a study condition."""
    raw = condition.config_overrides.get("task_step_budgets")
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"default", "discovery_required"}:
        raise ValueError("task_step_budgets must contain exactly default and discovery_required")
    parsed: dict[str, int] = {}
    for key in ("default", "discovery_required"):
        value = raw[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"task_step_budgets.{key} must be a positive integer")
        if value > study_budget.max_steps:
            raise ValueError(f"task_step_budgets.{key} exceeds the study max_steps cap")
        parsed[key] = value
    return parsed


def _retained_study_tasks(
    study: StudyLayout,
    manifest: StudyManifest,
) -> tuple[BenchmarkTask, ...]:
    from webagent.evaluation.task_binding import tasks_from_manifest_bytes

    retained = study.task_manifests_dir / f"{manifest.task_manifest_sha256}.json"
    try:
        payload = retained.read_bytes()
    except OSError as exc:
        raise ValueError(f"preregistered task manifest is missing: {retained}") from exc
    if _sha256(payload) != manifest.task_manifest_sha256:
        raise ValueError("retained task manifest bytes differ from the study manifest hash")
    return tasks_from_manifest_bytes(payload)


def _retain_execution_task_manifest(
    study: StudyLayout,
    manifest: StudyManifest,
    execution: StudyExecutionLayout,
) -> Path:
    """Copy exact preregistered bytes into the execution evidence boundary once."""
    source = study.task_manifests_dir / f"{manifest.task_manifest_sha256}.json"
    payload = source.read_bytes()
    target = execution.inputs_dir / "task-manifests" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        if target.read_bytes() != payload:
            raise ValueError("execution task-manifest hash path contains different bytes")
        return target
    temporary = target.with_suffix(".json.tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return target


def _record_identity(record: StudyRunRecord) -> tuple[object, ...]:
    return (
        record.study_id,
        record.task_id,
        record.setting_id,
        record.provider,
        record.model,
        record.condition_id,
        record.collection_date,
        record.repetition,
    )


def _sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


__all__ = [
    "STUDY_MANIFEST_SCHEMA_ID",
    "STUDY_MANIFEST_SCHEMA_VERSION",
    "STUDY_RUN_RECORD_SCHEMA_ID",
    "STUDY_RUN_RECORD_SCHEMA_VERSION",
    "StudyBudgets",
    "StudyCondition",
    "StudyManifest",
    "StudyModel",
    "StudyRunContext",
    "StudyRunRecord",
    "append_study_record",
    "append_study_records",
    "load_study_records",
    "publish_study_run_records",
    "read_study_manifest",
    "validate_study_run_context",
    "validate_study_task_set",
    "verify_study_record",
    "write_study_manifest",
]

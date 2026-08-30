"""Versioned, typed wire format for auditable agent execution traces."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from webagent.utils.runtime import package_source_fingerprint

RUN_TRACE_SCHEMA_VERSION = 8
RUN_TRACE_SCHEMA_ID = (
    "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
    "src/webagent/schemas/run-trace-v8.schema.json"
)
SUPPORTED_TRACE_SCHEMA_VERSIONS = frozenset({7, RUN_TRACE_SCHEMA_VERSION})


class TraceSchemaError(ValueError):
    """Raised when a trace cannot be validated or migrated safely."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TraceProducerV8(_StrictModel):
    """Identity of the package that emitted a trace."""

    name: Literal["lixiuyin-webagent"] = "lixiuyin-webagent"
    version: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TraceEvaluationV8(_StrictModel):
    """Evaluation and isolation settings bound into a run trace."""

    agent_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: str
    discovery_mode: str
    direct_source_tools_enabled: bool
    high_risk_action_policy: str
    stealth_mode: bool
    anti_shortcut_contract: str | None
    certificate_required: bool
    strict_eval_mode: bool
    search_engine_only: bool
    browser_profile_mode: str
    persistent_pdf_cache: bool


class TraceStepV8(_StrictModel):
    """One planner-visible, policy-audited tool execution."""

    step_number: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    timestamp: str | None = None
    tool: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    success: bool
    error: str | None = None
    result: Any = None
    planner_visible_result: str = ""
    policy: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    tool_duration_seconds: float | None = Field(default=None, ge=0.0)


class RunTraceV8(_StrictModel):
    """Stable v8 envelope persisted as ``trajectory/trace.json`` by new runs."""

    schema_version: Literal[8] = 8
    schema_uri: Literal[
        "https://raw.githubusercontent.com/lixiuyin/web-agent/main/"
        "src/webagent/schemas/run-trace-v8.schema.json"
    ] = Field(alias="$schema")
    producer: TraceProducerV8
    created_at: str | None = None
    run_id: str = Field(min_length=1)
    run_kind: Literal["agent_e2e"] = "agent_e2e"
    task: str
    status: str
    success: bool
    steps_taken: int = Field(default=0, ge=0)
    total_duration: float = Field(default=0.0, ge=0.0)
    final_result: Any = Field(default_factory=dict)
    evaluation: TraceEvaluationV8
    planner_attempts: list[dict[str, Any]] = Field(default_factory=list)
    events: list[Any] = Field(default_factory=list)
    steps: list[TraceStepV8] = Field(default_factory=list)
    resume_count: int = Field(default=0, ge=0)
    checkpoint_schema_version: int | None = Field(default=None, ge=1)
    resumed_from_checkpoint: bool = False

    @model_validator(mode="after")
    def _validate_continuation_metadata(self) -> Self:
        if self.resumed_from_checkpoint:
            if self.resume_count < 1:
                raise ValueError("resumed traces must have resume_count >= 1")
            if self.checkpoint_schema_version is None:
                raise ValueError("resumed traces must identify checkpoint_schema_version")
        elif self.resume_count != 0:
            raise ValueError("non-resumed traces must have resume_count == 0")
        return self


def _package_version() -> str:
    try:
        return version("lixiuyin-webagent")
    except PackageNotFoundError:
        return "0+unknown"


def build_run_trace_v8(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Add the v8 envelope to a new trace payload and validate it."""
    source_sha256 = package_source_fingerprint()
    values = dict(payload)
    values.update(
        {
            "schema_version": RUN_TRACE_SCHEMA_VERSION,
            "$schema": RUN_TRACE_SCHEMA_ID,
            "producer": {
                "name": "lixiuyin-webagent",
                "version": _package_version(),
                "source_sha256": source_sha256,
            },
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    evaluation = values.get("evaluation")
    if isinstance(evaluation, dict):
        evaluation = dict(evaluation)
        evaluation["agent_source_sha256"] = source_sha256
        if evaluation.get("anti_shortcut_contract") == "search_engine_only_v7":
            evaluation["anti_shortcut_contract"] = "search_engine_only_v8"
        values["evaluation"] = evaluation
    return _validated_dump(values)


def migrate_trace_to_v8(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically normalize a supported legacy trace to schema v8."""
    schema_version = trace.get("schema_version")
    if schema_version == RUN_TRACE_SCHEMA_VERSION:
        return _validated_dump(trace)
    if schema_version != 7:
        raise TraceSchemaError(
            f"unsupported trace schema version {schema_version!r}; "
            f"supported versions are {sorted(SUPPORTED_TRACE_SCHEMA_VERSIONS)}"
        )

    migrated = deepcopy(dict(trace))
    evaluation = migrated.get("evaluation")
    source_sha256 = evaluation.get("agent_source_sha256") if isinstance(evaluation, dict) else None
    if not isinstance(source_sha256, str):
        source_sha256 = "0" * 64
    created_at = _first_step_timestamp(migrated.get("steps"))
    migrated.update(
        {
            "schema_version": RUN_TRACE_SCHEMA_VERSION,
            "$schema": RUN_TRACE_SCHEMA_ID,
            "producer": {
                "name": "lixiuyin-webagent",
                "version": "0+legacy-v7",
                "source_sha256": source_sha256,
            },
            "created_at": created_at,
        }
    )
    if isinstance(evaluation, dict):
        evaluation = dict(evaluation)
        if evaluation.get("anti_shortcut_contract") == "search_engine_only_v7":
            evaluation["anti_shortcut_contract"] = "search_engine_only_v8"
        migrated["evaluation"] = evaluation
    return _validated_dump(migrated)


def validate_run_trace_v8(trace: Mapping[str, Any]) -> RunTraceV8:
    """Return a typed v8 trace or raise a stable domain error."""
    try:
        return RunTraceV8.model_validate(trace)
    except ValidationError as exc:
        raise TraceSchemaError(f"invalid v8 trace: {exc}") from exc


def _validated_dump(trace: Mapping[str, Any]) -> dict[str, Any]:
    return validate_run_trace_v8(trace).model_dump(mode="json", by_alias=True)


def _first_step_timestamp(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for step in value:
        timestamp = step.get("timestamp") if isinstance(step, dict) else None
        if isinstance(timestamp, str):
            return timestamp
    return None


__all__ = [
    "RUN_TRACE_SCHEMA_ID",
    "RUN_TRACE_SCHEMA_VERSION",
    "SUPPORTED_TRACE_SCHEMA_VERSIONS",
    "RunTraceV8",
    "TraceEvaluationV8",
    "TraceProducerV8",
    "TraceSchemaError",
    "TraceStepV8",
    "build_run_trace_v8",
    "migrate_trace_to_v8",
    "validate_run_trace_v8",
]

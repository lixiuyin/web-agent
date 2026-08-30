"""Typed schemas for web-interaction benchmark tasks and results."""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from webagent.evaluation.calibration import CalibrationAnalysis
from webagent.evaluation.failures import FailureAnalysis
from webagent.evaluation.generality import GeneralityAnalysis
from webagent.evaluation.long_horizon import LongHorizonAnalysis, TrajectoryDiagnostics
from webagent.evaluation.transfer import TransferAnalysis

AssertionKind = Literal[
    "url_equals",
    "url_contains",
    "text_contains",
    "element_text_equals",
    "element_value_equals",
    "element_checked",
    "element_visible",
    "attribute_equals",
    "json_equals",
    "answer_contains",
    "answer_date",
    "answer_labeled_date",
    "answer_not_contains",
    "answer_regex",
    "answer_in_order",
    "history_url_observed",
    "history_origin_observed",
    "history_tool_succeeded",
    "history_tool_sequence",
    "artifact_exists",
    "artifact_sha256",
    "certificate_valid",
]

ScenarioKind = Literal[
    "document_read",
    "search_discovery",
    "general_interaction",
    "spa_interaction",
    "authenticated_session",
    "cross_origin_form",
    "file_workflow",
    "sandbox_transaction",
    "recovery",
]
EnvironmentKind = Literal["public_web", "sandbox"]
EntryMode = Literal["direct", "search", "authenticated"]
RiskScope = Literal["read_only", "sandbox_mutation"]
ResearchSplit = Literal[
    "development",
    "validation",
    "held_out_task",
    "held_out_setting",
]
FeedbackKind = Literal[
    "unspecified",
    "verifiable",
    "partial",
    "delayed",
    "noisy",
    "absent",
]
ExpectedHorizon = Literal["unspecified", "short", "medium", "long"]


class FeedbackSpec(BaseModel):
    """Feedback conditions declared before a task is run.

    ``unspecified`` is intentional for legacy manifests: compatibility must not
    silently turn an old task into evidence about perfect or verifiable feedback.
    """

    kind: FeedbackKind = "unspecified"
    delay_steps: int = Field(default=0, ge=0)
    noise_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_feedback_parameters(self) -> FeedbackSpec:
        if self.kind == "delayed" and self.delay_steps < 1:
            raise ValueError("delayed feedback requires delay_steps >= 1")
        if self.kind == "noisy" and self.noise_rate is None:
            raise ValueError("noisy feedback requires noise_rate")
        if self.kind != "noisy" and self.noise_rate is not None:
            raise ValueError("noise_rate is only valid for noisy feedback")
        return self


def _validate_answer_date_expectation(kind: AssertionKind, expected: Any) -> None:
    if kind == "answer_date":
        if not isinstance(expected, str):
            raise ValueError("answer_date requires an ISO date string")
        try:
            date.fromisoformat(expected)
        except ValueError as exc:
            raise ValueError("answer_date requires an ISO date string") from exc
    if kind == "answer_labeled_date":
        if not isinstance(expected, dict):
            raise ValueError("answer_labeled_date requires label/date fields")
        label = expected.get("label")
        expected_date = expected.get("date")
        if not isinstance(label, str) or not label.strip() or not isinstance(expected_date, str):
            raise ValueError("answer_labeled_date requires label/date fields")
        try:
            date.fromisoformat(expected_date)
        except ValueError as exc:
            raise ValueError("answer_labeled_date requires an ISO date") from exc


def _validate_artifact_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("artifact path must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact path must stay below the task artifacts directory")
    return value


class BenchmarkAssertion(BaseModel):
    """One independently scored condition on browser or server terminal state."""

    kind: AssertionKind
    expected: Any = None
    selector: str | None = None
    attribute: str | None = None
    endpoint: str | None = None
    json_path: str | None = None
    required: bool = True
    weight: float = Field(default=1.0, gt=0)
    description: str = ""

    @model_validator(mode="after")
    def validate_kind_fields(self) -> BenchmarkAssertion:
        if self.kind.startswith("element_") and not self.selector:
            raise ValueError(f"{self.kind} requires selector")
        if self.kind == "attribute_equals" and (not self.selector or not self.attribute):
            raise ValueError("attribute_equals requires selector and attribute")
        if self.kind == "json_equals" and (not self.endpoint or not self.json_path):
            raise ValueError("json_equals requires endpoint and json_path")
        if self.kind == "history_url_observed" and not isinstance(self.expected, str):
            raise ValueError("history_url_observed requires a string expected URL/prefix")
        if self.kind == "history_origin_observed" and not isinstance(self.expected, str):
            raise ValueError("history_origin_observed requires a string origin")
        if self.kind == "history_tool_succeeded" and not isinstance(self.expected, str):
            raise ValueError("history_tool_succeeded requires a tool name")
        if self.kind == "history_tool_sequence" and (
            not isinstance(self.expected, list)
            or not self.expected
            or not all(isinstance(item, str) and item for item in self.expected)
        ):
            raise ValueError("history_tool_sequence requires a non-empty tool-name list")
        if self.kind == "answer_in_order" and (
            not isinstance(self.expected, list)
            or not self.expected
            or not all(isinstance(item, str) and item.strip() for item in self.expected)
        ):
            raise ValueError("answer_in_order requires a non-empty string list")
        if self.kind == "artifact_exists":
            _validate_artifact_path(self.expected)
        if self.kind == "artifact_sha256":
            if not isinstance(self.expected, dict):
                raise ValueError("artifact_sha256 requires path and sha256 fields")
            _validate_artifact_path(self.expected.get("path"))
            digest = self.expected.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("artifact_sha256 requires a 64-character sha256")
        _validate_answer_date_expectation(self.kind, self.expected)
        return self


class BenchmarkTask(BaseModel):
    """A reproducible task whose success is determined outside the agent."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    category: str
    goal: str
    start_url: str
    assertions: list[BenchmarkAssertion] = Field(min_length=1)
    max_steps: int = Field(default=20, ge=1, le=200)
    tags: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    network_required: bool = False
    discovery_required: bool = False
    scenario: ScenarioKind = "document_read"
    environment: EnvironmentKind = "public_web"
    entry_mode: EntryMode = "direct"
    risk_scope: RiskScope = "read_only"
    split: ResearchSplit = "development"
    task_family: str | None = None
    setting_id: str | None = None
    leakage_group: str | None = None
    target_failure_modes: list[str] = Field(default_factory=list)
    feedback: FeedbackSpec = Field(default_factory=FeedbackSpec)
    expected_horizon: ExpectedHorizon = "unspecified"

    @model_validator(mode="after")
    def validate_snapshot_metadata(self) -> BenchmarkTask:
        self._normalize_discovery_metadata()
        self._validate_discovery_contract()
        self._validate_network_contract()
        self._validate_validity_window()
        self._validate_safety_contract()
        self._normalize_research_metadata()
        return self

    def _normalize_research_metadata(self) -> None:
        """Give legacy manifests deterministic, non-claiming research metadata."""
        self.task_family = self._research_value(self.task_family, self.category, "task_family")
        default_setting = self.snapshot_id or self.environment
        self.setting_id = self._research_value(self.setting_id, default_setting, "setting_id")
        self.leakage_group = self._research_value(self.leakage_group, self.id, "leakage_group")
        normalized_modes: list[str] = []
        for mode in self.target_failure_modes:
            value = mode.strip()
            if not value:
                raise ValueError("target_failure_modes cannot contain empty values")
            if value not in normalized_modes:
                normalized_modes.append(value)
        self.target_failure_modes = normalized_modes

    @staticmethod
    def _research_value(value: str | None, default: str, field_name: str) -> str:
        if value is None:
            return default
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must be non-empty")
        return normalized

    def _normalize_discovery_metadata(self) -> None:
        """Preserve v1 manifest compatibility while exposing typed v2 metadata."""
        if self.discovery_required:
            if self.entry_mode == "direct":
                self.entry_mode = "search"
            if self.scenario == "document_read":
                self.scenario = "search_discovery"

    def _validate_discovery_contract(self) -> None:
        if self.discovery_required:
            if not self.network_required:
                raise ValueError("discovery_required tasks must also set network_required")
            if self.start_url != "about:blank":
                raise ValueError("discovery_required tasks must start from about:blank")
            if not any(assertion.kind == "certificate_valid" for assertion in self.assertions):
                raise ValueError("discovery_required tasks must assert certificate_valid")
        if self.entry_mode == "search" and not self.discovery_required:
            raise ValueError("search entry_mode requires discovery_required")

    def _validate_safety_contract(self) -> None:
        if self.risk_scope == "sandbox_mutation" and self.environment != "sandbox":
            raise ValueError("sandbox_mutation risk scope requires a sandbox environment")
        if self.scenario == "sandbox_transaction" and self.risk_scope != "sandbox_mutation":
            raise ValueError("sandbox_transaction tasks require sandbox_mutation risk scope")
        if self.entry_mode == "authenticated" and self.scenario != "authenticated_session":
            raise ValueError("authenticated entry mode requires authenticated_session scenario")

    def _validate_network_contract(self) -> None:
        if self.network_required:
            if not self.source_urls:
                raise ValueError("network_required tasks must declare source_urls")
            if not self.snapshot_id or not self.valid_from or not self.valid_until:
                raise ValueError(
                    "network_required tasks must declare snapshot_id, valid_from, and valid_until"
                )
            observed_sources = {
                str(assertion.expected)
                for assertion in self.assertions
                if assertion.kind == "history_url_observed"
            }
            cited_sources = {
                str(assertion.expected)
                for assertion in self.assertions
                if assertion.kind == "answer_contains"
            }
            if not any(source in observed_sources for source in self.source_urls):
                raise ValueError(
                    "network_required tasks must assert that a declared source URL was observed"
                )
            if not any(source in cited_sources for source in self.source_urls):
                raise ValueError(
                    "network_required tasks must require a declared source URL in the answer"
                )

    def _validate_validity_window(self) -> None:
        for value in (self.valid_from, self.valid_until):
            if value is not None:
                date.fromisoformat(value)
        if self.valid_from and self.valid_until and self.valid_from > self.valid_until:
            raise ValueError("valid_from must not be after valid_until")


class AssertionOutcome(BaseModel):
    """Observed value and pass/fail decision for one assertion."""

    assertion: BenchmarkAssertion
    passed: bool
    observed: Any = None
    error: str | None = None


class TaskEvaluation(BaseModel):
    """Environment-grounded result for one agent run."""

    task_id: str
    category: str
    goal: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    agent_reported_success: bool
    agent_status: str
    error: str | None = None
    duration_seconds: float = Field(ge=0.0)
    steps: int = Field(ge=0)
    action_count: int = Field(ge=0)
    failed_action_count: int = Field(ge=0)
    planner_attempt_count: int = Field(ge=0)
    planner_failure_count: int = Field(ge=0)
    planner_tokens: int = Field(ge=0)
    answer_assertion_count: int = Field(default=0, ge=0)
    answer_assertion_passed: int = Field(default=0, ge=0)
    termination_reason: str = "unknown"
    timed_out: bool = False
    captcha_encountered: bool = False
    blocked: bool = False
    max_steps_reached: bool = False
    split: ResearchSplit = "development"
    task_family: str | None = None
    setting_id: str | None = None
    leakage_group: str | None = None
    target_failure_modes: list[str] = Field(default_factory=list)
    feedback: FeedbackSpec = Field(default_factory=FeedbackSpec)
    expected_horizon: ExpectedHorizon = "unspecified"
    scenario: ScenarioKind = "document_read"
    environment: EnvironmentKind = "public_web"
    entry_mode: EntryMode = "direct"
    risk_scope: RiskScope = "read_only"
    source_origins: list[str] = Field(default_factory=list)
    trajectory: TrajectoryDiagnostics | None = None
    success_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_source: Literal["self_reported"] | None = None
    confidence_elicited_at_step: int | None = Field(default=None, ge=1)
    assertions: list[AssertionOutcome]

    @model_validator(mode="after")
    def normalize_research_result(self) -> TaskEvaluation:
        self.task_family = self.task_family or self.category
        self.setting_id = self.setting_id or "legacy-unspecified"
        self.leakage_group = self.leakage_group or self.task_id
        if self.success_probability is not None and self.confidence_source is None:
            self.confidence_source = "self_reported"
        if self.success_probability is None and self.confidence_source is not None:
            raise ValueError("confidence_source requires success_probability")
        if self.success_probability is None and self.confidence_elicited_at_step is not None:
            raise ValueError("confidence_elicited_at_step requires success_probability")
        return self


class BenchmarkSummary(BaseModel):
    """Aggregate metrics that separate claimed completion from actual success."""

    task_count: int = Field(ge=0)
    passed_tasks: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    mean_score: float = Field(ge=0.0, le=1.0)
    agent_completion_rate: float = Field(ge=0.0, le=1.0)
    false_completion_rate: float = Field(ge=0.0, le=1.0)
    action_validity_rate: float = Field(ge=0.0, le=1.0)
    mean_steps: float = Field(ge=0.0)
    mean_duration_seconds: float = Field(ge=0.0)
    total_planner_tokens: int = Field(ge=0)
    mean_planner_tokens: float = Field(ge=0.0)
    answer_grounding_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    timeout_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    captcha_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    blocked_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_steps_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    p95_duration_seconds: float = Field(default=0.0, ge=0.0)
    p95_steps: float = Field(default=0.0, ge=0.0)
    p95_planner_tokens: float = Field(default=0.0, ge=0.0)
    termination_reason_counts: dict[str, int] = Field(default_factory=dict)
    category_success_rate: dict[str, float]


class ResearchAnalyses(BaseModel):
    """Failure, calibration, and held-out diagnostics emitted together."""

    failures: FailureAnalysis
    calibration: CalibrationAnalysis
    transfer: TransferAnalysis
    generality: GeneralityAnalysis
    long_horizon: LongHorizonAnalysis


class BenchmarkReport(BaseModel):
    """Serializable report emitted by :class:`BenchmarkRunner`."""

    schema_version: int = 4
    suite: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary: BenchmarkSummary
    tasks: list[TaskEvaluation]
    research: ResearchAnalyses | None = None

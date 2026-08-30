"""Evidence-bounded failure taxonomy for benchmark evaluations.

Automatic analysis records directly observed symptoms separately from diagnostic
candidates.  In particular, aggregate benchmark records cannot establish a
reasoning or memory cause, so this module never auto-assigns those causal labels.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from webagent.evaluation.models import TaskEvaluation

FailureEvidenceStatus = Literal["observed", "candidate", "adjudicated"]
FailureLayer = Literal[
    "planning",
    "reasoning",
    "memory_context",
    "tool_selection",
    "tool_execution",
    "answer_grounding",
    "execution_control",
    "policy",
    "environment",
    "feedback",
    "unknown",
]
FailureEvidenceSource = Literal[
    "task_metric",
    "assertion",
    "terminal_status",
    "trace",
    "human_adjudication",
    "controlled_intervention",
]


class FailureEvidence(BaseModel):
    """One metric or judge outcome supporting a failure finding."""

    source: FailureEvidenceSource
    key: str
    observed: Any = None
    reference: str | None = None

    @model_validator(mode="after")
    def require_research_evidence_reference(self) -> FailureEvidence:
        if self.source in {"trace", "human_adjudication", "controlled_intervention"} and not (
            self.reference and self.reference.strip()
        ):
            raise ValueError(f"{self.source} evidence requires a stable reference")
        return self


class FailureFinding(BaseModel):
    """An observed symptom or explicitly non-causal diagnostic candidate."""

    taxonomy_version: Literal["1"] = "1"
    task_id: str
    status: FailureEvidenceStatus
    layer: FailureLayer
    subtype: str
    detector: str = Field(min_length=1)
    terminal: bool = False
    onset_step: int | None = Field(default=None, ge=1)
    recovery_step: int | None = Field(default=None, ge=1)
    evidence: list[FailureEvidence] = Field(min_length=1)
    note: str

    @model_validator(mode="after")
    def validate_evidence_boundary(self) -> FailureFinding:
        if self.recovery_step is not None:
            if self.onset_step is None:
                raise ValueError("recovery_step requires onset_step")
            if self.recovery_step < self.onset_step:
                raise ValueError("recovery_step cannot precede onset_step")
        if self.status != "adjudicated":
            return self
        if self.layer == "unknown":
            raise ValueError("adjudicated failures require a specific failure layer")
        sources = {item.source for item in self.evidence}
        controlled = "controlled_intervention" in sources
        trace_and_human = {"trace", "human_adjudication"}.issubset(sources)
        if not controlled and not trace_and_human:
            raise ValueError(
                "adjudicated failures require controlled-intervention evidence or both trace and "
                "human-adjudication evidence"
            )
        return self


class FailureAnalysis(BaseModel):
    """Suite-level failure findings with an explicit causal-inference boundary."""

    schema_version: int = 2
    failure_taxonomy_version: Literal["1"] = "1"
    task_count: int = Field(ge=0)
    affected_task_count: int = Field(ge=0)
    finding_count: int = Field(ge=0)
    observed_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    adjudicated_count: int = Field(ge=0)
    observed_by_layer: dict[str, int] = Field(default_factory=dict)
    candidate_by_layer: dict[str, int] = Field(default_factory=dict)
    adjudicated_by_layer: dict[str, int] = Field(default_factory=dict)
    recurrence_by_signature: dict[str, int] = Field(default_factory=dict)
    findings: list[FailureFinding] = Field(default_factory=list)
    causal_boundary: str = (
        "Automatic findings identify observable symptoms only; reasoning and memory causes are "
        "never assigned automatically. Adjudicated causal labels require a referenced controlled "
        "intervention or both referenced trace evidence and human adjudication."
    )


def analyze_failures(evaluations: Sequence[TaskEvaluation]) -> FailureAnalysis:
    """Extract direct symptoms and bounded diagnostic candidates."""
    findings: list[FailureFinding] = []
    for evaluation in evaluations:
        findings.extend(_observed_findings(evaluation))
        findings.extend(_candidate_findings(evaluation))

    return _analysis_from_findings(task_count=len(evaluations), findings=findings)


def merge_adjudicated_findings(
    analysis: FailureAnalysis,
    findings: Sequence[FailureFinding],
) -> FailureAnalysis:
    """Return a new analysis containing explicitly adjudicated research findings."""
    additions = list(findings)
    if any(item.status != "adjudicated" for item in additions):
        raise ValueError("only adjudicated findings may be merged through this research boundary")
    return _analysis_from_findings(
        task_count=analysis.task_count,
        findings=[*analysis.findings, *additions],
    )


def _analysis_from_findings(
    *, task_count: int, findings: Sequence[FailureFinding]
) -> FailureAnalysis:
    retained = list(findings)
    observed = [item for item in retained if item.status == "observed"]
    candidates = [item for item in retained if item.status == "candidate"]
    adjudicated = [item for item in retained if item.status == "adjudicated"]
    return FailureAnalysis(
        task_count=task_count,
        affected_task_count=len({item.task_id for item in retained}),
        finding_count=len(retained),
        observed_count=len(observed),
        candidate_count=len(candidates),
        adjudicated_count=len(adjudicated),
        observed_by_layer=dict(sorted(Counter(item.layer for item in observed).items())),
        candidate_by_layer=dict(sorted(Counter(item.layer for item in candidates).items())),
        adjudicated_by_layer=dict(sorted(Counter(item.layer for item in adjudicated).items())),
        recurrence_by_signature=dict(
            sorted(Counter(f"{item.layer}:{item.subtype}" for item in retained).items())
        ),
        findings=retained,
    )


def _finding(
    evaluation: TaskEvaluation,
    *,
    status: FailureEvidenceStatus,
    layer: FailureLayer,
    subtype: str,
    source: Literal["task_metric", "assertion", "terminal_status"],
    key: str,
    observed: Any,
    note: str,
    terminal: bool = False,
) -> FailureFinding:
    return FailureFinding(
        task_id=evaluation.task_id,
        status=status,
        layer=layer,
        subtype=subtype,
        detector="aggregate-rule-v1",
        terminal=terminal,
        evidence=[FailureEvidence(source=source, key=key, observed=observed)],
        note=note,
    )


def _observed_findings(evaluation: TaskEvaluation) -> list[FailureFinding]:
    findings: list[FailureFinding] = []
    if evaluation.planner_failure_count:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="planning",
                subtype="planner_attempt_failure",
                source="task_metric",
                key="planner_failure_count",
                observed=evaluation.planner_failure_count,
                note="One or more planner attempts failed; the underlying cause is not inferred.",
            )
        )
    if evaluation.failed_action_count:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="tool_execution",
                subtype="tool_action_failure",
                source="task_metric",
                key="failed_action_count",
                observed=evaluation.failed_action_count,
                note="The tool result directly reported one or more failed actions.",
            )
        )
    if evaluation.timed_out:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="execution_control",
                subtype="timeout",
                source="terminal_status",
                key="termination_reason",
                observed=evaluation.termination_reason,
                note="The run terminated at its time boundary.",
                terminal=True,
            )
        )
    if evaluation.max_steps_reached:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="execution_control",
                subtype="step_budget_exhausted",
                source="terminal_status",
                key="termination_reason",
                observed=evaluation.termination_reason,
                note="The run exhausted its configured step budget.",
                terminal=True,
            )
        )
    if evaluation.blocked:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="policy",
                subtype="blocked_termination",
                source="terminal_status",
                key="termination_reason",
                observed=evaluation.termination_reason,
                note="The terminal status was blocked; policy versus environment cause is unresolved.",
                terminal=True,
            )
        )
    if evaluation.captcha_encountered:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="environment",
                subtype="captcha_encountered",
                source="task_metric",
                key="captcha_encountered",
                observed=True,
                note="A challenge was observed, whether or not the task later recovered.",
            )
        )
    failed_assertions = sum(not outcome.passed for outcome in evaluation.assertions)
    if failed_assertions:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="answer_grounding",
                subtype="judge_assertion_failure",
                source="assertion",
                key="failed_assertion_count",
                observed=failed_assertions,
                note="The external judge observed unmet conditions; this does not identify a cause.",
                terminal=not evaluation.passed,
            )
        )
    if evaluation.agent_reported_success and not evaluation.passed:
        findings.append(
            _finding(
                evaluation,
                status="observed",
                layer="execution_control",
                subtype="false_completion",
                source="task_metric",
                key="agent_reported_success",
                observed=True,
                note="The agent declared completion while the external judge rejected the result.",
                terminal=True,
            )
        )
    return findings


def _candidate_findings(evaluation: TaskEvaluation) -> list[FailureFinding]:
    if evaluation.passed:
        return []
    if evaluation.failed_action_count:
        return [
            _finding(
                evaluation,
                status="candidate",
                layer="unknown",
                subtype="grounding_selection_or_page_state_candidate",
                source="task_metric",
                key="failed_action_count",
                observed=evaluation.failed_action_count,
                note=(
                    "Failed actions warrant trace inspection for grounding, tool selection, "
                    "parameters, or page-state drift; none is established here."
                ),
            )
        ]
    return [
        _finding(
            evaluation,
            status="candidate",
            layer="unknown",
            subtype="unresolved_task_failure",
            source="task_metric",
            key="passed",
            observed=False,
            note=(
                "No aggregate runtime symptom explains the failed task. Trace or human analysis "
                "is required before assigning reasoning, memory, or tool-use causes."
            ),
        )
    ]


__all__ = [
    "FailureAnalysis",
    "FailureEvidence",
    "FailureEvidenceSource",
    "FailureEvidenceStatus",
    "FailureFinding",
    "FailureLayer",
    "analyze_failures",
    "merge_adjudicated_findings",
]

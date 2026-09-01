"""Research-facing failure, calibration, and held-out transfer diagnostics."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from webagent.evaluation.calibration import analyze_calibration
from webagent.evaluation.failures import (
    FailureEvidence,
    FailureFinding,
    analyze_failures,
    merge_adjudicated_findings,
)
from webagent.evaluation.models import (
    AssertionOutcome,
    BenchmarkAssertion,
    BenchmarkTask,
    FeedbackSpec,
    TaskEvaluation,
)
from webagent.evaluation.studies import StudyRunRecord
from webagent.evaluation.transfer import analyze_intervention_transfer, analyze_transfer


def _evaluation(
    task_id: str,
    *,
    passed: bool,
    split: str = "development",
    probability: float | None = None,
    leakage_group: str | None = None,
    failed_actions: int = 0,
    planner_failures: int = 0,
    reported: bool | None = None,
) -> TaskEvaluation:
    assertion = BenchmarkAssertion(kind="url_contains", expected="/done")
    return TaskEvaluation(
        task_id=task_id,
        category="navigation",
        goal="finish",
        passed=passed,
        score=float(passed),
        agent_reported_success=passed if reported is None else reported,
        agent_status="completed" if passed else "failed",
        duration_seconds=1.0,
        steps=3,
        action_count=2,
        failed_action_count=failed_actions,
        planner_attempt_count=1,
        planner_failure_count=planner_failures,
        planner_tokens=10,
        split=split,  # type: ignore[arg-type]
        task_family="navigation",
        setting_id="site-v1",
        leakage_group=leakage_group or task_id,
        success_probability=probability,
        assertions=[AssertionOutcome(assertion=assertion, passed=passed)],
    )


def _study_record(
    task_id: str,
    *,
    split: str,
    condition: str,
    success: bool,
    study_id: str = "transfer-study",
) -> StudyRunRecord:
    return StudyRunRecord(
        study_id=study_id,
        task_id=task_id,
        split=split,  # type: ignore[arg-type]
        setting_id="setting-v1",
        provider="openrouter",
        model="provider/model-a",
        condition_id=condition,
        failure_taxonomy_version="1",
        collection_date=date(2026, 8, 30),
        repetition=1,
        run_path=(f"executions/2026-08-30/provider-model-a/{condition}/execution-1/runs/{task_id}"),
        report_path=(
            f"executions/2026-08-30/provider-model-a/{condition}/execution-1/runs/"
            f"{task_id}/evaluation/task.json"
        ),
        report_sha256="a" * 64,
        success=success,
    )


def test_legacy_task_gets_conservative_research_defaults() -> None:
    task = BenchmarkTask(
        id="legacy_task",
        category="navigation",
        goal="finish",
        start_url="https://example.test",
        assertions=[BenchmarkAssertion(kind="text_contains", expected="done")],
    )

    assert task.split == "development"
    assert task.task_family == "navigation"
    assert task.setting_id == "public_web"
    assert task.leakage_group == "legacy_task"
    assert task.feedback.kind == "unspecified"
    assert task.expected_horizon == "unspecified"


def test_explicit_research_metadata_is_validated_and_deduplicated() -> None:
    task = BenchmarkTask(
        id="held_out",
        category="form",
        goal="finish",
        start_url="https://example.test",
        assertions=[BenchmarkAssertion(kind="text_contains", expected="done")],
        split="held_out_setting",
        task_family="forms",
        setting_id="perturbed-dom-v2",
        leakage_group="form-template-7",
        target_failure_modes=["grounding", "grounding", "recovery"],
        feedback=FeedbackSpec(kind="delayed", delay_steps=2),
        expected_horizon="long",
    )

    assert task.target_failure_modes == ["grounding", "recovery"]
    with pytest.raises(ValidationError, match="noise_rate"):
        FeedbackSpec(kind="noisy")
    with pytest.raises(ValidationError, match="delay_steps"):
        FeedbackSpec(kind="delayed")


def test_failure_analysis_separates_observations_from_candidates() -> None:
    failed = _evaluation(
        "failure",
        passed=False,
        failed_actions=2,
        planner_failures=1,
        reported=True,
    )
    analysis = analyze_failures([failed])

    assert analysis.observed_count >= 4
    assert analysis.candidate_count == 1
    assert analysis.adjudicated_count == 0
    assert {item.status for item in analysis.findings} == {"observed", "candidate"}
    assert "tool_execution" in analysis.observed_by_layer
    assert all(item.layer not in {"reasoning", "memory"} for item in analysis.findings)
    assert "never assigned automatically" in analysis.causal_boundary


def test_failure_adjudication_requires_trace_plus_human_or_controlled_evidence() -> None:
    automatic = analyze_failures([_evaluation("memory-case", passed=False)])
    adjudicated = FailureFinding(
        task_id="memory-case",
        status="adjudicated",
        layer="memory_context",
        subtype="observed_then_unavailable_at_decision",
        detector="human-review-v1",
        onset_step=3,
        evidence=[
            FailureEvidence(
                source="trace",
                key="planner_visible_evidence",
                observed="missing",
                reference="trajectory/trace.json#steps/3",
            ),
            FailureEvidence(
                source="human_adjudication",
                key="label",
                observed="memory_context",
                reference="annotations/reviewer-a.json#memory-case",
            ),
        ],
        note="A reviewer linked the missing decision context to retained trajectory evidence.",
    )

    merged = merge_adjudicated_findings(automatic, [adjudicated])

    assert merged.adjudicated_count == 1
    assert merged.adjudicated_by_layer == {"memory_context": 1}
    assert (
        merged.recurrence_by_signature["memory_context:observed_then_unavailable_at_decision"] == 1
    )
    with pytest.raises(ValidationError, match="controlled-intervention evidence"):
        FailureFinding(
            task_id="unsupported-cause",
            status="adjudicated",
            layer="reasoning",
            subtype="unsupported",
            detector="human-review-v1",
            evidence=[
                FailureEvidence(
                    source="human_adjudication",
                    key="label",
                    observed="reasoning",
                    reference="annotations/reviewer-a.json#unsupported-cause",
                )
            ],
            note="Human opinion without retained trace evidence is insufficient.",
        )


def test_calibration_reports_missing_coverage_without_imputation() -> None:
    analysis = analyze_calibration(
        [
            _evaluation("a", passed=True, probability=0.8),
            _evaluation("b", passed=False, probability=0.4),
            _evaluation("c", passed=True),
        ]
    )

    assert analysis.status == "partial"
    assert analysis.reason == "missing confidence for 1 task(s)"
    assert analysis.confidence_count == 2
    assert analysis.confidence_coverage == pytest.approx(2 / 3)
    assert analysis.brier_score == pytest.approx(0.1)
    assert analysis.expected_calibration_error == pytest.approx(0.3)
    assert [point.coverage for point in analysis.risk_coverage_curve] == [0.5, 1.0]
    assert [point.selective_risk for point in analysis.risk_coverage_curve] == [0.0, 0.5]
    assert analysis.area_under_risk_coverage_curve == pytest.approx(0.25)

    missing = analyze_calibration([_evaluation("missing", passed=True)])
    assert missing.status == "unavailable"
    assert missing.reason == "no task-success confidence was recorded"
    assert missing.confidence_coverage == 0.0


def test_transfer_distinguishes_task_and_setting_holdouts() -> None:
    analysis = analyze_transfer(
        [
            _evaluation("dev-a", passed=True),
            _evaluation("dev-b", passed=False),
            _evaluation("task-holdout", passed=True, split="held_out_task"),
            _evaluation("setting-holdout", passed=False, split="held_out_setting"),
        ]
    )

    assert analysis.status == "available"
    assert analysis.held_out_task_success_delta == pytest.approx(0.5)
    assert analysis.held_out_setting_success_delta == pytest.approx(-0.5)
    assert analysis.pooled_held_out_success_delta == pytest.approx(0.0)
    assert "do not establish" in analysis.interpretation_notice


def test_transfer_returns_explicit_unavailable_reasons() -> None:
    no_holdout = analyze_transfer([_evaluation("dev", passed=True)])
    assert no_holdout.status == "unavailable"
    assert no_holdout.reason == "no held-out task or setting evaluations were recorded"

    leakage = analyze_transfer(
        [
            _evaluation("dev", passed=True, leakage_group="shared"),
            _evaluation(
                "holdout",
                passed=True,
                split="held_out_task",
                leakage_group="shared",
            ),
        ]
    )
    assert leakage.status == "unavailable"
    assert leakage.leakage_groups == ["shared"]
    assert "crosses development and held-out" in str(leakage.reason)


def test_intervention_transfer_uses_only_paired_study_cells() -> None:
    records = [
        _study_record("dev-a", split="development", condition="baseline", success=False),
        _study_record("dev-a", split="development", condition="memory-v2", success=True),
        _study_record("dev-b", split="development", condition="baseline", success=True),
        _study_record("dev-b", split="development", condition="memory-v2", success=True),
        _study_record("task-ho", split="held_out_task", condition="baseline", success=False),
        _study_record("task-ho", split="held_out_task", condition="memory-v2", success=True),
        _study_record("setting-ho", split="held_out_setting", condition="baseline", success=True),
        _study_record("setting-ho", split="held_out_setting", condition="memory-v2", success=True),
        # An unmatched intervention row must not enter either condition rate.
        _study_record("unpaired", split="development", condition="memory-v2", success=True),
    ]

    analysis = analyze_intervention_transfer(
        records,
        baseline_condition_id="baseline",
        intervention_condition_id="memory-v2",
    )

    assert analysis.status == "available"
    assert analysis.effects["development"].paired_count == 2
    assert analysis.effects["development"].success_delta == pytest.approx(0.5)
    assert analysis.effects["held_out_task"].success_delta == pytest.approx(1.0)
    assert analysis.effects["held_out_setting"].success_delta == pytest.approx(0.0)
    assert analysis.held_out_task_transfer_gap == pytest.approx(0.5)
    assert analysis.held_out_setting_transfer_gap == pytest.approx(-0.5)
    assert "descriptive" in analysis.interpretation_notice


def test_intervention_transfer_fails_closed_for_unpaired_or_mixed_studies() -> None:
    unpaired = analyze_intervention_transfer(
        [_study_record("dev", split="development", condition="baseline", success=True)],
        baseline_condition_id="baseline",
        intervention_condition_id="memory-v2",
    )
    assert unpaired.status == "unavailable"
    assert unpaired.effects["development"].reason == (
        "no matching task/model/date/repetition cells"
    )

    mixed = analyze_intervention_transfer(
        [
            _study_record("dev", split="development", condition="baseline", success=True),
            _study_record(
                "dev",
                split="development",
                condition="memory-v2",
                success=True,
                study_id="other-study",
            ),
        ],
        baseline_condition_id="baseline",
        intervention_condition_id="memory-v2",
    )
    assert mixed.status == "unavailable"
    assert mixed.reason == "records must belong to exactly one study"

    with pytest.raises(ValueError, match="must differ"):
        analyze_intervention_transfer(
            [], baseline_condition_id="same", intervention_condition_id="same"
        )

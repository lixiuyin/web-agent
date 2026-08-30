"""Research diagnostics for breadth, trajectory length, and portfolio completeness."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from benchmarks.environments.controlled_web.long_horizon_site import (
    CUE_SOURCE_STAGES,
    CUES,
    RECALLS,
    _stage_page,
)

from webagent.agent.loop import _checkpoint_planning_state
from webagent.agent.state import PlanningState
from webagent.core.models import AgentResult, AgentStep, BrowserState, ToolCall, ToolResult
from webagent.evaluation import (
    AssertionOutcome,
    BenchmarkAssertion,
    PortfolioInput,
    TaskEvaluation,
    TrajectoryDiagnostics,
    analyze_empirical_portfolio,
    analyze_generality,
    analyze_long_horizon,
    trajectory_diagnostics,
)
from webagent.tools.builtin.task_tools import RememberTool

_SCENARIOS = (
    "search_discovery",
    "spa_interaction",
    "authenticated_session",
    "cross_origin_form",
    "file_workflow",
    "sandbox_transaction",
    "recovery",
)


def test_long_horizon_recall_instructions_name_the_actual_cue_source_stage() -> None:
    for recall_stage, source_stage in CUE_SOURCE_STAGES.items():
        assert RECALLS[recall_stage] == CUES[source_stage]
        page = _stage_page(recall_stage).decode()
        assert f"introduced at stage {source_stage}" in page

    assert CUE_SOURCE_STAGES == {44: 4, 49: 14, 54: 24, 59: 34}


def _evaluation(
    index: int,
    *,
    scenario: str,
    actions: int = 3,
    passed: bool = True,
) -> TaskEvaluation:
    assertion = BenchmarkAssertion(kind="url_contains", expected="/done")
    trajectory = TrajectoryDiagnostics(
        action_count=actions,
        distinct_tool_count=2,
        tool_entropy_bits=1.0,
        repeated_action_rate=0.0,
        longest_identical_action_streak=1,
        longest_failure_streak=0,
        recovery_count=0,
        resume_count=1 if actions >= 50 else 0,
        resumed_from_checkpoint=actions >= 50,
    )
    return TaskEvaluation(
        task_id=f"task-{index:02d}",
        category=f"category-{index % 8}",
        goal="complete the verifiable task",
        passed=passed,
        score=float(passed),
        agent_reported_success=passed,
        agent_status="completed",
        duration_seconds=1.0,
        steps=actions + 1,
        action_count=actions,
        failed_action_count=0,
        planner_attempt_count=actions + 1,
        planner_failure_count=0,
        planner_tokens=100,
        split=(
            "held_out_task"
            if index % 10 == 8
            else "held_out_setting"
            if index % 10 == 9
            else "development"
        ),
        task_family=f"family-{index % 8}",
        setting_id=f"setting-{index}",
        leakage_group=f"leakage-{index}",
        scenario=scenario,  # type: ignore[arg-type]
        environment="public_web" if scenario == "search_discovery" else "sandbox",
        entry_mode="search" if scenario == "search_discovery" else "direct",
        risk_scope="sandbox_mutation" if scenario != "search_discovery" else "read_only",
        source_origins=[f"https://source-{index % 8}.example"],
        expected_horizon="long" if actions >= 50 else "short",
        trajectory=trajectory,
        assertions=[AssertionOutcome(assertion=assertion, passed=passed)],
    )


def _broad_tasks() -> list[TaskEvaluation]:
    tasks = [
        _evaluation(index, scenario=_SCENARIOS[index % len(_SCENARIOS)]) for index in range(36)
    ]
    tasks[-1] = _evaluation(35, scenario="recovery", actions=55)
    return tasks


def test_generality_requires_cross_setting_coverage() -> None:
    ready = analyze_generality(_broad_tasks())
    narrow = analyze_generality(
        [_evaluation(index, scenario="search_discovery") for index in range(30)]
    )

    assert ready.status == "ready"
    assert ready.source_origin_count == 8
    assert ready.discovery_task_count >= 5
    assert narrow.status == "insufficient"
    assert any("missing scenario coverage" in reason for reason in narrow.missing_requirements)
    assert any("both public_web and sandbox" in reason for reason in narrow.missing_requirements)


def test_long_horizon_reports_degradation_resume_and_collapse() -> None:
    short = _evaluation(1, scenario="search_discovery", passed=True)
    long = _evaluation(2, scenario="recovery", actions=55, passed=False)
    assert long.trajectory is not None
    long.trajectory = long.trajectory.model_copy(update={"collapse_onset_step": 31})

    analysis = analyze_long_horizon([short, long])

    assert analysis.status == "available"
    assert analysis.long_task_count == 1
    assert analysis.resumed_task_count == 1
    assert analysis.reliability_degradation == pytest.approx(1.0)
    assert analysis.collapse_incidence == pytest.approx(1.0)


def test_trajectory_diagnostics_detects_repetition_only_with_no_progress() -> None:
    state = BrowserState(
        screenshot=None,
        dom_summary="unchanged",
        url="https://example.test/stuck",
        title="Stuck",
        timestamp="now",
    )
    steps = [
        AgentStep(
            step_number=index,
            timestamp="now",
            browser_state=state,
            tool_call=ToolCall(tool_name="click", parameters={"selector": "#retry"}),
            tool_result=ToolResult(
                success=index == 6,
                tool_name="click",
                error=None if index == 6 else "not found",
            ),
            duration_seconds=0.1,
        )
        for index in range(1, 7)
    ]
    result = AgentResult(
        success=False,
        status="max_steps_reached",
        steps_taken=6,
        total_duration=1.0,
        history=steps,
        events=[{"type": "run_resumed", "resume_count": 2}],
    )

    metrics = trajectory_diagnostics(result)

    assert metrics.collapse_onset_step == 1
    assert metrics.longest_identical_action_streak == 6
    assert metrics.longest_failure_streak == 5
    assert metrics.recovery_count == 1
    assert metrics.resume_count == 2


def test_trajectory_diagnostics_detects_state_stagnation_when_tools_vary() -> None:
    state = BrowserState(
        screenshot=None,
        dom_summary="same form",
        url="https://example.test/recall",
        title="Recall",
        timestamp="now",
    )
    names = ("type", "click", "get_all_links", "back") * 2
    steps = [
        AgentStep(
            step_number=index,
            timestamp="now",
            browser_state=state,
            tool_call=ToolCall(tool_name=name, parameters={"attempt": index}),
            tool_result=ToolResult(success=True, tool_name=name),
            duration_seconds=0.1,
        )
        for index, name in enumerate(names, start=11)
    ]
    result = AgentResult(
        success=False,
        status="max_steps_reached",
        steps_taken=8,
        total_duration=1.0,
        history=steps,
        events=[
            {"type": "replan"},
            {"type": "replan"},
            {"type": "strategy_switch"},
        ],
    )

    metrics = trajectory_diagnostics(result)

    assert metrics.collapse_onset_step is None
    assert metrics.stagnation_onset_step == 11
    assert metrics.longest_same_state_streak == 8
    assert metrics.replan_count == 2
    assert metrics.strategy_switch_count == 1
    assert metrics.replan_rate == pytest.approx(0.25)


def test_empirical_portfolio_requires_complete_common_model_date_cells() -> None:
    runs = []
    for model in ("model-a", "model-b"):
        for day in (date(2026, 8, 28), date(2026, 8, 29), date(2026, 8, 30)):
            tasks = _broad_tasks()
            evidence = PortfolioInput(
                path=f"/{model}/{day.isoformat()}/results.json",
                sha256="a" * 64,
                run_id=f"{model}-{day.isoformat()}",
                suite="combined-agent-evidence-v1",
                date=day.isoformat(),
                provider="openrouter",
                model=model,
                task_count=len(tasks),
            )
            # Three complementary reports are required per cell; split the task
            # landscape without duplicating task identities.
            for suffix, chunk in zip(
                ("open", "sandbox", "long"), (tasks[:18], tasks[18:35], tasks[35:]), strict=True
            ):
                runs.append(
                    (
                        evidence.model_copy(
                            update={
                                "suite": suffix,
                                "run_id": f"{evidence.run_id}-{suffix}",
                                "task_count": len(chunk),
                            }
                        ),
                        chunk,
                    )
                )

    report = analyze_empirical_portfolio(runs)

    assert report.status == "ready"
    assert report.endpoint_count == 2
    assert report.common_complete_dates == ["2026-08-28", "2026-08-29", "2026-08-30"]
    assert all(cell.ready for cell in report.cells)
    assert all(cell.failures.task_count == 36 for cell in report.cells)
    assert all(
        cell.transfer is not None and cell.transfer.status == "available" for cell in report.cells
    )


def test_empirical_portfolio_excludes_transport_unavailable_endpoint() -> None:
    runs = []
    for model in ("model-a", "model-b"):
        for day in ("2026-08-28", "2026-08-29", "2026-08-30"):
            tasks = _broad_tasks()
            for suffix, chunk in zip(
                ("open", "sandbox", "long"), (tasks[:18], tasks[18:35], tasks[35:]), strict=True
            ):
                runs.append(
                    (
                        PortfolioInput(
                            path=f"/{model}/{day}/{suffix}.json",
                            sha256="a" * 64,
                            run_id=f"{model}-{day}-{suffix}",
                            suite=suffix,
                            date=day,
                            provider="openrouter",
                            model=model,
                            task_count=len(chunk),
                        ),
                        chunk,
                    )
                )

    unavailable = [
        item.model_copy(
            update={
                "action_count": 0,
                "failed_action_count": 0,
                "planner_attempt_count": 2,
                "planner_failure_count": 2,
                "planner_tokens": 0,
                "passed": False,
                "score": 0.0,
                "agent_reported_success": False,
                "agent_status": "failed",
            }
        )
        for item in _broad_tasks()[:3]
    ]
    for suffix, task in zip(("open", "sandbox", "long"), unavailable, strict=True):
        runs.append(
            (
                PortfolioInput(
                    path=f"/unavailable/2026-08-30/{suffix}.json",
                    sha256="b" * 64,
                    run_id=f"unavailable-2026-08-30-{suffix}",
                    suite=suffix,
                    date="2026-08-30",
                    provider="openrouter",
                    model="model-unavailable",
                    task_count=1,
                ),
                [task],
            )
        )

    report = analyze_empirical_portfolio(runs)

    assert report.status == "ready"
    assert report.requested_endpoint_count == 3
    assert report.endpoint_count == 2
    assert report.excluded_endpoints == ["openrouter::model-unavailable"]
    assert report.overall_success_rate == 1.0
    excluded = next(cell for cell in report.cells if cell.model == "model-unavailable")
    assert excluded.endpoint_status == "unavailable"
    assert excluded.success_rate is None
    assert excluded.transfer is None
    assert excluded.ready is False
    assert "planner endpoint unavailable" in excluded.reasons[0]


async def test_durable_memory_survives_checkpoint_but_rejects_sensitive_notes(
    tmp_path: Path,
) -> None:
    tool = RememberTool()
    result = await tool.execute({"note": "Mission cue 14: ORBIT"})
    state = PlanningState.create("complete mission", ["retain cues", "finish"])
    state = state.record_evidence(
        step_number=14,
        kind="durable_note",
        summary=str(result.data["note"]),
    )

    checkpoint_state = _checkpoint_planning_state(state, tmp_path)

    assert checkpoint_state is not None
    assert checkpoint_state.evidence[0].summary == "Mission cue 14: ORBIT"
    with pytest.raises(ValueError, match="cannot contain"):
        tool.validate_params({"note": "password: orbit42"})

"""Controller-owned planning state tests."""

from pydantic import ValidationError

from webagent.agent.state import PlanMilestone, PlanningState


def test_planning_state_advances_records_evidence_and_revises() -> None:
    state = PlanningState.create("Find and compare reports", ["discover", "verify", "answer"])
    state = state.record_evidence(
        step_number=2,
        summary="Official repository exposes report.pdf",
        source="https://example.test/repo",
    )
    # Durable evidence is deduplicated rather than growing the prompt forever.
    state = state.record_evidence(
        step_number=3,
        summary="Official repository exposes report.pdf",
        source="https://example.test/repo",
    )
    state = state.complete_active(step_number=3)
    revised = state.revise(
        step_number=4,
        reason="candidate date was unknown",
        strategy="search-discovery",
        milestone_descriptions=["inspect file history", "compare explicit dates"],
    )

    assert len(revised.evidence) == 1
    assert revised.milestones[0].status == "completed"
    assert revised.milestones[1].status == "abandoned"
    assert revised.active_milestone_id == "r1-m1"
    assert revised.revisions[0].reason == "candidate date was unknown"
    assert "RECENT EVIDENCE" in revised.prompt_summary()
    assert "Official repository" in revised.prompt_summary()


def test_prompt_summary_reserves_budget_for_durable_notes() -> None:
    state = PlanningState.create("Recall retained cues", ["collect", "recall"])
    for step, cue in ((4, "CEDAR"), (14, "ORBIT"), (24, "LANTERN"), (34, "DELTA")):
        state = state.record_evidence(
            step_number=step,
            kind="durable_note",
            summary=f"Mission cue: {cue}",
        )
    for step in range(35, 60):
        state = state.record_evidence(
            step_number=step,
            kind="tool_result",
            summary=f"ordinary observation {step}",
        )

    summary = state.prompt_summary(max_evidence=8, max_durable_notes=4)

    assert "DURABLE NOTES" in summary
    assert all(cue in summary for cue in ("CEDAR", "ORBIT", "LANTERN", "DELTA"))
    assert "ordinary observation 59" in summary
    assert "ordinary observation 54" not in summary


def test_prompt_summary_rejects_negative_limits() -> None:
    state = PlanningState.create("Task", ["act"])

    try:
        state.prompt_summary(max_evidence=-1)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("negative prompt evidence budget was accepted")


def test_planning_state_round_trips_and_rejects_dangling_links() -> None:
    state = PlanningState.create("Task", ["one"])
    assert PlanningState.model_validate_json(state.model_dump_json()) == state

    try:
        PlanningState(
            objective="Task",
            milestones=(PlanMilestone(id="m1", description="one"),),
            active_milestone_id="missing",
        )
    except ValidationError as exc:
        assert "active_milestone_id" in str(exc)
    else:  # pragma: no cover - assertion branch
        raise AssertionError("dangling milestone link was accepted")

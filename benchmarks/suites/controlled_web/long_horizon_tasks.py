"""Task definitions for the controlled long-horizon mission."""

from webagent.evaluation import BenchmarkAssertion, BenchmarkTask, FeedbackSpec


def build_long_horizon_tasks(base_url: str) -> list[BenchmarkTask]:
    return [
        BenchmarkTask(
            id="mission_60_stage_resume",
            category="long_horizon_workflow",
            goal=(
                "Complete all 60 mission stages, recover from transient feedback, retain each "
                "durable cue until it is requested, and finally report the four cues in order."
            ),
            start_url=f"{base_url}/mission",
            max_steps=100,
            scenario="recovery",
            environment="sandbox",
            risk_scope="sandbox_mutation",
            split="held_out_setting",
            task_family="long_horizon_state_machine",
            setting_id="controlled-mission-v1",
            leakage_group="mission-template-v1",
            target_failure_modes=[
                "context_loss",
                "memory_drift",
                "tool_loop",
                "checkpoint_resume",
                "transient_feedback",
            ],
            feedback=FeedbackSpec(kind="delayed", delay_steps=40),
            expected_horizon="long",
            assertions=[
                BenchmarkAssertion(
                    kind="element_visible", selector="#mission-complete", expected=True
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="completed",
                    expected=True,
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="stage",
                    expected=60,
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="memory_errors",
                    expected=0,
                    weight=2.0,
                ),
                BenchmarkAssertion(
                    kind="answer_regex",
                    expected=r"CEDAR\W+ORBIT\W+LANTERN\W+DELTA",
                    weight=2.0,
                ),
            ],
            tags=["60-stage", "resume", "delayed-feedback", "memory", "recovery"],
        )
    ]


__all__ = ["build_long_horizon_tasks"]

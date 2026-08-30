"""Bounded strategy switching and replan trigger tests."""

from webagent.agent.strategy import StrategyManager, StrategyObservation, StrategyState


def _observation(**overrides: object) -> StrategyObservation:
    values: dict[str, object] = {
        "tool_name": "click",
        "success": False,
        "progress": False,
        "error": "not found",
    }
    values.update(overrides)
    return StrategyObservation.model_validate(values)


def test_repeated_failures_switch_once_and_require_replan() -> None:
    manager = StrategyManager(failure_threshold=2)

    first = manager.observe(_observation(), step_number=1)
    second = manager.observe(_observation(), step_number=2)

    assert first.replan_required is False
    assert second.replan_required is True
    assert second.switch is not None
    assert second.switch.current == "semantic-dom"
    assert second.state.consecutive_failures == 0
    assert "semantic DOM" in second.prompt_hint


def test_policy_denial_and_loop_trigger_immediate_distinct_switches() -> None:
    manager = StrategyManager()
    policy = manager.observe(
        _observation(tool_name="download_pdf", policy_denied=True), step_number=1
    )
    loop = manager.observe(_observation(loop_type="action_repeat"), step_number=2)

    assert policy.switch is not None and policy.switch.current == "search-discovery"
    assert loop.switch is not None and loop.switch.current != policy.switch.current
    assert loop.replan_required is True


def test_progress_resets_counters_and_state_round_trips() -> None:
    manager = StrategyManager(failure_threshold=3)
    manager.observe(_observation(), step_number=1)
    update = manager.observe(_observation(success=True, progress=True, error=None), step_number=2)

    assert update.state.consecutive_failures == 0
    assert update.state.consecutive_no_progress == 0
    restored = StrategyState.model_validate_json(update.state.model_dump_json())
    assert StrategyManager(restored).state == update.state


def test_success_without_progress_eventually_switches() -> None:
    manager = StrategyManager(no_progress_threshold=2)
    stalled = _observation(success=True, progress=False, error=None)

    manager.observe(stalled, step_number=1)
    update = manager.observe(stalled, step_number=2)

    assert update.replan_required is True
    assert update.switch is not None
    assert "no progress" in update.switch.reason


def test_strategy_budget_exhausts_instead_of_cycling_forever() -> None:
    manager = StrategyManager(max_switches=1)
    first = manager.observe(_observation(policy_denied=True, tool_name="search"), step_number=1)
    second = manager.observe(_observation(policy_denied=True, tool_name="search"), step_number=2)

    assert first.switch is not None
    assert second.switch is None
    assert second.exhausted is True
    assert second.replan_required is True

"""Observation references preserve action identity and authorization boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from webagent.browser.reference_selectors import reference_parts
from webagent.browser.references import execute_reference, resolve_reference
from webagent.core.models import BrowserState, ToolCall
from webagent.tools.builtin.browser_tools import ClickTool, _resolve_selector
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry
from webagent.tools.risk import ActionRiskPolicy, BrowserRiskContext


def _target():
    target = SimpleNamespace(
        click=AsyncMock(),
        fill=AsyncMock(),
        type=AsyncMock(),
        press=AsyncMock(),
        hover=AsyncMock(),
        scroll_into_view_if_needed=AsyncMock(),
        select_option=AsyncMock(return_value=["b"]),
        inner_text=AsyncMock(return_value="readable"),
        get_attribute=AsyncMock(return_value="dest"),
        evaluate=AsyncMock(return_value={"text": "Delete account"}),
        dispose=AsyncMock(),
    )
    target.as_element = lambda: target
    return target


def _browser(target):
    frame = SimpleNamespace(parent_frame=None, evaluate_handle=AsyncMock(return_value=target))
    return SimpleNamespace(page=SimpleNamespace(frames=[frame], context=SimpleNamespace(pages=[])))


def _params(**values):
    return {
        "selector": {"type": "css", "value": "#opaque", "observation_id": "obs", "ref": "f0:e0"},
        **values,
    }


def test_direct_selector_resolution_cannot_discard_reference_binding():
    with pytest.raises(ValueError, match="registry dispatch"):
        _resolve_selector(_params()["selector"])


def test_stale_reference_is_repaired_before_spending_an_action():
    registry = ToolRegistry()
    registry.register(ClickTool(browser=None))
    executor = ToolExecutor(registry)
    executor.record_observation(
        BrowserState(url="u", title="t", timestamp="now", dom_summary="", observation_id="current")
    )
    call = ToolCall(
        tool_name="click", parameters={"selector": {"type": "ref", "value": "old/f0:e0"}}
    )
    assert "CURRENT PAGE" in executor.validate_tool_call(call)
    call.parameters["selector"]["value"] = "current/f0:e0"
    assert executor.validate_tool_call(call) is None


@pytest.mark.parametrize("compact", [False, True])
async def test_bound_action_still_runs_risk_policy_before_clicking(compact):
    target = _target()
    browser = _browser(target)
    registry = ToolRegistry()
    registry.register(ClickTool(browser=browser))
    executor = ToolExecutor(
        registry,
        risk_policy=ActionRiskPolicy(
            mode="deny",
            context_provider=BrowserRiskContext(browser),
        ),
    )
    params = _params()
    if compact:
        params["selector"] = {"type": "ref", "value": "obs/f0:e0"}
    result = await executor.execute(ToolCall(tool_name="click", parameters=params))
    assert result.success is False
    target.click.assert_not_called()
    target.dispose.assert_awaited_once()
    assert result.audit["risk"]["decision"] == "deny"


async def test_failed_observed_action_disposes_original_handle():
    target = _target()
    target.click.side_effect = RuntimeError("node detached during action")
    with pytest.raises(RuntimeError, match="detached"):
        await execute_reference(_browser(target), "click", _params())
    target.dispose.assert_awaited_once()


async def test_wrong_frame_and_changed_ancestor_reject_before_resolution():
    target = _target()
    browser = _browser(target)
    with pytest.raises(ValueError, match="frame mismatch"):
        await resolve_reference(
            browser.page,
            _params(selector={"type": "ref", "value": "obs/f1:e0"}),
            require_viewport=True,
        )
    browser.page.frames[0].evaluate_handle.assert_not_called()
    browser.page.frames[0].parent_frame = SimpleNamespace(evaluate=AsyncMock(return_value=False))
    frame_handle = SimpleNamespace(evaluate=AsyncMock(return_value=False), dispose=AsyncMock())
    browser.page.frames[0].frame_element = AsyncMock(return_value=frame_handle)
    with pytest.raises(ValueError, match="Ancestor frame changed"):
        await resolve_reference(browser.page, _params(), require_viewport=True)
    frame_handle.dispose.assert_awaited_once()


@pytest.mark.parametrize("value", ["f0:e0", "obs/f-1:e0", "obs/f0:e0/extra", 3])
def test_malformed_compact_reference_is_rejected(value):
    with pytest.raises(ValueError, match="reference value"):
        reference_parts({"type": "ref", "value": value})


async def test_compact_reference_uses_internal_node_mapping_without_css():
    target = _target()
    browser = _browser(target)
    selector = {"type": "ref", "value": "obs/f0:e0"}
    with pytest.raises(ValueError, match="registry dispatch"):
        _resolve_selector(selector)
    result = await execute_reference(browser, "click", {"selector": selector})
    assert result.success and result.data["observation_id"] == "obs"
    assert browser.page.frames[0].evaluate_handle.call_args.args[1]["selector"] is None
    target.click.assert_awaited_once()


def test_conflicting_compact_bindings_are_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        reference_parts({"type": "ref", "value": "obs/f0:e0", "observation_id": "other"})


def test_redundant_matching_compact_bindings_are_normalized():
    selector = {
        "type": "ref",
        "value": "obs/f0:e0",
        "observation_id": "obs",
        "ref": "f0:e0",
    }

    assert reference_parts(selector) == ("obs", "f0:e0", None)


@pytest.mark.parametrize(
    ("name", "params", "expected"),
    [
        ("type", {"text": "new", "clear_first": False}, {"text": "new"}),
        ("frame_interact", {"frame_index": 0, "action": "type", "text": "new"}, {"typed": True}),
        ("press", {"key": "Enter"}, {"key": "Enter"}),
        ("select_dropdown", {"value": "b"}, {"option": ["b"]}),
        ("extract_text", {}, {"text": "readable"}),
        ("get_attribute", {"attribute": "href"}, {"value": "dest"}),
    ],
)
async def test_bound_tool_preserves_result_contract(name, params, expected):
    target = _target()
    result = await execute_reference(_browser(target), name, _params(**params))
    assert result.success
    assert all(result.data[key] == value for key, value in expected.items())
    target.dispose.assert_awaited_once()

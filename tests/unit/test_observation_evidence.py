"""Evidence must distinguish model inputs, action outcomes and unknown delivery."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image

from webagent.agent.checkpoint_redaction import _checkpoint_step
from webagent.agent.observations import observation_references, save_observation
from webagent.agent.run_outputs import _save_step_screenshot
from webagent.core.models import AgentStep, BrowserState, ToolCall, ToolResult
from webagent.evaluation.artifacts import RunLayout
from webagent.planner.api import APIPlanner, _planning_screenshot_needed


def _state(**changes: Any) -> BrowserState:
    return BrowserState(
        screenshot=Image.new("RGB", (20, 30), "blue"),
        dom_summary="document text" * 600,
        url="https://example.test/",
        title="Document",
        timestamp="2026-09-08T00:00:00Z",
    ).model_copy(update=changes)


def test_observation_preserves_full_dom_and_lossless_image(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path)
    state = _state(
        full_page_screenshot=Image.new("RGB", (20, 300), "green"),
        elements=[{"text": "below fold", "in_viewport": False}],
        observation_metadata={"scroll": {"x": 0, "y": 400}},
    )
    path = save_observation(state, layout, 4, "pre")
    payload = json.loads((tmp_path / path).read_text())
    assert payload["dom_summary"] == state.dom_summary
    assert payload["elements"] == state.elements
    assert payload["metadata"]["scroll"]["y"] == 400
    encoded = (tmp_path / payload["screenshot"]["path"]).read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == payload["screenshot"]["sha256"]
    assert payload["full_page_screenshot"]["height"] == 300
    assert observation_references(layout, 4) == {"pre": path}
    assert observation_references(layout, 3) == {}


def test_absent_images_are_explicit_and_dom_stays_out_of_checkpoint(tmp_path: Path) -> None:
    state = _state(
        screenshot=None,
        observation_metadata={"title": "private title"},
        viewport_context="private viewport",
        document_context="private document",
        viewport_blocks=["private viewport blocks"],
        document_blocks=["private document blocks"],
        elements=[{"text": "private form value"}],
    )
    path = save_observation(state, RunLayout.from_root(tmp_path), 1, "pre")
    payload = json.loads((tmp_path / path).read_text())
    assert payload["screenshot"] is None
    assert payload["full_page_screenshot"] is None
    step = AgentStep(
        step_number=1,
        timestamp="now",
        browser_state=state,
        tool_call=ToolCall(tool_name="wait"),
        tool_result=ToolResult(tool_name="wait", success=True),
        duration_seconds=0,
    )
    checkpoint = _checkpoint_step(step, tmp_path)
    assert "private" not in json.dumps(checkpoint)
    assert "document text" not in json.dumps(checkpoint)


def test_replayed_step_preserves_old_attempt_evidence(tmp_path: Path) -> None:
    layout = RunLayout.from_root(tmp_path)
    original = save_observation(_state(), layout, 1, "pre")
    original_bytes = (tmp_path / original).read_bytes()
    replay = save_observation(_state(dom_summary="after recovery"), layout, 1, "pre")
    state = _state(observation_path=replay)
    post = save_observation(state, layout, 1, "post")
    assert original != replay
    assert (tmp_path / original).read_bytes() == original_bytes
    assert observation_references(layout, 1, original) == {"pre": original}
    assert observation_references(layout, 1, replay) == {"pre": replay, "post": post}
    with pytest.raises(FileExistsError):
        save_observation(state, layout, 1, "post")


def test_pair_reference_cannot_escape_step_directory(tmp_path: Path) -> None:
    state = _state(observation_path="observations/step_002/pre.json")
    with pytest.raises(ValueError, match="outside the step"):
        save_observation(state, RunLayout.from_root(tmp_path), 1, "post")


def test_replacing_legacy_preview_does_not_mutate_shared_previous_frame(tmp_path: Path) -> None:
    first = tmp_path / "step_001.jpg"
    second = tmp_path / "step_002.jpg"
    _save_step_screenshot(_state(), first)
    _save_step_screenshot(_state(), second)
    original = first.read_bytes()
    _save_step_screenshot(_state(screenshot=Image.new("RGB", (20, 30), "red")), second)
    assert first.read_bytes() == original
    assert second.read_bytes() != original


def _planner(mode: str = "always") -> APIPlanner:
    planner = APIPlanner(api_url="https://example.test/api", api_key="key", screenshot_mode=mode)
    planner._vision._supports_vision = True
    return planner


def test_scope_explanation_does_not_hide_sparse_page_from_vision() -> None:
    state = _state(observation_metadata={"dom_content_chars": 50})
    assert _planning_screenshot_needed(state, "") is True


async def test_planner_records_dispatched_image_and_actual_dom_prefix(monkeypatch) -> None:
    planner = _planner()
    payloads: list[dict[str, Any]] = []

    async def post(url, payload, headers, timeout=None):
        payloads.append(payload)
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": '{"tool": "done", "parameters": {}}'}}]},
        )

    monkeypatch.setattr(planner, "_bounded_post", post)
    state = _state(dom_summary="\n\n".join(["complete paragraph" * 20] * 20))
    await planner.plan_action("task", state, "", "done")
    meta = planner.last_planning_input_metadata
    assert meta["screenshot_sent"] is True
    assert meta["dom_truncated"] is True
    from webagent.planner.base import planner_dom_context

    actual_dom = planner_dom_context(state)
    assert 0 < meta["dom_chars"] <= 6000
    assert meta["dom_sha256"] == hashlib.sha256(actual_dom.encode()).hexdigest()
    content = payloads[0]["messages"][1]["content"]
    image_bytes = base64.b64decode(content[1]["image_url"]["url"].split(",")[1])
    assert meta["screenshot_sha256"] == hashlib.sha256(image_bytes).hexdigest()
    assert actual_dom in content[0]["text"]


@pytest.mark.parametrize(
    ("mode", "changes", "history", "reason"),
    [
        ("never", {}, "", "mode_never"),
        ("auto", {}, "", "auto_dom_sufficient"),
        ("always", {"screenshot": None}, "", "not_captured"),
        ("always", {"screenshot": Image.new("RGB", (20, 30), "white")}, "", "blank_image"),
        ("always", {"url": "file:///figure.png"}, "pdf_analyze_figure(", "local_artifact_evidence"),
    ],
)
async def test_omission_and_no_dispatch_are_distinct(monkeypatch, mode, changes, history, reason):
    planner = _planner(mode)
    monkeypatch.setattr(planner, "_call", AsyncMock(return_value="{}"))
    await planner.plan_action("task", _state(**changes), history, "done")
    meta = planner.last_planning_input_metadata
    assert meta["screenshot_omission_reason"] == reason
    assert meta["screenshot_sent"] is False
    assert meta["request_dispatched"] is False


async def test_failed_transport_keeps_input_evidence_and_next_call_resets_it(monkeypatch):
    planner = _planner()
    monkeypatch.setattr(planner, "_bounded_post", AsyncMock(side_effect=TimeoutError("network")))
    with pytest.raises(TimeoutError):
        await planner.plan_action("task", _state(), "", "done")
    assert planner.last_planning_input_metadata["screenshot_sent"] is True
    assert planner._planning_request_active is False
    planner._vision._supports_vision = False
    monkeypatch.setattr(planner, "_call", AsyncMock(return_value="{}"))
    await planner.plan_action("task", _state(), "", "done")
    assert (
        planner.last_planning_input_metadata["screenshot_omission_reason"] == "vision_unsupported"
    )
    assert planner.last_planning_input_metadata["screenshot_sent"] is False

import base64
import io

import pytest
from benchmarks.suites.browsergym.adapter import (
    browser_state_from_observation,
    browsergym_tool_specs,
    goal_images,
    goal_text,
    render_browsergym_action,
    task_id_from_name,
    task_set_sha256,
)
from PIL import Image

from webagent.core.models import ToolCall


def _data_uri() -> str:
    image = Image.new("RGB", (10, 8), "red")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def test_browsergym_tool_descriptor_round_trip_preserves_parameter_order() -> None:
    specs = browsergym_tool_specs(
        [
            {
                "name": "fill",
                "description": "Fill a field",
                "parameters": {
                    "type": "object",
                    "properties": {"bid": {"type": "string"}, "value": {"type": "string"}},
                    "required": ["bid", "value"],
                },
            }
        ]
    )
    action = render_browsergym_action(
        ToolCall(tool_name="fill", parameters={"value": "Ada", "bid": "42"}), specs
    )

    assert action == "fill(bid='42', value='Ada')"

    with pytest.raises(ValueError, match="missing required parameters"):
        render_browsergym_action(ToolCall(tool_name="fill", parameters={"bid": "42"}), specs)


def test_visual_goal_images_are_labeled_with_page_screenshot() -> None:
    goal = [
        {"type": "text", "text": "Find the matching item"},
        {"type": "image_url", "image_url": {"url": _data_uri()}},
    ]
    obs = {
        "goal_object": goal,
        "screenshot": Image.new("RGB", (20, 12), "blue"),
        "open_pages_urls": ["https://example.test"],
        "open_pages_titles": ["Example"],
        "active_page_index": 0,
        "url": "https://example.test",
        "axtree_txt": "[12] button Submit",
        "last_action": "",
        "last_action_error": "",
    }

    state = browser_state_from_observation(obs)

    assert goal_text(goal) == "Find the matching item"
    assert len(goal_images(goal)) == 1
    assert state.screenshot is not None
    assert state.screenshot.height == 8 + 12 + 2 * 28
    assert "REFERENCE IMAGE" in state.dom_summary
    assert "[12] button Submit" in state.dom_summary


def test_external_task_identity_and_hash_are_exact() -> None:
    assert task_id_from_name("webarena_verified.23.410.2") == 410
    assert task_id_from_name("visualwebarena.721") == 721
    assert task_set_sha256(["visualwebarena.1", "visualwebarena.2"]) != task_set_sha256(
        ["visualwebarena.2", "visualwebarena.1"]
    )
    assert task_set_sha256(["visualwebarena.1"], [28]) != task_set_sha256(
        ["visualwebarena.1"], [29]
    )

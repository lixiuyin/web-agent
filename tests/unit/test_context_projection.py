"""Structured context budgeting keeps complete controls and scope boundaries."""

from webagent.browser.context_projection import pack_blocks, project_context
from webagent.browser.rendered import _intersection, _local_clip, _transform
from webagent.browser.snapshot import _filter_and_dedupe
from webagent.core.models import BrowserState
from webagent.planner.base import planner_dom_context
from webagent.planner.observation_input import input_metadata


def test_budget_skips_large_record_without_cutting_following_selector():
    selector = 'selector: {"type":"css","value":"#target","ref":"f0:e0"}'
    packed, omitted = pack_blocks(["x" * 500, selector, "last"], 100)
    assert packed == selector + "\n\nlast"
    assert omitted == 1
    assert pack_blocks([selector], 0) == ("", 1)


def test_viewport_and_document_have_independent_budgets():
    projection = {
        "texts": [
            {"text": "on screen", "viewport_text": "on screen", "frame_index": 0},
            {"text": "off screen", "viewport_text": "", "frame_index": 0},
        ]
    }
    output = project_context(projection, [], viewport_budget=100, document_budget=0)
    assert "on screen" in output["viewport_context"]
    assert output["document_context"] == ""
    assert output["context_omissions"] == {"viewport": 0, "document": 1}
    state = BrowserState(
        dom_summary="legacy mixed content",
        url="u",
        title="t",
        timestamp="now",
        observation_id="obs",
        viewport_context=output["viewport_context"],
        document_context="",
        observation_metadata={"context_omissions": output["context_omissions"]},
    )
    actual = planner_dom_context(state)
    assert "legacy mixed" not in actual
    assert "VIEWPORT CONTENT" in actual and "DOCUMENT SUPPLEMENT" in actual
    assert input_metadata(state, None, "not_captured")["dom_truncated"] is True


def test_frame_projection_round_trip_with_scale_and_clipping():
    mapping = {
        "x": 100,
        "y": 200,
        "sx": 2,
        "sy": 2,
        "clip": {"x": 120, "y": 220, "width": 200, "height": 100},
    }
    clip = _local_clip(mapping)
    assert clip == {"x": 10, "y": 10, "width": 100, "height": 50}
    assert _transform(clip, mapping) == mapping["clip"]
    assert _intersection(clip, {"x": 1000, "y": 1000, "width": 20, "height": 20})["width"] == 0


def test_same_named_control_in_different_frames_is_not_deduplicated():
    control = {"tag": "button", "text": "Save", "css_path": "#save"}
    controls = [{**control, "frame_index": index} for index in (0, 1)]
    assert len(_filter_and_dedupe(controls)) == 2


def _control(index):
    return {
        "tag": "button",
        "text": f"Button {index}",
        "css_path": "#long" * 100,
        "observation_id": "obs",
        "ref": f"f0:e{index}",
        "in_viewport": True,
        "receives_events": True,
        "enabled": True,
    }


def test_dense_controls_cannot_starve_long_body_text():
    text = "Important正文。" * 200
    result = project_context(
        {"texts": [{"text": text, "viewport_text": text, "frame_index": 0}]},
        [_control(i) for i in range(50)],
        viewport_budget=700,
        document_budget=0,
        text_block_chars=100,
    )
    context = result["viewport_context"]
    assert len(context) <= 700
    assert "Important正文" in context and "[obs/f0:e0]" in context
    assert "#long" not in context and "selector:" not in context
    usage = result["context_budget_usage"]["viewport"]
    assert usage["text_chars"] > 200 and usage["control_chars"] > 200
    assert usage["text_omitted"] > 0 and usage["control_omitted"] > 0


def test_unused_control_budget_is_reclaimed_for_text():
    text = "Read this sentence. " * 100
    result = project_context(
        {"texts": [{"text": text, "viewport_text": text, "frame_index": 0}]},
        [],
        viewport_budget=500,
        document_budget=0,
    )
    assert 400 < len(result["viewport_context"]) <= 500


def test_long_unbroken_text_is_chunked_instead_of_completely_dropped():
    text = "中文正文" * 500
    result = project_context(
        {"texts": [{"text": text, "viewport_text": text, "frame_index": 0}]},
        [],
        viewport_budget=500,
        document_budget=0,
        text_block_chars=120,
    )
    assert "中文正文" in result["viewport_context"]
    assert all(len(block) <= 120 for block in result["viewport_blocks"])


def test_long_url_is_never_published_as_a_valid_looking_prefix():
    url = "https://source.test/" + "long-path-" * 80
    result = project_context(
        {
            "texts": [
                {"text": "Source: " + url, "viewport_text": "Source: " + url, "frame_index": 0}
            ]
        },
        [],
        viewport_budget=100,
        document_budget=0,
        text_block_chars=80,
    )
    assert "https://source.test" not in result["viewport_context"]
    assert any(url in block for block in result["viewport_blocks"])


def test_text_sentence_splitting_preserves_versions_decimals_and_urls():
    text = (
        "Qwen3.8-Flash-Next has 2.4 trillion tokens. See https://qwen.ai/blog?id=qwen3.8-flash-next"
    )
    result = project_context(
        {"texts": [{"text": text, "viewport_text": text, "frame_index": 0}]},
        [],
        viewport_budget=1000,
        document_budget=0,
    )
    for token in ("Qwen3.8-Flash-Next", "2.4", "https://qwen.ai/blog?id=qwen3.8-flash-next"):
        assert token in result["viewport_context"]

"""Tests for prompt building and response parsing."""

import base64

from PIL import Image

from webagent.core.models import BrowserState
from webagent.planner.base import (
    STRUCTURED_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_prompt,
    parse_llm_response,
)


def test_build_prompt():
    state = BrowserState(
        dom_summary="<h1>Hi</h1>",
        url="https://example.com",
        title="Example",
        timestamp="2024-01-01",
    )
    prompt, b64 = build_prompt("click button", state, "No previous actions.", "goto, click, done")
    assert "TASK: click button" in prompt
    assert "URL: https://example.com" in prompt
    assert b64 is None  # no screenshot


def test_system_prompts_require_selected_winner_date_for_dated_latest_tasks():
    for prompt in (SYSTEM_PROMPT, STRUCTURED_SYSTEM_PROMPT):
        assert "selected winner's explicit" in prompt
        assert "keep searching" in prompt


def test_build_prompt_includes_non_blank_screenshot_b64():
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "red"),
        dom_summary="<button>Go</button>",
        url="https://example.com",
        title="Example",
        timestamp="2024-01-01",
    )

    prompt, b64 = build_prompt("click button", state, "", "click, done")

    assert "PAGE:\n<button>Go</button>" in prompt
    assert b64 is not None
    assert base64.b64decode(b64, validate=True)


def test_build_prompt_omits_blank_screenshot_b64():
    state = BrowserState(
        screenshot=Image.new("RGB", (20, 20), "white"),
        dom_summary="<body></body>",
        url="about:blank",
        title="",
        timestamp="2024-01-01",
    )

    prompt, b64 = build_prompt("task", state, "", "done")

    assert "PAGE:\n<body></body>" in prompt
    assert STRUCTURED_SYSTEM_PROMPT not in prompt
    assert b64 is None


def test_parse_valid_json():
    tc = parse_llm_response(
        '{"tool": "goto", "parameters": {"url": "https://x.com"}, "reasoning": "nav"}'
    )
    assert tc is not None
    assert tc.tool_name == "goto"
    assert tc.parameters["url"] == "https://x.com"


def test_parse_json_in_code_block():
    raw = '```json\n{"tool": "click", "parameters": {"selector": {"type": "text", "value": "OK"}}}\n```'
    tc = parse_llm_response(raw)
    assert tc is not None
    assert tc.tool_name == "click"


def test_parse_empty_returns_none():
    assert parse_llm_response("") is None
    assert parse_llm_response("no json here") is None


def test_parse_invalid_json_returns_none():
    assert parse_llm_response("{invalid json}") is None


def test_parse_json_with_action_key():
    tc = parse_llm_response('{"action": "done", "params": {"summary": "ok"}}')
    assert tc is not None
    assert tc.tool_name == "done"


def test_parse_pretty_printed_json_with_tool_first():
    """Regression: 4-space-indented JSON with `tool` first must parse (not None).

    Previously a `{\n    "tool"` entry in the prefix-strip list corrupted this
    common pretty-printed format into an inner `{}` and returned None.
    """
    raw = (
        "{\n"
        '    "tool": "goto",\n'
        '    "parameters": {"url": "https://x.com"},\n'
        '    "reasoning": "navigate"\n'
        "}"
    )
    tc = parse_llm_response(raw)
    assert tc is not None
    assert tc.tool_name == "goto"
    assert tc.parameters["url"] == "https://x.com"


class TestParseLlmResponseEdges:
    def test_preamble_is_stripped(self):
        assert (
            parse_llm_response('Here is the JSON:\n{"tool": "click", "parameters": {}}') is not None
        )

    def test_unterminated_code_fence_returns_none(self):
        assert parse_llm_response('```json\n{"tool": "click"}') is None

    def test_plain_fence_without_json_tag(self):
        call = parse_llm_response('```\n{"tool": "scroll"}\n```')
        assert call is not None and call.tool_name == "scroll"

    def test_nested_braces_extracted_from_prose(self):
        call = parse_llm_response(
            'Sure! {"tool": "type", "parameters": {"text": "hi {brace}"}} hope that helps'
        )
        assert call is not None and call.parameters == {"text": "hi {brace}"}

    def test_trailing_commas_are_repaired(self):
        call = parse_llm_response('{"tool": "done", "parameters": {"a": 1,},}')
        assert call is not None and call.parameters == {"a": 1}

    def test_non_dict_json_returns_none(self):
        assert parse_llm_response("[1, 2, 3]") is None

    def test_params_alias_keys(self):
        call = parse_llm_response('{"action": "go", "arguments": {"url": "https://x"}}')
        assert call is not None
        assert call.tool_name == "go"
        assert call.parameters == {"url": "https://x"}

    def test_non_string_tool_returns_none(self):
        assert parse_llm_response('{"tool": 42}') is None

    def test_present_but_non_string_tool_does_not_fall_through(self):
        # "tool" exists but is not a string -> no fallback to "action".
        assert parse_llm_response('{"tool": null, "action": "click"}') is None

    def test_non_string_reasoning_becomes_empty(self):
        call = parse_llm_response('{"tool": "x", "reasoning": 5}')
        assert call is not None and call.reasoning == ""

    def test_empty_string_response_returns_none(self):
        assert parse_llm_response("") is None

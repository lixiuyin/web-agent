"""Regression tests for structured-output response parsing.

Structured output reuses the shared ``parse_llm_response`` parser; these cases
guard the response shapes emitted when ``use_structured_output`` is enabled.
"""

from webagent.core.models import ToolCall
from webagent.planner.base import parse_llm_response


def test_parse_structured_json_in_code_fence():
    """Regression: ```json fenced blocks must parse without dropping lines."""
    raw = (
        "```json\n"
        "{\n"
        '  "thinking": "look at page",\n'
        '  "memory": "step 1",\n'
        '  "next_goal": "click submit",\n'
        '  "tool": "click",\n'
        '  "parameters": {"selector": {"type": "text", "value": "Submit"}},\n'
        '  "reasoning": "advance"\n'
        "}\n"
        "```"
    )
    call = parse_llm_response(raw)
    assert call is not None
    assert isinstance(call, ToolCall)
    assert call.tool_name == "click"
    assert call.parameters["selector"]["value"] == "Submit"


def test_parse_structured_bare_json():
    call = parse_llm_response('{"tool": "done", "parameters": {"summary": "ok"}}')
    assert call is not None
    assert call.tool_name == "done"


def test_parse_structured_invalid_returns_none():
    assert parse_llm_response("not json at all") is None

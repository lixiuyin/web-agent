"""Regression tests for enhanced (structured-output) response parsing."""

from webagent.planner.enhanced_base import parse_enhanced_response


def test_parse_enhanced_json_in_code_fence():
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
    call = parse_enhanced_response(raw)
    assert call is not None
    assert call.tool_name == "click"
    assert call.parameters["selector"]["value"] == "Submit"


def test_parse_enhanced_bare_json():
    call = parse_enhanced_response('{"tool": "done", "parameters": {"summary": "ok"}}')
    assert call is not None
    assert call.tool_name == "done"


def test_parse_enhanced_invalid_returns_none():
    assert parse_enhanced_response("not json at all") is None

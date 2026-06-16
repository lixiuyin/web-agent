"""Tests for session history."""

from webagent.agent.history import SessionHistory
from webagent.core.models import AgentStep, BrowserState, ToolCall, ToolResult


def _make_step(n: int, tool: str = "goto", success: bool = True) -> AgentStep:
    return AgentStep(
        step_number=n,
        timestamp="2024-01-01",
        browser_state=BrowserState(
            dom_summary="", url="https://example.com", title="", timestamp="2024-01-01"
        ),
        tool_call=ToolCall(tool_name=tool, parameters={"url": "https://example.com"}),
        tool_result=ToolResult(
            success=success, tool_name=tool, data={"url": "https://example.com"}
        ),
        duration_seconds=0.5,
    )


def test_empty_history():
    h = SessionHistory()
    assert h.format_for_llm() == "No previous actions."
    assert h.steps == []


def test_add_and_format():
    h = SessionHistory(context_length=5)
    h.add(_make_step(1))
    h.add(_make_step(2, "click"))
    text = h.format_for_llm()
    assert "Step 1:" in text
    assert "Step 2:" in text
    assert "click" in text


def test_context_length_truncation():
    h = SessionHistory(context_length=2)
    for i in range(5):
        h.add(_make_step(i + 1))
    text = h.format_for_llm()
    assert "Step 4:" in text
    assert "Step 5:" in text
    assert "Step 1:" not in text


def test_clear():
    h = SessionHistory()
    h.add(_make_step(1))
    h.clear()
    assert h.steps == []

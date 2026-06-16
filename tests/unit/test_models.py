"""Tests for data models."""

from webagent.core.models import BrowserState, TaskStatus, ToolCall, ToolResult


def test_tool_call_creation():
    tc = ToolCall(tool_name="goto", parameters={"url": "https://x.com"})
    assert tc.tool_name == "goto"
    assert tc.parameters["url"] == "https://x.com"
    assert tc.reasoning == ""


def test_tool_result_success():
    tr = ToolResult(success=True, tool_name="goto", data={"url": "https://x.com"})
    assert tr.success is True
    assert tr.error is None


def test_tool_result_failure():
    tr = ToolResult(success=False, tool_name="goto", error="timeout")
    assert tr.success is False
    assert tr.error == "timeout"


def test_task_status_values():
    assert TaskStatus.COMPLETED.value == "completed"
    assert TaskStatus.FAILED.value == "failed"
    assert TaskStatus.TIMEOUT.value == "timeout"


def test_browser_state():
    bs = BrowserState(
        dom_summary="<h1>Hi</h1>",
        url="https://example.com",
        title="Example",
        timestamp="2024-01-01",
    )
    assert bs.screenshot is None
    assert bs.url == "https://example.com"

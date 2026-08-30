"""Default browser-grounded mode binds navigation and downloads to evidence."""

from __future__ import annotations

from pathlib import Path

from webagent.agent.context import planner_result_preview
from webagent.core.models import ToolCall, ToolResult
from webagent.tools.policy import BrowserGroundedPolicy


class _Page:
    url = "about:blank"


class _Browser:
    page = _Page()


class _LoadedPage:
    url = "https://app.example.test/catalog"


class _LoadedBrowser:
    page = _LoadedPage()


class _BrowserWithoutPage:
    pass


async def _record(
    policy: BrowserGroundedPolicy,
    call: ToolCall,
    result: ToolResult,
) -> None:
    decision = await policy.authorize(call)
    assert decision.allowed
    await policy.record_result(
        call,
        result,
        decision,
        planner_visible_result=planner_result_preview(
            call.tool_name, result.data, success=result.success
        ),
    )


async def test_user_supplied_url_is_allowed_but_guessed_url_is_denied(tmp_path: Path) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(), artifacts_dir=tmp_path / "artifacts", allowed_tools={"goto", "search"}
    )
    policy.reset("Open https://example.com/docs and summarize it")

    supplied = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.com/docs"})
    )
    guessed = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.com/private"})
    )

    assert supplied.allowed is True
    assert supplied.provenance["source"] == "user_task"
    assert guessed.allowed is False


async def test_search_result_grounds_navigation_without_strict_search_prerequisites(
    tmp_path: Path,
) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(), artifacts_dir=tmp_path / "artifacts", allowed_tools={"search", "goto"}
    )
    policy.reset("Find the documentation")
    url = "https://docs.example.test/guide"
    await _record(
        policy,
        ToolCall(tool_name="search", parameters={"query": "example docs"}),
        ToolResult(
            success=True,
            tool_name="search",
            data={"url": "https://bing.test/search", "results": [{"url": url}]},
        ),
    )

    decision = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": url}))
    assert decision.allowed is True
    assert decision.provenance["source"] == "search_planner_visible"


async def test_discovery_task_must_start_with_browser_search(tmp_path: Path) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(), artifacts_dir=tmp_path / "artifacts", allowed_tools={"search", "done"}
    )
    policy.reset("Find the official documentation")

    decision = await policy.authorize(ToolCall(tool_name="done", parameters={"summary": "x"}))

    assert decision.allowed is False
    assert "begin with browser search" in decision.reason


async def test_discovery_detection_tolerates_browser_before_page_creation(
    tmp_path: Path,
) -> None:
    policy = BrowserGroundedPolicy(
        _BrowserWithoutPage(),
        artifacts_dir=tmp_path / "artifacts",
        allowed_tools={"search", "done"},
    )
    policy.reset("Find the official documentation")

    decision = await policy.authorize(ToolCall(tool_name="done", parameters={"summary": "x"}))

    assert decision.allowed is False
    assert "begin with browser search" in decision.reason


async def test_latest_discovery_inherits_recency_evidence_gate(tmp_path: Path) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(), artifacts_dir=tmp_path / "artifacts", allowed_tools={"search", "done"}
    )
    policy.reset("Find the latest Aurora technical report")
    await _record(
        policy,
        ToolCall(tool_name="search", parameters={"query": "Aurora technical report"}),
        ToolResult(
            success=True,
            tool_name="search",
            data={
                "url": "https://bing.test/search",
                "results": [{"url": "https://example.test/report.pdf"}],
            },
        ),
    )

    decision = await policy.authorize(ToolCall(tool_name="done", parameters={"summary": "x"}))

    assert decision.allowed is False
    assert "at least two successful" in decision.reason


async def test_user_url_task_does_not_force_search(tmp_path: Path) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(), artifacts_dir=tmp_path / "artifacts", allowed_tools={"goto", "done"}
    )
    policy.reset("Find the heading at https://example.com/docs")

    decision = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.com/docs"})
    )

    assert decision.allowed is True


async def test_discovery_language_on_loaded_page_does_not_force_web_search(
    tmp_path: Path,
) -> None:
    policy = BrowserGroundedPolicy(
        _LoadedBrowser(),
        artifacts_dir=tmp_path / "artifacts",
        allowed_tools={"click_link", "done"},
    )
    policy.reset("Find the Amber Notebook in the current catalog")

    decision = await policy.authorize(
        ToolCall(tool_name="click_link", parameters={"text": "Amber Notebook"})
    )

    assert decision.allowed is True


async def test_browser_grounded_hides_direct_source_tool(tmp_path: Path) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(), artifacts_dir=tmp_path / "artifacts", allowed_tools={"search"}
    )
    decision = await policy.authorize(ToolCall(tool_name="official_report_search"))
    assert decision.allowed is False

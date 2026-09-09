"""Tests for search-engine-only anti-shortcut enforcement."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from webagent.agent.context import planner_result_preview
from webagent.core.models import BrowserState, ToolCall, ToolResult
from webagent.tools.executor import ToolExecutor
from webagent.tools.policies.evidence import _visible_result_urls, _visible_text_urls
from webagent.tools.policy import PolicyDecision, SearchEngineOnlyPolicy
from webagent.tools.registry import ToolRegistry


class _Page:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.hrefs: list[str] = []

    async def eval_on_selector_all(self, _selector: str, _script: str) -> list[str]:
        return list(self.hrefs)


def test_visible_result_urls_distinguish_prose_relative_fields_and_truncation():
    result = {
        "text": "Visit https://source.test/report for context.",
        "title": "Ordinary title",
        "links": [{"href": "guide.html", "text": "Guide"}],
        "partial": "See (https://hidden.test/report...).",
        "url": "https://partial.test/...",
    }
    assert _visible_result_urls(result, "https://source.test/docs/") == {
        "https://source.test/report",
        "https://source.test/docs/guide.html",
    }
    assert _visible_text_urls("Source: HTTPS://SOURCE.TEST/report.") == {
        "https://source.test/report"
    }
    assert _visible_result_urls(
        {"attribute": "href", "value": "../next"}, "https://source.test/docs/"
    ) == {"https://source.test/next"}
    assert _visible_result_urls(None, "https://source.test/") == set()


async def test_extracted_inline_url_is_authorized_without_trusting_hidden_raw_result(tmp_path):
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path)
    await _complete_search(policy, "https://source.test/start")
    call = ToolCall(tool_name="extract_text", parameters={})
    decision = await policy.authorize(call)
    visible = "https://source.test/report"
    hidden = "https://hidden.test/report"
    await policy.record_result(
        call,
        ToolResult(success=True, tool_name="extract_text", data={"text": visible + hidden}),
        decision,
        planner_visible_result=json.dumps({"text": f"Source: {visible}"}),
    )
    allowed = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": visible}))
    assert allowed.allowed and allowed.provenance["source"] == "extract_text_planner_visible"
    assert not (
        await policy.authorize(ToolCall(tool_name="goto", parameters={"url": hidden}))
    ).allowed


async def test_search_query_echo_does_not_ground_guessed_url(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    guessed = "https://guessed.test/report"
    await _complete_search(policy, "https://observed.test/page", query=f'Find "{guessed}"')
    assert guessed not in policy.export_state()["observed_urls"]
    assert not (
        await policy.authorize(ToolCall(tool_name="goto", parameters={"url": guessed}))
    ).allowed


async def test_write_results_and_input_attribute_echoes_do_not_ground_urls(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    await _complete_search(policy, "https://observed.test/page")
    guessed = "https://guessed.test/report"
    cases = [
        ("type", {"text": guessed}),
        ("frame_interact", {"action": "type", "text": guessed}),
        ("get_attribute", {"attribute": "value", "value": guessed}),
    ]
    for name, data in cases:
        call = ToolCall(tool_name=name, parameters={})
        decision = await policy.authorize(call)
        assert decision.allowed
        await _record_result(
            policy, call, ToolResult(success=True, tool_name=name, data=data), decision
        )
    assert guessed not in policy.export_state()["observed_urls"]


class _Browser:
    def __init__(self) -> None:
        self.page = _Page()


class _Tool:
    def __init__(self, name: str, result: ToolResult) -> None:
        self._tool_name = name
        self._tool_description = f"{name} description"
        self._result = result

    def validate_params(self, _params: dict[str, Any]) -> None:
        return None

    async def execute(self, _params: dict[str, Any]) -> ToolResult:
        return self._result


def _observe_official(policy: SearchEngineOnlyPolicy, url: str) -> None:
    policy.record_observation(
        BrowserState(url=url, title="Report", dom_summary="", timestamp="now")
    )


async def test_complete_observation_does_not_bypass_strict_first_search(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    policy.reset("Summarize this page")
    policy.record_observation(
        BrowserState(
            url="https://official.test/guide",
            title="Guide",
            timestamp="now",
            dom_summary="Guide",
            observation_metadata={"status": "complete"},
        )
    )
    decision = await policy.authorize(ToolCall(tool_name="done", parameters={"summary": "Guide"}))
    assert not decision.allowed
    assert "first successful action must be browser search" in decision.reason


def test_pdf_on_current_candidate_page_takes_priority_over_homepage_navigation(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    policy.reset("Find the latest Qwen PDF")
    policy.record_observation(
        BrowserState(
            url="https://github.com/QwenLM/model",
            title="Model",
            dom_summary="",
            viewport_context="tech_report.pdf",
            timestamp="now",
        )
    )
    assert (
        "call inspect_download_links on this page before leaving" in policy.planner_evidence_hint()
    )


def test_pdf_on_recovered_frontier_repository_precedes_more_recovery(tmp_path):
    browser = _Browser()
    browser.page.url = "https://github.com/QwenLM/Qwen3.8-Flash-Next"
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    policy._version_frontier = "qwen3.8"
    policy._version_frontier_key = (3, 8)
    policy._report_discovery_attempts = 2
    policy._official_identity_search_completed = True
    policy._official_identity_urls = {"https://github.com/QwenLM/Qwen"}
    policy.record_observation(
        BrowserState(
            url="https://github.com/QwenLM/Qwen3.8-Flash-Next",
            title="Qwen3.8-Flash-Next",
            dom_summary="",
            viewport_context="tech_report.pdf",
            timestamp="now",
        )
    )

    hint = policy.planner_evidence_hint()

    assert hint.startswith("\nCURRENT PAGE EXPOSES A PDF/REPORT")
    assert "call inspect_download_links on this page before leaving" in hint
    assert "BOUNDED REPORT DISCOVERY RECOVERY" not in hint


def test_old_pdf_on_base_repository_does_not_preempt_frontier_recovery(tmp_path):
    browser = _Browser()
    browser.page.url = "https://github.com/QwenLM/Qwen"
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    policy._version_frontier = "qwen3.8"
    policy._version_frontier_key = (3, 8)
    policy._report_discovery_attempts = 2
    policy.record_observation(
        BrowserState(
            url=browser.page.url,
            title="Qwen",
            dom_summary="",
            viewport_context="QWEN_TECHNICAL_REPORT.pdf",
            timestamp="now",
        )
    )

    hint = policy.planner_evidence_hint()

    assert hint.startswith("BOUNDED REPORT DISCOVERY RECOVERY")
    assert "CURRENT PAGE EXPOSES A PDF/REPORT" not in hint


def test_observed_highest_version_report_precedes_generic_release_navigation(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    report = "https://github.com/QwenLM/Qwen3.8-Flash-Next/commits/main/tech_report.pdf"
    policy._candidate_ledger.search(
        "Qwen 2026 release model",
        {"results": [{"title": "tech_report.pdf", "url": report}]},
        {"qwen"},
    )
    policy._record_url(
        "https://qwen.ai/", source="search_planner_visible", page_url="https://bing.test/"
    )

    hint = policy.planner_evidence_hint()

    assert hint.startswith("PRIORITY REPORT SOURCE")
    assert report in hint
    assert "candidate lead, not proof" in hint
    assert "RELEASE DISCOVERY ROUTE" not in hint


def test_highest_release_without_report_requests_unrestricted_pdf_search(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    policy._version_frontier = "qwen3.8"
    policy._version_frontier_key = (3, 8)
    policy._candidate_ledger.search(
        "Qwen reports",
        {
            "results": [
                {
                    "title": "Qwen3 Technical Report",
                    "url": "https://arxiv.org/abs/2505.09388",
                }
            ]
        },
        {"qwen"},
    )

    hint = policy.planner_evidence_hint()

    assert hint.startswith("PRIORITY REPORT DISCOVERY GAP")
    assert "qwen3.8 technical report PDF" in hint
    assert "without an arXiv" in hint
    assert "2505.09388" not in hint

    generic = ToolCall(tool_name="goto", parameters={"url": "https://github.com/QwenLM/Qwen3.8"})
    assert "PRIORITY REPORT DISCOVERY GAP" in (policy.validate_planner_call(generic) or "")

    report = "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"
    policy._candidate_ledger.search(
        "Qwen3.8 technical report PDF",
        {"results": [{"title": "tech_report.pdf", "url": report}]},
        {"qwen"},
    )
    assert (
        policy.validate_planner_call(ToolCall(tool_name="goto", parameters={"url": report})) is None
    )


async def test_report_discovery_gap_becomes_bounded_navigation_recovery(tmp_path):
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    task = "Find the latest Qwen technical report PDF"
    policy.reset(task)
    policy._version_frontier = "qwen3.8"
    policy._version_frontier_key = (3, 8)
    policy._official_identity_urls = {"https://github.com/QwenLM/Qwen"}
    call = ToolCall(tool_name="search", parameters={"query": "qwen3.8 technical report PDF"})
    for _ in range(2):
        decision = await policy.authorize(call)
        await _record_result(
            policy,
            call,
            ToolResult(success=False, tool_name="search", error="selector drift"),
            decision,
        )

    hint = policy.planner_evidence_hint()
    generic = ToolCall(tool_name="goto", parameters={"url": "https://github.com/QwenLM/Qwen"})

    assert hint.startswith("BOUNDED REPORT DISCOVERY RECOVERY")
    assert "organization page" in hint
    assert 'Observed owner labels: ["qwenlm"]' in hint
    assert "click the visible owner breadcrumb" in hint
    assert "first click the visible Repositories tab" in hint
    assert policy.validate_planner_call(generic) is None
    restored = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path)
    restored.import_state(policy.export_state(), task=task)
    assert restored.export_state()["report_discovery_attempts"] == 2


async def _complete_search(
    policy: SearchEngineOnlyPolicy,
    url: str,
    query: str = "report",
    *,
    result_title: str = "",
    result_snippet: str = "",
    recency: str | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {"query": query}
    if recency is not None:
        parameters["recency"] = recency
    call = ToolCall(tool_name="search", parameters=parameters)
    decision = await policy.authorize(call)
    assert decision.allowed is True
    result = ToolResult(
        success=True,
        tool_name="search",
        data={
            "url": "https://www.bing.com/search?q=report",
            "results": [{"title": result_title, "url": url, "snippet": result_snippet}],
        },
    )
    return await _record_result(policy, call, result, decision)


async def _record_result(
    policy: SearchEngineOnlyPolicy,
    call: ToolCall,
    result: ToolResult,
    decision: Any,
) -> dict[str, Any]:
    return await policy.record_result(
        call,
        result,
        decision,
        planner_visible_result=planner_result_preview(
            call.tool_name, result.data, success=result.success
        ),
    )


async def test_official_org_repository_link_counts_as_identity_bound_scope(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report PDF")
    policy._search_completed = True
    policy._official_identity_search_completed = True
    policy._official_identity_urls = {"https://github.com/QwenLM/Qwen"}
    policy._version_frontier = "qwen3.8"
    policy._version_frontier_key = (3, 8)
    organization = "https://github.com/orgs/QwenLM/repositories"
    target = "https://github.com/QwenLM/Qwen3.8-Flash-Next"
    policy._record_url(
        target,
        source="get_all_links_planner_visible",
        page_url=organization,
    )
    call = ToolCall(tool_name="goto", parameters={"url": target})
    decision = await policy.authorize(call)
    browser.page.url = target

    audit = await _record_result(
        policy,
        call,
        ToolResult(success=True, tool_name="goto", data={"url": target}),
        decision,
    )

    assert audit["official_scope_search_completed"] is True
    assert audit["official_scope_result_count"] == 1


async def test_other_owner_repository_link_does_not_count_as_scope(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report PDF")
    policy._search_completed = True
    policy._official_identity_search_completed = True
    policy._official_identity_urls = {"https://github.com/QwenLM/Qwen"}
    policy._version_frontier = "qwen3.8"
    organization = "https://github.com/orgs/Other/repositories"
    target = "https://github.com/Other/Qwen3.8-Flash-Next"
    policy._record_url(
        target,
        source="get_all_links_planner_visible",
        page_url=organization,
    )
    call = ToolCall(tool_name="goto", parameters={"url": target})
    decision = await policy.authorize(call)
    browser.page.url = target

    audit = await _record_result(
        policy,
        call,
        ToolResult(success=True, tool_name="goto", data={"url": target}),
        decision,
    )

    assert audit["official_scope_search_completed"] is False


async def test_first_successful_action_must_be_browser_search(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")

    goto = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://github.com/known/repo"})
    )
    done = await policy.authorize(ToolCall(tool_name="done", parameters={"summary": "guess"}))

    assert goto.allowed is False
    assert done.allowed is False
    assert "first successful action" in goto.reason


async def test_download_filename_override_requires_exact_user_request(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Download the file and upload that exact downloaded file")

    denied = await policy.authorize(
        ToolCall(
            tool_name="download_file",
            parameters={"selector": {"type": "text", "value": "Download"}, "filename": "case_file"},
        )
    )
    assert denied.allowed is False
    assert "browser-suggested" in denied.reason

    policy.reset("Download it and rename the file case_file")
    allowed_name = await policy.authorize(
        ToolCall(
            tool_name="download_file",
            parameters={"selector": {"type": "text", "value": "Download"}, "filename": "case_file"},
        )
    )
    assert "browser-suggested" not in allowed_name.reason


async def test_failed_search_does_not_unlock_other_tools(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    call = ToolCall(tool_name="search", parameters={"query": "report"})
    decision = await policy.authorize(call)
    await _record_result(
        policy,
        call,
        ToolResult(success=False, tool_name="search", error="blocked"),
        decision,
    )

    denied = await policy.authorize(ToolCall(tool_name="click", parameters={}))

    assert denied.allowed is False


async def test_success_without_search_results_does_not_unlock_policy(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    call = ToolCall(tool_name="search", parameters={"query": "report"})
    decision = await policy.authorize(call)
    await _record_result(
        policy,
        call,
        ToolResult(success=True, tool_name="search", data={"results": []}),
        decision,
    )

    denied = await policy.authorize(ToolCall(tool_name="click", parameters={}))

    assert denied.allowed is False


async def test_goto_accepts_observed_search_result_and_rejects_guess(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    observed = "https://github.com/known/repo/blob/main/report.pdf"
    await _complete_search(policy, observed)

    allowed = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": observed}))
    denied = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://github.com/guessed/repo"})
    )

    assert allowed.allowed is True
    assert allowed.provenance["source"] == "search_planner_visible"
    assert denied.allowed is False
    assert "not observed" in denied.reason


async def test_goto_rejects_exact_current_url_as_no_progress(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    observed = "https://example.test/current"
    await _complete_search(policy, observed)
    browser.page.url = observed

    denied = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": observed}))

    assert denied.allowed is False
    assert "already on this exact URL" in denied.reason


async def test_done_requires_a_visited_non_search_evidence_page(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    observed = "https://example.test/report"
    await _complete_search(policy, observed)

    denied = await policy.authorize(
        ToolCall(tool_name="done", parameters={"summary": f"Found {observed}"})
    )
    assert denied.allowed is False
    assert "visiting a non-search evidence page" in denied.reason

    goto = ToolCall(tool_name="goto", parameters={"url": observed})
    decision = await policy.authorize(goto)
    browser.page.url = observed
    await _record_result(
        policy,
        goto,
        ToolResult(success=True, tool_name="goto", data={"url": observed}),
        decision,
    )

    allowed = await policy.authorize(
        ToolCall(tool_name="done", parameters={"summary": f"Source: `{observed}`"})
    )
    assert allowed.allowed is True


async def test_planner_visible_url_upgrades_internal_visit_provenance(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://example.test/start")

    click = ToolCall(tool_name="click_link", parameters={"text": "Docs"})
    decision = await policy.authorize(click)
    destination = "https://example.test/docs"
    browser.page.url = destination
    await _record_result(
        policy,
        click,
        ToolResult(success=True, tool_name="click_link", data={"text": "Docs"}),
        decision,
    )
    assert policy._observed_urls[destination]["source"] == "visited_page"

    policy.record_observation(
        BrowserState(
            url=destination,
            title="Docs",
            dom_summary="Documentation",
            timestamp="now",
            observation_metadata={"status": "complete"},
        )
    )
    assert policy._observed_urls[destination]["source"] == "planner_state_current_url"

    browser.page.url = "https://www.bing.com/search?q=docs"
    allowed = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": destination}))
    assert allowed.allowed is True
    assert allowed.provenance["source"] == "planner_state_current_url"


async def test_done_rejects_unobserved_cited_url(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    observed = "https://example.test/report"
    await _complete_search(policy, observed)
    goto = ToolCall(tool_name="goto", parameters={"url": observed})
    decision = await policy.authorize(goto)
    browser.page.url = observed
    await _record_result(
        policy,
        goto,
        ToolResult(success=True, tool_name="goto", data={"url": observed}),
        decision,
    )

    denied = await policy.authorize(
        ToolCall(
            tool_name="done",
            parameters={"summary": "Source: https://example.test/invented"},
        )
    )
    preflight = policy.validate_planner_call(
        ToolCall(
            tool_name="done",
            parameters={"summary": "Source: https://example.test/invented"},
        )
    )
    assert denied.allowed is False
    assert "absent from browser evidence" in denied.reason
    assert preflight is not None and "final allowlist" in preflight


async def test_done_strips_markdown_emphasis_from_cited_url(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    observed = "https://example.test/report"
    await _complete_search(policy, observed)
    goto = ToolCall(tool_name="goto", parameters={"url": observed})
    decision = await policy.authorize(goto)
    browser.page.url = observed
    await _record_result(
        policy,
        goto,
        ToolResult(success=True, tool_name="goto", data={"url": observed}),
        decision,
    )

    allowed = await policy.authorize(
        ToolCall(tool_name="done", parameters={"summary": f"Source: **{observed}**"})
    )

    assert allowed.allowed is True


async def test_complete_link_projection_records_urls_without_truncation(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://example.test/start")
    call = ToolCall(tool_name="get_all_links", parameters={})
    decision = await policy.authorize(call)
    links = [
        {"href": f"https://example.test/docs/{index}", "text": f"Documentation {index}"}
        for index in range(30)
    ]
    result = ToolResult(
        success=True,
        tool_name="get_all_links",
        data={"links": links, "total_count": 30, "returned": 30},
    )

    audit = await _record_result(policy, call, result, decision)

    assert audit["new_urls_observed"] == 20
    assert "https://example.test/docs/19" in policy._observed_urls
    assert "https://example.test/docs/20" not in policy._observed_urls


async def test_terminal_evidence_hint_lists_visited_page_not_unvisited_variant(
    tmp_path: Path,
) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    visited = "https://numpy.org/devdocs/user/absolute_beginners.html"
    unvisited = "https://numpy.org/doc/stable/user/absolute_beginners.html"
    await _complete_search(policy, "https://numpy.org/")
    goto = ToolCall(tool_name="goto", parameters={"url": "https://numpy.org/"})
    decision = await policy.authorize(goto)
    browser.page.url = visited
    await _record_result(
        policy,
        goto,
        ToolResult(success=True, tool_name="goto", data={"url": visited}),
        decision,
    )

    hint = policy.terminal_evidence_hint()

    assert visited in hint
    assert unvisited not in hint
    assert "do not reconstruct URL variants" in hint


async def test_unobserved_url_forces_exact_search_recovery(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://playwright.dev/docs/intro")
    target = "https://playwright.dev/python/docs/intro"

    denied_goto = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": target}))
    denied_broad_search = await policy.authorize(
        ToolCall(
            tool_name="search",
            parameters={"query": "Playwright Python docs introduction pip install"},
        )
    )
    exact_search = await policy.authorize(
        ToolCall(tool_name="search", parameters={"query": f'"{target}"'})
    )

    assert denied_goto.allowed is False
    assert denied_broad_search.allowed is False
    assert "exact quoted URL query" in denied_broad_search.reason
    assert exact_search.allowed is True
    assert (
        policy.validate_planner_call(
            ToolCall(
                tool_name="search",
                parameters={"query": "Playwright Python docs introduction pip install"},
            )
        )
        is not None
    )
    assert (
        policy.validate_planner_call(
            ToolCall(tool_name="search", parameters={"query": f'"{target}"'})
        )
        is None
    )
    assert target in policy.planner_evidence_hint()


async def test_exact_search_result_clears_url_recovery_hint(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://playwright.dev/docs/intro")
    target = "https://playwright.dev/python/docs/intro"
    await policy.authorize(ToolCall(tool_name="goto", parameters={"url": target}))
    search = ToolCall(tool_name="search", parameters={"query": f'"{target}"'})
    decision = await policy.authorize(search)
    await policy.record_result(
        search,
        ToolResult(
            success=True,
            tool_name="search",
            data={"results": [{"title": "Python", "url": target}]},
        ),
        decision,
        planner_visible_result=json.dumps(
            {"query": f'"{target}"', "results": [{"title": "Python", "url": target}]}
        ),
    )

    assert policy.planner_evidence_hint() == ""
    assert (await policy.authorize(ToolCall(tool_name="goto", parameters={"url": target}))).allowed


async def test_exact_url_recovery_stops_after_two_searches_and_resumes(tmp_path: Path) -> None:
    target = "https://playwright.dev/python/docs/intro"
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://playwright.dev/docs/intro")
    await policy.authorize(ToolCall(tool_name="goto", parameters={"url": target}))

    for index in range(2):
        search = ToolCall(tool_name="search", parameters={"query": f'"{target}"'})
        decision = await policy.authorize(search)
        assert decision.allowed is True
        await _record_result(
            policy,
            search,
            ToolResult(
                success=True,
                tool_name="search",
                data={"results": [{"url": f"https://playwright.dev/other/{index}"}]},
            ),
            decision,
        )

    restored = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    restored.import_state(policy.export_state(), task="")
    denied = await restored.authorize(
        ToolCall(tool_name="search", parameters={"query": f'"{target}"'})
    )

    assert denied.allowed is False
    assert "two exact searches" in denied.reason
    assert "links, tabs, or site navigation" in restored.planner_evidence_hint()


async def test_policy_checkpoint_restores_grounded_url_and_counters(tmp_path: Path) -> None:
    observed = "https://example.test/report.pdf"
    original = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    original.reset("Find a report")
    await _complete_search(original, observed, "report one")
    state = original.export_state()

    restored = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    restored.import_state(state, task="Find a report")
    allowed = await restored.authorize(ToolCall(tool_name="goto", parameters={"url": observed}))

    assert allowed.allowed is True
    assert restored.export_state()["successful_searches"] == 1


def test_policy_checkpoint_rejects_task_or_policy_mismatch(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("task a")
    state = policy.export_state()

    import pytest

    with pytest.raises(ValueError, match="task mismatch"):
        policy.import_state(state, task="task b")
    state["policy"] = "other"
    with pytest.raises(ValueError, match="schema/name mismatch"):
        policy.import_state(state, task="task a")


async def test_hidden_dom_anchor_and_nonvisible_tool_data_do_not_authorize_url(
    tmp_path: Path,
) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://example.test/known")
    hidden = "https://example.test/hidden.pdf"
    browser.page.hrefs = [hidden]

    call = ToolCall(tool_name="get_all_links", parameters={})
    decision = await policy.authorize(call)
    await policy.record_result(
        call,
        ToolResult(
            success=True,
            tool_name="get_all_links",
            data={"links": [{"href": hidden}]},
        ),
        decision,
        planner_visible_result="{}",
    )

    denied = await policy.authorize(ToolCall(tool_name="goto", parameters={"url": hidden}))

    assert denied.allowed is False


async def test_malformed_url_like_search_text_is_ignored_without_crashing(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    call = ToolCall(tool_name="search", parameters={"query": "Qwen3.8 report"})
    decision = await policy.authorize(call)
    result = ToolResult(
        success=True,
        tool_name="search",
        data={
            "url": "https://search.example/?q=qwen",
            "results": [
                {"title": "[2505.09388] Qwen report", "url": "https://[2505.09388]/bad"},
                {"title": "Official report", "url": "https://example.test/report.pdf"},
            ],
        },
    )

    audit = await _record_result(policy, call, result, decision)
    allowed = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.test/report.pdf"})
    )

    assert audit["search_completed"] is True
    assert allowed.allowed is True


async def test_site_scope_must_be_endorsed_by_prior_official_identity_search(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen report")
    year = datetime.now(UTC).year
    official_pdf = "https://github.com/QwenLM/Qwen3/blob/main/report.pdf"
    await _complete_search(policy, official_pdf, "Qwen3.5 official repository")
    await _complete_search(policy, official_pdf, f"Qwen report {year}")
    await _complete_search(
        policy,
        official_pdf,
        f"Qwen latest model releases {year}",
        result_title="Qwen3 model release",
    )
    await _complete_search(policy, official_pdf, f"site:github.com Qwen report {year}")
    broad_scope_denied = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )
    await _complete_search(
        policy,
        "https://github.com/OtherOwner/repo/blob/main/report.pdf",
        f"site:github.com/OtherOwner Qwen report {year}",
    )

    denied = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )
    await _complete_search(
        policy,
        official_pdf,
        f"site:github.com/QwenLM Qwen report {year}",
    )
    allowed = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )

    assert broad_scope_denied.allowed is False
    assert denied.allowed is False
    assert "endorsed host/owner" in denied.reason
    assert allowed.allowed is True


async def test_repository_scope_requires_endorsed_owner_in_query_and_result(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen report")
    year = datetime.now(UTC).year
    official_pdf = "https://github.com/QwenLM/Qwen3.8/blob/main/tech_report.pdf"
    await _complete_search(policy, official_pdf, "Qwen official repository")
    await _complete_search(policy, official_pdf, f"Qwen report {year}")
    await _complete_search(
        policy,
        official_pdf,
        f"Qwen latest model releases {year}",
        result_title="Qwen3.8 model release",
    )

    await _complete_search(
        policy,
        "https://github.com/OtherOwner/Qwen/blob/main/report.pdf",
        f"site:github.com QwenLM Qwen report {year}",
    )
    off_owner = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )
    await _complete_search(policy, official_pdf, "Qwen3.8 technical report")
    await _complete_search(
        policy,
        official_pdf,
        f"GitHub QwenLM Qwen report {year}",
    )
    _observe_official(policy, official_pdf)
    allowed = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )

    assert off_owner.allowed is False
    assert allowed.allowed is True


async def test_repository_scope_accepts_version_qualified_subject_after_identity(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")
    year = datetime.now(UTC).year
    official_pdf = "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"
    await _complete_search(policy, official_pdf, "QwenLM official GitHub repository")
    await _complete_search(policy, official_pdf, f"Qwen model release lineup {year}")
    await _complete_search(
        policy,
        official_pdf,
        f"GitHub QwenLM Qwen3.8-Flash-Next {year} release",
    )
    _observe_official(policy, official_pdf)

    allowed = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )

    assert allowed.allowed is True


async def test_exact_version_result_under_previously_endorsed_owner_establishes_scope(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report PDF")
    target = "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"
    await _complete_search(
        policy,
        "https://github.com/QwenLM/Qwen",
        "Qwen official repository",
        result_title="Official Qwen repository",
    )
    audit = await _complete_search(policy, target, "Qwen3.8 technical report PDF")

    assert audit["official_scope_search_completed"] is True
    assert target in policy.export_state()["official_scope_result_urls"]


async def test_exact_version_result_under_different_owner_does_not_establish_scope(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report PDF")
    await _complete_search(
        policy,
        "https://github.com/QwenLM/Qwen",
        "Qwen official repository",
        result_title="Official Qwen repository",
    )
    audit = await _complete_search(
        policy,
        "https://github.com/Other/Qwen3.8-Flash-Next/blob/main/tech_report.pdf",
        "Qwen3.8 technical report PDF",
    )

    assert audit["official_scope_search_completed"] is False


async def test_latest_task_requires_exact_followup_for_higher_version_lead(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")
    year = datetime.now(UTC).year
    older = "https://github.com/QwenLM/Qwen3.5/blob/main/report.pdf"
    newer_lead = "https://tracker.example/qwen3.8/"

    await _complete_search(policy, older, "Qwen official repository")
    await _complete_search(policy, newer_lead, f"Qwen latest model releases {year}")
    await _complete_search(policy, older, f"GitHub QwenLM Qwen report {year}")

    unresolved = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": older})
    )
    await _complete_search(policy, older, "Qwen3.8 technical report official")
    resolved = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": older}))

    assert unresolved.allowed is False
    assert "qwen3.8" in unresolved.reason
    assert "exact version" in unresolved.reason
    assert resolved.allowed is False  # Query text is not evidence that a lead was checked.


async def test_release_variant_in_visible_snippet_becomes_exact_frontier(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report PDF")

    audit = await _complete_search(
        policy,
        "https://x.com/Alibaba_Qwen",
        "Qwen technical report 2026 official",
        result_title="Qwen (@Alibaba_Qwen)",
        result_snippet=(
            "Qwen released Qwen3.8-Flash-Next, an experimental open-weight model "
            "that previews the next architecture."
        ),
    )

    assert audit["version_frontier"] == "qwen3.8-flash-next"
    assert audit["newer_version_leads_resolved"] is False
    assert "qwen3.8-flash-next technical report PDF" in policy.planner_evidence_hint()


async def test_named_variant_replaces_bare_same_numeric_frontier(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Model technical report PDF")

    await _complete_search(
        policy,
        "https://model.example/releases/model3.8",
        "Model latest releases 2026",
        result_title="Model3.8 release",
    )
    audit = await _complete_search(
        policy,
        "https://model.example/releases/model3.8-flash-next",
        "Model official updates 2026",
        result_title="Model announcements",
        result_snippet="Introducing Model3.8-Flash-Next.",
    )

    assert audit["version_frontier"] == "model3.8-flash-next"


async def test_latest_task_requires_two_successful_searches(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the most recent technical report")
    url = "https://example.test/report.pdf"
    await _complete_search(policy, url)

    too_early = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": url}))
    await _complete_search(policy, url, "  REPORT  ")
    duplicate_query = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": url})
    )
    await _complete_search(policy, url, "official report date")
    missing_year = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": url})
    )
    await _complete_search(policy, url, f"report arXiv {datetime.now(UTC).year}")
    index_only_year = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": url})
    )
    await _complete_search(policy, url, f"official report {datetime.now(UTC).year}")
    missing_release_landscape = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": url})
    )
    await _complete_search(
        policy,
        url,
        f"latest model releases report {datetime.now(UTC).year}",
        result_title="Model release catalog",
    )
    missing_official_site = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": url})
    )
    await _complete_search(
        policy, url, f"site:publisher.example official report {datetime.now(UTC).year}"
    )
    offsite_result = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": url})
    )
    await _complete_search(
        policy, url, f"site:example.test official report {datetime.now(UTC).year}"
    )
    allowed = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": url}))

    assert too_early.allowed is False
    assert duplicate_query.allowed is False
    assert "two successful" in too_early.reason
    assert missing_year.allowed is False
    assert str(datetime.now(UTC).year) in missing_year.reason
    assert index_only_year.allowed is False
    assert "aggregator" in index_only_year.reason
    assert missing_release_landscape.allowed is False
    assert "release landscape" in missing_release_landscape.reason
    assert missing_official_site.allowed is False
    assert "scope search" in missing_official_site.reason
    assert offsite_result.allowed is False
    assert allowed.allowed is True


async def test_latest_denial_reports_every_missing_prerequisite_at_once(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")
    target = "https://github.com/QwenLM/Qwen3/blob/main/report.pdf"
    audit = await _complete_search(policy, target, "Qwen report")

    denied = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": target}))

    missing = denied.provenance["missing_prerequisites"]
    assert denied.allowed is False
    assert audit["latest_missing_prerequisites"] == missing
    assert audit["latest_evidence_complete"] is False
    assert len(missing) == 5
    assert "two successful" in denied.reason
    assert str(datetime.now(UTC).year) in denied.reason
    assert "release landscape" in denied.reason
    assert "official identity" in denied.reason
    assert "endorsed host/owner" in denied.reason
    assert denied.reason.count("\n- ") == len(missing)


async def test_release_landscape_requires_semantic_result_evidence(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")
    year = datetime.now(UTC).year

    await _complete_search(
        policy,
        "https://unrelated.example/about",
        f"Qwen model version release series generation lineup {year}",
        result_title="Unrelated company home page",
    )
    missing = policy._latest_missing_prerequisites()
    await _complete_search(
        policy,
        "https://qwen.example/models/qwen3.8",
        f"Qwen model release lineup {year}",
        result_title="Qwen3.8 model release",
    )

    assert any("release landscape" in item for item in missing)
    assert policy._release_landscape_search_completed is True
    assert policy._release_landscape_evidence_urls == {"https://qwen.example/models/qwen3.8"}


def test_version_stuffed_landscape_search_is_repaired_before_action(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report PDF")
    invalid = ToolCall(
        tool_name="search",
        parameters={
            "query": "Qwen 2026 model lineup Qwen3.8-Flash-Next releases",
            "recency": "year",
        },
    )
    valid = ToolCall(
        tool_name="search",
        parameters={"query": "qwen model version release lineup 2026", "recency": "year"},
    )

    error = policy.validate_planner_call(invalid)

    assert error is not None
    assert "without any dotted or named model version" in error
    assert "qwen model version release lineup 2026" in error
    assert policy.validate_planner_call(valid) is None


async def test_year_recency_counts_as_current_year_evidence(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")

    await _complete_search(
        policy,
        "https://qwen.example/models/qwen3.8",
        "Qwen model release lineup",
        result_title="Qwen3.8 model release",
        recency="year",
    )

    assert policy._broad_current_year_search_completed is True
    assert policy._release_landscape_search_completed is True


async def test_result_labelled_official_establishes_repository_identity(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")

    await _complete_search(
        policy,
        "https://github.com/QwenLM/Qwen",
        "Qwen technical report",
        result_title="The official repo of Qwen",
    )

    assert policy._official_identity_search_completed is True
    assert policy._official_identity_urls == {"https://github.com/QwenLM/Qwen"}


async def test_owner_scope_can_corroborate_exact_candidate_seen_in_earlier_search(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")
    year = datetime.now(UTC).year
    target = "https://github.com/QwenLM/Qwen3.8-Flash-Next/raw/main/tech_report.pdf"
    owner_only = "https://github.com/QwenLM/Qwen3"

    await _complete_search(policy, target, "Qwen official GitHub repository")
    await _complete_search(
        policy,
        target,
        f"Qwen model release lineup {year}",
        result_title="Qwen3.8 model release",
    )
    await _complete_search(policy, target, "Qwen3.8 technical report")
    await _complete_search(
        policy,
        owner_only,
        "GitHub QwenLM Qwen technical report",
    )
    corroborated = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": target})
    )
    await _complete_search(
        policy,
        target,
        f"GitHub QwenLM Qwen3.8-Flash-Next technical report {year}",
    )
    _observe_official(policy, target)
    allowed = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": target}))

    assert corroborated.allowed is False
    assert allowed.allowed is True


def test_selected_candidate_prefers_declared_repository_download(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")

    policy._record_selected_candidate(
        {
            "candidates": [
                {
                    "url": "https://viewscreen.githubusercontent.com/view/pdf?browser=chrome",
                    "evidence_type": "iframe",
                },
                {
                    "url": (
                        "https://github.com/QwenLM/Qwen3.8-Flash-Next/"
                        "raw/refs/heads/main/tech_report.pdf"
                    ),
                    "evidence_type": "declared_page_metadata",
                },
            ]
        }
    )

    assert policy._selected_candidate_url == (
        "https://github.com/QwenLM/Qwen3.8-Flash-Next/raw/refs/heads/main/tech_report.pdf"
    )


async def test_pdf_navigation_reports_target_owner_identity_and_scope_together(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest Qwen technical report")
    year = datetime.now(UTC).year
    target = "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"

    await _complete_search(policy, "https://qwen.ai/home", "Qwen official website")
    await _complete_search(
        policy,
        target,
        f"GitHub QwenLM Qwen3.8-Flash-Next technical report {year}",
    )
    goto = ToolCall(tool_name="goto", parameters={"url": target})
    goto_decision = await policy.authorize(goto)
    assert goto_decision.allowed is True
    goto_audit = await _record_result(
        policy,
        goto,
        ToolResult(success=True, tool_name="goto", data={"url": target}),
        goto_decision,
    )

    missing = goto_audit["latest_missing_prerequisites"]
    assert goto_audit["selected_candidate_url"] == target
    assert goto_audit["selected_candidate_identity_endorsed"] is False
    assert any("github.com/qwenlm" in item for item in missing)
    assert any("independent scope search" in item for item in missing)

    await _complete_search(
        policy,
        target,
        "Qwen official GitHub QwenLM repository",
    )
    await _complete_search(
        policy,
        "https://qwen.ai/blog?id=qwen3.8",
        f"Qwen model release lineup {year}",
        result_title="Qwen3.8 model release",
    )
    final_audit = await _complete_search(
        policy,
        target,
        f"GitHub QwenLM Qwen3.8-Flash-Next technical report {year}",
    )

    assert final_audit["selected_candidate_identity_endorsed"] is True
    assert final_audit["latest_missing_prerequisites"]  # A SERP is not an official page check.


async def test_executor_reset_clears_policy_evidence(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    registry = ToolRegistry()
    registry.register(_Tool("search", ToolResult(success=True, tool_name="search")))
    executor = ToolExecutor(registry, policy=policy)
    await _complete_search(policy, "https://example.test/known")
    assert executor.planner_evidence_hint() == ""

    executor.reset_policy("new task")
    initial_denied = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.test/known"})
    )
    await _complete_search(policy, "https://example.test/other")
    denied = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.test/known"})
    )

    assert denied.allowed is False
    assert "https://example.test/known" in executor.planner_evidence_hint()
    assert (
        executor.validate_tool_call(
            ToolCall(tool_name="search", parameters={"query": "known page"})
        )
        is not None
    )
    assert "first successful action" in initial_denied.reason


async def test_extracted_search_results_count_as_search_evidence(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://qwen.ai/home", "Qwen official website")
    call = ToolCall(tool_name="get_search_results", parameters={})
    decision = await policy.authorize(call)
    audit = await _record_result(
        policy,
        call,
        ToolResult(
            success=True,
            tool_name="get_search_results",
            data={
                "engine": "bing",
                "query": "Qwen3.8 technical report",
                "results": [
                    {
                        "title": "Qwen3.8-Flash-Next technical report",
                        "link": (
                            "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"
                        ),
                    }
                ],
            },
        ),
        decision,
    )

    assert audit["successful_searches"] == 2
    assert audit["new_urls_observed"] == 1


def test_current_pdf_page_promotes_selected_candidate(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    target = "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"
    browser.page.url = target

    policy._record_current_page_url()

    assert policy._selected_candidate_url == target


async def test_done_requires_requested_pdf_and_figure_analysis(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    policy.reset("Download the PDF and interpret Figure 1")
    evidence_url = "https://example.test/report"
    await _complete_search(policy, evidence_url, "report PDF")
    browser.page.url = evidence_url
    policy._record_current_page_visit()

    before_download = await policy.authorize(
        ToolCall(tool_name="done", parameters={"summary": "finished"})
    )
    artifact = (tmp_path / "artifacts" / "report.pdf").resolve()
    policy._downloaded_paths[artifact] = {"source_url": evidence_url}
    before_figure = await policy.authorize(
        ToolCall(tool_name="done", parameters={"summary": "finished"})
    )
    policy._figure_analysis_completed = True
    allowed = await policy.authorize(ToolCall(tool_name="done", parameters={"summary": "finished"}))

    assert before_download.allowed is False
    assert "download_pdf" in before_download.reason
    assert "pdf_analyze_figure" in before_download.reason
    assert before_figure.allowed is False
    assert "pdf_analyze_figure" in before_figure.reason
    assert allowed.allowed is True


async def test_visible_pdf_link_can_be_downloaded_then_analyzed(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=artifacts)
    await _complete_search(policy, "https://github.com/known/repo")
    browser.page.url = "https://github.com/known/repo"
    pdf_url = "https://raw.githubusercontent.com/known/repo/main/report.pdf"
    inspect = ToolCall(tool_name="inspect_download_links", parameters={})
    inspect_decision = await policy.authorize(inspect)
    await _record_result(
        policy,
        inspect,
        ToolResult(
            success=True,
            tool_name="inspect_download_links",
            data={
                "source_url": browser.page.url,
                "candidate_count": 1,
                "candidates": [{"url": pdf_url, "evidence_type": "dom_attribute"}],
            },
        ),
        inspect_decision,
    )

    call = ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    decision = await policy.authorize(call)
    assert decision.allowed is True
    path = artifacts / "report.pdf"
    audit = await _record_result(
        policy,
        call,
        ToolResult(success=True, tool_name="download_pdf", data={"path": str(path)}),
        decision,
    )
    analyze = await policy.authorize(
        ToolCall(tool_name="pdf_analyze_figure", parameters={"path": str(path)})
    )
    unrelated = await policy.authorize(
        ToolCall(tool_name="pdf_extract_text", parameters={"path": str(tmp_path / "old.pdf")})
    )

    assert audit["downloaded_artifact_count"] == 1
    assert analyze.allowed is True
    assert analyze.provenance["source_url"] == pdf_url
    assert unrelated.allowed is False


async def test_download_file_from_visible_control_grounds_pdf_tools(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=artifacts)
    policy.reset("Find the Qwen3 technical report PDF and interpret Figure 1")
    await _complete_search(policy, "https://github.com/known/repo")
    browser.page.url = "https://github.com/known/repo/blob/main/report.pdf"
    selector = {"type": "css", "value": "[data-testid='download-raw-button']"}
    inspect = ToolCall(tool_name="inspect_download_links", parameters={})
    inspect_decision = await policy.authorize(inspect)
    await _record_result(
        policy,
        inspect,
        ToolResult(
            success=True,
            tool_name="inspect_download_links",
            data={
                "source_url": browser.page.url,
                "candidate_count": 0,
                "candidates": [],
                "download_controls": [{"selector": selector, "element": "button"}],
            },
        ),
        inspect_decision,
    )
    blocked_done = await policy.authorize(
        ToolCall(tool_name="done", parameters={"summary": "found it"})
    )

    pdf_path = artifacts / "downloads" / "report.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")
    call = ToolCall(tool_name="download_file", parameters={"selector": selector})
    decision = await policy.authorize(call)
    assert decision.allowed is True
    audit = await _record_result(
        policy,
        call,
        ToolResult(success=True, tool_name="download_file", data={"path": str(pdf_path)}),
        decision,
    )
    analyze = await policy.authorize(
        ToolCall(tool_name="pdf_analyze_figure", parameters={"path": str(pdf_path)})
    )
    relative = await policy.authorize(
        ToolCall(tool_name="pdf_extract_text", parameters={"path": "downloads/report.pdf"})
    )

    assert "download_file" in blocked_done.reason
    assert audit["downloaded_artifact_count"] == 1
    assert analyze.allowed is True
    assert analyze.provenance["source_tool"] == "download_file"
    assert analyze.provenance["page_url"] == browser.page.url
    assert analyze.provenance["selector"] == selector
    assert relative.allowed is True
    exported = policy.export_state()
    assert exported["downloaded_paths"] == {
        "artifacts/downloads/report.pdf": {
            "source_tool": "download_file",
            "policy_step": audit["policy_step"],
            "page_url": browser.page.url,
        }
    }


async def test_download_file_registers_only_pdf_payloads(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=artifacts)
    await _complete_search(policy, "https://github.com/known/repo")
    browser.page.url = "https://github.com/known/repo"
    (artifacts / "downloads").mkdir(parents=True)
    archive = artifacts / "downloads" / "bundle.zip"
    archive.write_bytes(b"PK\x03\x04")
    headerless = artifacts / "downloads" / "paper.bin"
    headerless.write_bytes(b"%PDF-1.4\n")

    for path in (archive, headerless):
        call = ToolCall(
            tool_name="download_file", parameters={"selector": {"type": "text", "value": "Get"}}
        )
        decision = await policy.authorize(call)
        await _record_result(
            policy,
            call,
            ToolResult(success=True, tool_name="download_file", data={"path": str(path)}),
            decision,
        )

    zip_decision = await policy.authorize(
        ToolCall(tool_name="pdf_extract_text", parameters={"path": str(archive)})
    )
    magic_decision = await policy.authorize(
        ToolCall(tool_name="pdf_extract_text", parameters={"path": str(headerless)})
    )

    assert zip_decision.allowed is False
    assert magic_decision.allowed is True


async def test_download_file_obeys_latest_evidence_gate(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest technical report PDF about Qwen")
    await _complete_search(policy, "https://github.com/known/repo")
    browser.page.url = "https://github.com/known/repo/blob/main/report.pdf"

    decision = await policy.authorize(
        ToolCall(
            tool_name="download_file",
            parameters={"selector": {"type": "css", "value": "[data-testid='download']"}},
        )
    )

    assert decision.allowed is False
    assert "latest/newest evidence is incomplete" in decision.reason


async def test_linked_arxiv_pdf_is_bound_to_official_repository_evidence(
    tmp_path: Path,
) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the most recent technical report PDF about Qwen")
    repository = "https://github.com/QwenLM/Qwen3"
    abs_url = "https://arxiv.org/abs/2505.09388"
    pdf_url = "https://arxiv.org/pdf/2505.09388"
    policy._search_completed = True
    policy._successful_searches = 2
    policy._broad_current_year_search_completed = True
    policy._release_landscape_search_completed = True
    policy._official_identity_search_completed = True
    policy._official_scope_search_completed = True
    policy._version_frontier_resolved = True
    policy._official_identity_urls = {repository}
    policy._official_scope_result_urls = {repository}
    policy._record_url(
        abs_url,
        source="search_planner_visible",
        page_url="https://www.bing.com/search?q=Qwen3+technical+report",
        label="[2505.09388] Qwen3 Technical Report - arXiv.org",
    )
    policy._record_url(abs_url, source="get_all_links_planner_visible", page_url=repository)
    policy._observed_urls[pdf_url] = {
        "source": "inspect_download_links_planner_visible",
        "page_url": abs_url,
    }

    decision = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    )

    assert decision.allowed is True


async def test_arxiv_pdf_without_visible_official_repository_link_remains_denied(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the most recent technical report PDF about Qwen")
    policy._search_completed = True
    policy._successful_searches = 2
    policy._broad_current_year_search_completed = True
    policy._release_landscape_search_completed = True
    policy._official_identity_search_completed = True
    policy._official_scope_search_completed = True
    policy._version_frontier_resolved = True
    policy._official_identity_urls = {"https://github.com/QwenLM/Qwen3"}
    policy._official_scope_result_urls = {"https://github.com/QwenLM/Qwen3"}
    pdf_url = "https://arxiv.org/pdf/2505.09388"
    policy._observed_urls[pdf_url] = {
        "source": "inspect_download_links_planner_visible",
        "page_url": "https://arxiv.org/abs/2505.09388",
    }

    decision = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    )

    assert decision.allowed is False
    assert "arXiv is a paper index" in decision.reason
    assert "do not search for arXiv again" in decision.reason
    assert "https://github.com/QwenLM/Qwen3" in decision.reason
    assert "https://arxiv.org/abs/2505.09388" in decision.reason
    assert "official identity search whose results endorse" not in decision.reason


def _latest_policy_with_endorsed_official_site(tmp_path: Path) -> SearchEngineOnlyPolicy:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the most recent technical report PDF about Qwen")
    policy._search_completed = True
    policy._successful_searches = 2
    policy._broad_current_year_search_completed = True
    policy._release_landscape_search_completed = True
    policy._official_identity_search_completed = True
    policy._official_scope_search_completed = True
    policy._version_frontier_resolved = True
    policy._official_identity_urls = {
        "https://qwen.ai/blog?id=qwen3.8",
        "https://github.com/QwenLM/Qwen",
    }
    policy._official_scope_result_urls = {
        "https://qwen.ai/home",
        "https://github.com/QwenLM/Qwen",
        "https://github.com/QwenLM/Qwen3",
        "https://zhuanlan.zhihu.com/p/1",
    }
    return policy


async def test_arxiv_pdf_linked_from_endorsed_official_website_is_allowed(
    tmp_path: Path,
) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    abs_url = "https://arxiv.org/abs/2505.09388"
    pdf_url = "https://arxiv.org/pdf/2505.09388"
    policy._record_url(
        abs_url,
        source="search_planner_visible",
        page_url="https://www.bing.com/search?q=Qwen3+technical+report",
        label="[2505.09388] Qwen3 Technical Report - arXiv.org",
    )
    policy._record_url(
        abs_url, source="get_all_links_planner_visible", page_url="https://qwen.ai/publication"
    )
    policy._observed_urls[pdf_url] = {
        "source": "inspect_download_links_planner_visible",
        "page_url": abs_url,
    }

    decision = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    )

    assert decision.allowed is True


async def test_arxiv_pdf_linked_from_unendorsed_third_party_page_stays_denied(
    tmp_path: Path,
) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    abs_url = "https://arxiv.org/abs/2505.09388"
    pdf_url = "https://arxiv.org/pdf/2505.09388"
    policy._record_url(
        abs_url, source="get_all_links_planner_visible", page_url="https://zhuanlan.zhihu.com/p/1"
    )
    policy._observed_urls[pdf_url] = {
        "source": "inspect_download_links_planner_visible",
        "page_url": abs_url,
    }

    decision = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    )

    assert decision.allowed is False
    assert "arXiv is a paper index" in decision.reason
    assert "https://qwen.ai/home" in decision.reason or "QwenLM" in decision.reason


def test_cross_site_link_context_outranks_self_hosted_observation(tmp_path: Path) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    abs_url = "https://arxiv.org/abs/2505.09388"
    repository = "https://github.com/QwenLM/Qwen3"

    policy._record_url(abs_url, source="inspect_download_links_planner_visible", page_url=abs_url)
    policy._record_url(abs_url, source="get_all_links_planner_visible", page_url=repository)
    assert policy._observed_urls[abs_url]["page_url"] == repository

    policy._record_url(abs_url, source="inspect_download_links_planner_visible", page_url=abs_url)
    assert policy._observed_urls[abs_url]["page_url"] == repository

    policy._record_url(abs_url, source="get_all_links_planner_visible", page_url="https://x.test/")
    assert policy._observed_urls[abs_url]["page_url"] == repository


async def test_unendorsed_arxiv_candidate_hint_and_preflight(tmp_path: Path) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    abs_url = "https://arxiv.org/abs/2505.09388"
    pdf_url = "https://arxiv.org/pdf/2505.09388"
    policy._record_url(
        abs_url,
        source="search_planner_visible",
        page_url="https://www.bing.com/search?q=Qwen3+technical+report",
        label="[2505.09388] Qwen3 Technical Report - arXiv.org",
    )
    policy._browser.page.url = abs_url
    inspect = ToolCall(tool_name="inspect_download_links", parameters={})
    inspect_decision = await policy.authorize(inspect)
    audit = await _record_result(
        policy,
        inspect,
        ToolResult(
            success=True,
            tool_name="inspect_download_links",
            data={
                "source_url": abs_url,
                "candidate_count": 1,
                "candidates": [{"url": pdf_url, "evidence_type": "dom_attribute"}],
            },
        ),
        inspect_decision,
    )

    hint = policy.planner_evidence_hint()
    download = ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    assert policy.validate_planner_call(download) is None
    denied = await policy.authorize(download)
    await _record_result(
        policy, download, ToolResult(success=False, tool_name="download_pdf"), denied
    )
    repeat_preflight = policy.validate_planner_call(download)
    unrelated_preflight = policy.validate_planner_call(
        ToolCall(tool_name="download_pdf", parameters={"url": "https://example.test/x.pdf"})
    )

    assert audit["selected_candidate_url"] == pdf_url
    assert policy._discovery._arxiv_link_guidance(pdf_url) in audit["latest_missing_prerequisites"]
    assert any("date" in item for item in audit["latest_missing_prerequisites"])
    assert "CANDIDATE EVIDENCE INCOMPLETE for https://arxiv.org/pdf/2505.09388" in hint
    assert "https://github.com/QwenLM/Qwen3" in hint
    assert denied.allowed is False
    assert repeat_preflight is not None
    assert "already denied" in repeat_preflight and "arXiv is a paper index" in repeat_preflight
    assert unrelated_preflight is None

    repository = "https://github.com/QwenLM/Qwen3"
    goto = ToolCall(tool_name="goto", parameters={"url": repository})
    policy._record_url(repository, source="search_planner_visible", page_url="https://b.test/")
    goto_decision = await policy.authorize(goto)
    policy._browser.page.url = repository
    await _record_result(
        policy,
        goto,
        ToolResult(success=True, tool_name="goto", data={"url": repository, "title": "Qwen3"}),
        goto_decision,
    )
    assert policy.validate_planner_call(download) is None
    policy._record_url(abs_url, source="get_all_links_planner_visible", page_url=repository)

    assert "report-file date" in policy.planner_evidence_hint()
    assert (await policy.authorize(download)).allowed is True


async def test_off_subject_paper_linked_from_official_page_is_denied(tmp_path: Path) -> None:
    """An official blog links to many papers; only one about the subject is admissible."""
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    blog = "https://qwen.ai/blog?id=qwen3.8"
    abs_url = "https://arxiv.org/abs/2605.22389"
    pdf_url = "https://arxiv.org/pdf/2605.22389"
    policy._record_url(blog, source="search_planner_visible", page_url="https://b.test/")
    policy._browser.page.url = blog
    links = ToolCall(tool_name="get_all_links", parameters={})
    await _record_result(
        policy,
        links,
        ToolResult(
            success=True,
            tool_name="get_all_links",
            data={
                "links": [{"href": abs_url, "text": "“Unified Data Selection for LLM Reasoning”"}]
            },
        ),
        await policy.authorize(links),
    )

    goto = ToolCall(tool_name="goto", parameters={"url": abs_url})
    goto_decision = await policy.authorize(goto)
    assert goto_decision.allowed is True
    policy._browser.page.url = abs_url
    audit = await _record_result(
        policy,
        goto,
        ToolResult(
            success=True,
            tool_name="goto",
            data={"url": abs_url, "title": "[2605.22389] Unified Data Selection for LLM Reasoning"},
        ),
        goto_decision,
    )
    policy._record_url(pdf_url, source="get_attribute_planner_visible", page_url=abs_url)

    hint = policy.planner_evidence_hint()
    denied = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": pdf_url}))

    assert audit["selected_candidate_url"] == abs_url
    assert f"CANDIDATE EVIDENCE INCOMPLETE for {abs_url}" in hint
    assert "not visibly about the task subject (qwen)" in hint
    assert denied.allowed is False
    assert "Unified Data Selection for LLM Reasoning" in denied.reason
    assert "arXiv is a paper index" not in denied.reason


def test_observed_pdf_inspection_precedes_sibling_searches(tmp_path):
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    target = "https://qwen.ai/Model3.8/report.pdf"
    policy._selected_candidate_url = target
    policy._candidate_ledger.inspect(
        {"source_url": "https://qwen.ai/repo", "candidates": [{"url": target}]}, official=True
    )
    call = ToolCall(tool_name="search", parameters={"query": "another release"})
    assert "inspect the observed report file" in policy.planner_evidence_hint()
    assert "goto" in policy.validate_planner_call(call)
    assert "NEXT UNRESOLVED" not in policy.planner_evidence_hint()
    policy._candidate_ledger.inspection_attempt(target, False)
    assert policy.validate_planner_call(call) is None


def test_latest_completion_requires_selected_file_date(tmp_path: Path) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    target = "https://qwen.ai/Qwen3.8/report.pdf"
    policy._selected_candidate_url = target
    policy._candidate_ledger.inspect(
        {
            "source_url": target,
            "candidates": [{"url": target}],
            "date_evidence": [{"datetime": "2026-08-26T12:29:38Z"}],
        },
        official=True,
    )
    vague = ToolCall(tool_name="done", parameters={"summary": "Updated August 26–27."})
    assert "exact ISO date" in (policy.validate_planner_call(vague) or "")
    dated = ToolCall(tool_name="done", parameters={"summary": "Report file updated 2026-08-26."})
    assert policy.validate_planner_call(dated) is None


async def test_document_title_seen_on_its_own_page_binds_the_subject(tmp_path: Path) -> None:
    """A badge link has no text; opening the paper page supplies the title evidence."""
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    repository = "https://github.com/QwenLM/Qwen3"
    abs_url = "https://arxiv.org/abs/2505.09388"
    pdf_url = "https://arxiv.org/pdf/2505.09388"
    policy._record_url(abs_url, source="get_all_links_planner_visible", page_url=repository)
    policy._record_url(pdf_url, source="get_attribute_planner_visible", page_url=abs_url)
    download = ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})

    denied = await policy.authorize(download)
    assert denied.allowed is False
    assert "no title or link text has been observed" in denied.reason

    goto = ToolCall(tool_name="goto", parameters={"url": abs_url})
    goto_decision = await policy.authorize(goto)
    policy._browser.page.url = abs_url
    await _record_result(
        policy,
        goto,
        ToolResult(
            success=True,
            tool_name="goto",
            data={"url": abs_url, "title": "[2505.09388] Qwen3 Technical Report"},
        ),
        goto_decision,
    )

    assert (await policy.authorize(download)).allowed is True


def test_arxiv_guidance_ranks_official_pages_by_document_title_overlap(tmp_path: Path) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    policy._official_identity_urls |= {"https://github.com/QwenLM/Qwen3.8"}
    policy._official_scope_result_urls |= {"https://github.com/QwenLM/Qwen3.8"}
    abs_url = "https://arxiv.org/abs/2505.09388"
    policy._record_url(
        abs_url,
        source="search_planner_visible",
        page_url="https://b.test/",
        label="[2505.09388] Qwen3 Technical Report - arXiv.org",
    )

    guidance = policy._discovery._arxiv_link_guidance("https://arxiv.org/pdf/2505.09388")

    assert guidance is not None
    assert guidance.index("https://github.com/QwenLM/Qwen3,") < guidance.index(
        "https://github.com/QwenLM/Qwen,"
    )
    assert guidance.index("https://github.com/QwenLM/Qwen,") < guidance.index(
        "https://github.com/QwenLM/Qwen3.8"
    )


async def test_pdf_hosted_by_the_official_host_needs_no_subject_label(tmp_path: Path) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    pdf_url = "https://qwen.ai/files/tr-2026.pdf"
    policy._record_url(
        pdf_url, source="get_all_links_planner_visible", page_url="https://qwen.ai/home"
    )

    decision = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": pdf_url})
    )

    assert decision.allowed is True
    assert policy._discovery._document_needs_subject_binding(pdf_url) is False


def test_subject_labels_survive_checkpoint_and_reject_malformed_maps(tmp_path: Path) -> None:
    task = "Find the most recent technical report PDF about Qwen"
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset(task)
    abs_url = "https://arxiv.org/abs/2505.09388"
    policy._record_url(
        abs_url, source="search_planner_visible", page_url="https://b.test/", label="Qwen3 Report"
    )
    state = policy.export_state()
    assert state["observed_labels"] == {abs_url: ["Qwen3 Report"]}

    restored = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    restored.import_state(state, task=task)
    assert restored._discovery._document_names_subject(abs_url) is True

    legacy = {key: value for key, value in state.items() if key != "observed_labels"}
    restored.import_state(legacy, task=task)
    assert restored._observed_labels == {}

    broken = dict(state)
    broken["observed_labels"] = {abs_url: ["ok", 5]}
    try:
        restored.import_state(broken, task=task)
    except ValueError as error:
        assert "invalid labels" in str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("malformed label map must fail closed")


async def test_identity_search_endorses_subject_branded_and_query_named_hosts(
    tmp_path: Path,
) -> None:
    policy = SearchEngineOnlyPolicy(_Browser(), artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the most recent technical report PDF about Qwen")
    call = ToolCall(tool_name="search", parameters={"query": "Qwen technical report 2026"})
    decision = await policy.authorize(call)
    await _record_result(
        policy,
        call,
        ToolResult(
            success=True,
            tool_name="search",
            data={
                "url": "https://www.bing.com/search?q=Qwen",
                "results": [
                    {"title": "Qwen", "url": "https://qwen.ai/home", "snippet": "officially"},
                    {"title": "Qwen mirror", "url": "https://qwen-mirror.example/"},
                    {
                        "title": "GitHub - QwenLM/Qwen: official repo",
                        "url": "https://github.com/QwenLM/Qwen",
                    },
                    {"title": "知乎", "url": "https://www.zhihu.com/question/1"},
                    {"title": "Qwen3 report", "url": "https://arxiv.org/abs/2505.09388"},
                ],
            },
        ),
        decision,
    )
    assert policy._official_identity_urls == {
        "https://qwen.ai/home",
        "https://github.com/QwenLM/Qwen",
    }

    named = ToolCall(
        tool_name="search", parameters={"query": "Qwen official website huggingface.co"}
    )
    decision = await policy.authorize(named)
    await _record_result(
        policy,
        named,
        ToolResult(
            success=True,
            tool_name="search",
            data={
                "url": "https://www.bing.com/search?q=Qwen+official",
                "results": [
                    {"title": "Qwen (Qwen)", "url": "https://huggingface.co/Qwen"},
                    {"title": "知乎", "url": "https://www.zhihu.com/question/2"},
                ],
            },
        ),
        decision,
    )
    assert "https://huggingface.co/Qwen" in policy._official_identity_urls
    assert "https://www.zhihu.com/question/2" not in policy._official_identity_urls


async def test_download_file_binds_to_the_current_page_host(tmp_path: Path) -> None:
    policy = _latest_policy_with_endorsed_official_site(tmp_path)
    selector = {"type": "text", "value": "Download report PDF"}
    call = ToolCall(tool_name="download_file", parameters={"selector": selector})

    policy._browser.page.url = "https://www.zhihu.com/question/1"
    third_party = await policy.authorize(call)
    policy._browser.page.url = "https://qwen.ai/blog?id=qwen3.8"
    official = await policy.authorize(call)

    assert third_party.allowed is False
    assert "zhihu.com" in third_party.reason
    assert official.allowed is True


async def test_html_source_page_is_not_selected_as_candidate(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    policy.reset("Find the latest technical report PDF about Qwen")
    await _complete_search(policy, "https://qwen.ai/blog")
    inspect = ToolCall(tool_name="inspect_download_links", parameters={})
    for page_url, expected in (
        ("https://qwen.ai/blog?id=qwen3.8", None),
        (
            "https://github.com/QwenLM/Qwen3/blob/main/Report.pdf",
            "https://github.com/QwenLM/Qwen3/blob/main/Report.pdf",
        ),
    ):
        browser.page.url = page_url
        decision = await policy.authorize(inspect)
        audit = await _record_result(
            policy,
            inspect,
            ToolResult(
                success=True,
                tool_name="inspect_download_links",
                data={
                    "source_url": page_url,
                    "candidate_count": 0,
                    "candidates": [],
                    "download_controls": [{"selector": {"type": "text", "value": "Download"}}],
                },
            ),
            decision,
        )
        assert audit["selected_candidate_url"] == expected


async def test_repeated_denied_call_is_preflighted_until_evidence_changes(
    tmp_path: Path,
) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    await _complete_search(policy, "https://github.com/known/repo")
    guessed = ToolCall(tool_name="goto", parameters={"url": "https://github.com/known/repo/x"})

    assert policy.validate_planner_call(guessed) is None
    denied = await policy.authorize(guessed)
    await _record_result(policy, guessed, ToolResult(success=False, tool_name="goto"), denied)
    preflight = policy.validate_planner_call(guessed)
    assert denied.allowed is False
    assert preflight is not None and "already denied" in preflight
    assert denied.reason in preflight

    links = ToolCall(tool_name="get_all_links", parameters={})
    allowed = await policy.authorize(links)
    await _record_result(
        policy,
        links,
        ToolResult(success=True, tool_name="get_all_links", data={"links": []}),
        allowed,
    )

    assert policy.validate_planner_call(guessed) is None


async def test_failed_preview_cannot_ground_raw_link_but_explicit_inspection_can(
    tmp_path: Path,
) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    preview = "https://github.com/known/repo/blob/main/report.pdf"
    raw = "https://github.com/known/repo/raw/refs/heads/main/report.pdf"
    await _complete_search(policy, preview)

    call = ToolCall(tool_name="download_pdf", parameters={"url": preview})
    decision = await policy.authorize(call)
    await _record_result(
        policy,
        call,
        ToolResult(
            success=False,
            tool_name="download_pdf",
            data={"source_url": preview, "suggested_download_urls": [raw]},
        ),
        decision,
    )
    denied_retry = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": raw})
    )
    inspect = ToolCall(tool_name="inspect_download_links", parameters={})
    inspect_decision = await policy.authorize(inspect)
    await _record_result(
        policy,
        inspect,
        ToolResult(
            success=True,
            tool_name="inspect_download_links",
            data={
                "source_url": preview,
                "candidate_count": 1,
                "candidates": [{"url": raw, "evidence_type": "declared_page_metadata"}],
            },
        ),
        inspect_decision,
    )
    retry = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": raw}))

    assert denied_retry.allowed is False
    assert retry.allowed is True
    assert retry.provenance["source"] == "inspect_download_links_planner_visible"


async def test_executor_hides_and_denies_specialized_discovery_tools(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    registry = ToolRegistry()
    registry.register(_Tool("search", ToolResult(success=False, tool_name="search")))
    registry.register(
        _Tool(
            "official_report_search", ToolResult(success=True, tool_name="official_report_search")
        )
    )
    executor = ToolExecutor(registry, policy=policy)

    descriptions = executor.get_tool_descriptions()
    denied = await executor.execute(ToolCall(tool_name="official_report_search"))

    assert "SEARCH ENGINE ONLY" in descriptions
    assert "search: search description" in descriptions
    assert "official_report_search description" not in descriptions
    assert denied.success is False
    assert denied.audit["policy"] == "search_engine_only"
    assert denied.audit["decision"] == "deny"


async def test_executor_fails_closed_when_policy_crashes() -> None:
    class _BrokenPolicy:
        name = "broken"
        allowed_tools = {"search"}
        prompt_notice = "broken"

        async def authorize(self, _tool_call: ToolCall):
            raise RuntimeError("policy unavailable")

        async def record_result(self, *_args: object) -> dict[str, Any]:
            return {}

        def denial_audit(self, _tool_name: str, _reason: str) -> dict[str, Any]:
            return {}

    registry = ToolRegistry()
    registry.register(_Tool("search", ToolResult(success=True, tool_name="search")))
    executor = ToolExecutor(registry, policy=_BrokenPolicy())

    result = await executor.execute(ToolCall(tool_name="search", parameters={"query": "x"}))

    assert result.success is False
    assert "failed closed" in result.error
    assert result.audit == {"policy": "broken", "decision": "deny"}


async def test_executor_fails_closed_when_policy_evidence_recording_crashes() -> None:
    class _BrokenRecorder:
        name = "broken-recorder"
        allowed_tools = {"search"}
        prompt_notice = "broken"

        async def authorize(self, _tool_call: ToolCall):
            return PolicyDecision(True, "allowed", 1)

        async def record_result(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            raise RuntimeError("audit unavailable")

        def denial_audit(self, _tool_name: str, _reason: str) -> dict[str, Any]:
            return {}

    registry = ToolRegistry()
    registry.register(_Tool("search", ToolResult(success=True, tool_name="search")))
    executor = ToolExecutor(registry, policy=_BrokenRecorder())

    result = await executor.execute(ToolCall(tool_name="search", parameters={"query": "x"}))

    assert result.success is False
    assert "recording evidence" in (result.error or "")
    assert result.audit == {"policy": "broken-recorder", "decision": "deny"}

"""Tests for search-engine-only anti-shortcut enforcement."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from webagent.agent.context import planner_result_preview
from webagent.core.models import ToolCall, ToolResult
from webagent.tools.executor import ToolExecutor
from webagent.tools.policy import PolicyDecision, SearchEngineOnlyPolicy
from webagent.tools.registry import ToolRegistry


class _Page:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.hrefs: list[str] = []

    async def eval_on_selector_all(self, _selector: str, _script: str) -> list[str]:
        return list(self.hrefs)


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


async def _complete_search(
    policy: SearchEngineOnlyPolicy,
    url: str,
    query: str = "report",
    *,
    result_title: str = "",
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
            "results": [{"title": result_title, "url": url}],
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

    await policy.authorize(ToolCall(tool_name="get_url", parameters={}))
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
    assert denied.allowed is False
    assert "absent from browser evidence" in denied.reason


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

    allowed = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": official_pdf})
    )

    assert allowed.allowed is True


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
    assert resolved.allowed is True


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
    allowed = await policy.authorize(ToolCall(tool_name="download_pdf", parameters={"url": target}))

    assert corroborated.allowed is True
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
    assert final_audit["latest_missing_prerequisites"] == []


async def test_executor_reset_clears_policy_evidence(tmp_path: Path) -> None:
    browser = _Browser()
    policy = SearchEngineOnlyPolicy(browser, artifacts_dir=tmp_path / "artifacts")
    registry = ToolRegistry()
    registry.register(_Tool("search", ToolResult(success=True, tool_name="search")))
    executor = ToolExecutor(registry, policy=policy)
    await _complete_search(policy, "https://example.test/known")

    executor.reset_policy("new task")
    denied = await policy.authorize(
        ToolCall(tool_name="goto", parameters={"url": "https://example.test/known"})
    )

    assert denied.allowed is False
    assert "first successful action" in denied.reason


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

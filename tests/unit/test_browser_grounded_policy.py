"""Default browser-grounded mode binds navigation and downloads to evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


async def test_hybrid_policy_allows_direct_discovery_without_browser_search(
    tmp_path: Path,
) -> None:
    policy = BrowserGroundedPolicy(
        _Browser(),
        artifacts_dir=tmp_path / "artifacts",
        allowed_tools={"official_report_search", "goto", "done"},
        require_browser_search=False,
    )
    policy.reset("Find the latest Qwen technical report")

    decision = await policy.authorize(
        ToolCall(tool_name="official_report_search", parameters={"subject": "Qwen"})
    )

    assert decision.allowed is True
    assert "official_report_search first" in policy.prompt_notice
    assert "exactly one" in policy.prompt_notice


def _hybrid_policy(tmp_path: Path, **kwargs: Any) -> BrowserGroundedPolicy:
    return BrowserGroundedPolicy(
        _Browser(),
        artifacts_dir=tmp_path / "artifacts",
        allowed_tools={"official_report_search", "search", "download_pdf", "done"},
        require_browser_search=False,
        **kwargs,
    )


def _official_report_result() -> ToolResult:
    return ToolResult(
        success=True,
        tool_name="official_report_search",
        data={
            "subject": "Qwen",
            "official_owner": "QwenLM",
            "verified_first_party_candidates": [
                {
                    "source": "github",
                    "title": "QwenLM/Qwen3.8-Flash-Next",
                    "date": "2026-08-26T12:29:38Z",
                    "pdf_url": (
                        "https://raw.githubusercontent.com/QwenLM/"
                        "Qwen3.8-Flash-Next/main/tech_report.pdf"
                    ),
                    "html_url": (
                        "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf"
                    ),
                    "first_party": True,
                }
            ],
        },
    )


async def _record_official_report(policy: BrowserGroundedPolicy) -> dict[str, object]:
    call = ToolCall(
        tool_name="official_report_search",
        parameters={"subject": "Qwen", "official_owner": "QwenLM"},
    )
    result = _official_report_result()
    await _record(policy, call, result)
    return result.data


async def test_hybrid_exact_owner_report_and_commit_satisfy_identity_scope(
    tmp_path: Path,
) -> None:
    policy = _hybrid_policy(tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    await _record_official_report(policy)
    target = "https://raw.githubusercontent.com/QwenLM/Qwen3.8-Flash-Next/main/tech_report.pdf"

    missing = policy._latest_missing_prerequisites()
    early_download = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": target})
    )

    assert policy._official_identity_search_completed is True
    assert policy._official_scope_search_completed is True
    assert policy._selected_candidate_url == target
    assert policy._hybrid_candidate_date == "2026-08-26T12:29:38Z"
    assert len(missing) == 1
    assert "release landscape" in missing[0]
    assert early_download.allowed is False
    assert "required next action" in early_download.reason


async def test_hybrid_landscape_cross_check_forces_download_progress(tmp_path: Path) -> None:
    policy = _hybrid_policy(tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    await _record_official_report(policy)
    target = policy._selected_candidate_url
    assert target is not None
    year = datetime.now(UTC).year
    await _record(
        policy,
        ToolCall(
            tool_name="search",
            parameters={
                "query": f"Qwen model version release lineup {year}",
                "recency": "year",
            },
        ),
        ToolResult(
            success=True,
            tool_name="search",
            data={
                "query": f"Qwen model version release lineup {year}",
                "results": [
                    {
                        "title": "Qwen3.8 model release",
                        "url": "https://qwen.ai/blog?id=qwen3.8",
                    }
                ],
            },
        ),
    )

    redundant_search = await policy.authorize(
        ToolCall(tool_name="search", parameters={"query": "Qwen report again"})
    )
    download = await policy.authorize(
        ToolCall(tool_name="download_pdf", parameters={"url": target})
    )

    assert policy._latest_missing_prerequisites() == ()
    assert redundant_search.allowed is False
    assert redundant_search.provenance["required_next_action"] == {
        "tool": "download_pdf",
        "parameters": {"url": target},
    }
    assert download.allowed is True


async def test_hybrid_repeated_gap_requires_exact_action_then_ends_corroboration(
    tmp_path: Path,
) -> None:
    policy = _hybrid_policy(tmp_path, evidence_repeat_limit=3)
    policy.reset("Find the latest Qwen technical report PDF")
    await _record_official_report(policy)

    await _record(
        policy,
        ToolCall(tool_name="search", parameters={"query": "Qwen unrelated lookup"}),
        ToolResult(
            success=True,
            tool_name="search",
            data={"query": "Qwen unrelated lookup", "results": [{"url": "https://qwen.ai/"}]},
        ),
    )
    denied = await policy.authorize(
        ToolCall(tool_name="search", parameters={"query": "another rewrite"})
    )
    action = denied.provenance["required_next_action"]
    exact_call = ToolCall(tool_name=action["tool"], parameters=action["parameters"])
    await _record(
        policy,
        exact_call,
        ToolResult(
            success=True,
            tool_name="search",
            data={
                "query": action["parameters"]["query"],
                "results": [{"title": "No matching release", "url": "https://example.test/"}],
            },
        ),
    )

    assert denied.allowed is False
    assert policy._hybrid_cross_check_exhausted is True
    assert policy._latest_missing_prerequisites() == ()
    assert policy._hybrid_required_next_action["tool"] == "download_pdf"


async def test_hybrid_limits_repeated_official_report_subject_family(tmp_path: Path) -> None:
    policy = _hybrid_policy(tmp_path)
    policy.reset("Find the latest Qwen technical report PDF")
    await _record_official_report(policy)

    duplicate = await policy.authorize(
        ToolCall(
            tool_name="official_report_search",
            parameters={"subject": "Qwen3.8-Flash-Next", "official_owner": "QwenLM"},
        )
    )

    assert duplicate.allowed is False
    assert "already exhausted" in duplicate.reason
    assert duplicate.provenance["required_next_action"]["tool"] == "search"


async def test_hybrid_policy_checkpoint_restores_direct_evidence(tmp_path: Path) -> None:
    policy = _hybrid_policy(tmp_path)
    task = "Find the latest Qwen technical report PDF"
    policy.reset(task)
    await _record_official_report(policy)
    state = policy.export_state()

    restored = _hybrid_policy(tmp_path)
    restored.import_state(state, task=task)

    assert restored._hybrid_direct_evidence_verified is True
    assert restored._hybrid_candidate_owner == "qwenlm"
    assert restored._selected_candidate_url == policy._selected_candidate_url

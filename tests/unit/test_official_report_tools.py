"""Tests for generic multi-source official-report discovery."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from webagent.core.config import AgentConfig
from webagent.core.models import ToolResult
from webagent.tools.builtin.arxiv_tools import ArxivSearchTool
from webagent.tools.builtin.github_tools import GitHubSearchTool
from webagent.tools.builtin.official_report_tools import OfficialReportSearchTool


async def test_combines_sources_and_only_verifies_exact_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_arxiv(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name="arxiv_search",
            data={
                "results": [
                    {
                        "title": "Aurora Technical Report",
                        "published": "2025-02-01",
                        "pdf_url": "https://arxiv.test/aurora.pdf",
                        "abs_url": "https://arxiv.test/aurora",
                        "authors": ["Researcher"],
                    },
                    {
                        "title": "A parser built with Aurora",
                        "published": "2026-01-01",
                        "pdf_url": "https://arxiv.test/parser.pdf",
                    },
                ]
            },
        )

    async def fake_github(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name="github_search",
            data={
                "candidates": [
                    {
                        "repository": "AcmeAI/Aurora-Next",
                        "filename": "tech_report.pdf",
                        "committed_at": "2025-03-01T00:00:00Z",
                        "download_url": "https://raw.test/aurora.pdf",
                        "html_url": "https://github.test/AcmeAI/Aurora-Next",
                        "first_party": True,
                    }
                ]
            },
        )

    monkeypatch.setattr(ArxivSearchTool, "execute", fake_arxiv)
    monkeypatch.setattr(GitHubSearchTool, "execute", fake_github)

    result = await OfficialReportSearchTool().execute(
        {"subject": "Aurora", "official_owner": "AcmeAI"}
    )

    assert result.success
    verified = result.data["verified_first_party_candidates"]
    assert len(verified) == 1
    assert verified[0]["source"] == "github"
    assert verified[0]["date"] == "2025-03-01T00:00:00Z"
    arxiv = [item for item in result.data["all_candidates"] if item["source"] == "arxiv"]
    assert len(arxiv) == 1
    assert arxiv[0]["first_party"] is False


async def test_reports_both_source_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_arxiv(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(success=False, tool_name="arxiv_search", error="offline")

    async def fail_github(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(success=False, tool_name="github_search", error="limited")

    monkeypatch.setattr(ArxivSearchTool, "execute", fail_arxiv)
    monkeypatch.setattr(GitHubSearchTool, "execute", fail_github)

    result = await OfficialReportSearchTool().execute({"subject": "Mistral"})

    assert not result.success
    assert result.data["source_status"] == {"arxiv": "offline", "github": "limited"}


def test_validation() -> None:
    tool = OfficialReportSearchTool()
    with pytest.raises(ValueError):
        tool.validate_params({})
    with pytest.raises(ValueError):
        tool.validate_params({"subject": "Aurora", "official_owner": ""})


async def test_github_technical_report_filename_with_underscores_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_arxiv(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, tool_name="arxiv_search", data={"results": []})

    async def fake_github(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name="github_search",
            data={
                "candidates": [
                    {
                        "repository": "AcmeAI/Aurora3",
                        "filename": "Aurora3_Technical_Report.pdf",
                        "committed_at": "2029-01-01T00:00:00Z",
                        "first_party": True,
                    }
                ]
            },
        )

    monkeypatch.setattr(ArxivSearchTool, "execute", fake_arxiv)
    monkeypatch.setattr(GitHubSearchTool, "execute", fake_github)

    result = await OfficialReportSearchTool().execute(
        {"subject": "Aurora3", "official_owner": "AcmeAI"}
    )

    assert result.success
    assert len(result.data["verified_first_party_candidates"]) == 1


async def test_redundant_report_words_are_removed_from_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    async def fake_arxiv(self: Any, params: dict[str, Any]) -> ToolResult:
        seen["arxiv"] = params["query"]
        return ToolResult(success=True, tool_name="arxiv_search", data={"results": []})

    async def fake_github(self: Any, params: dict[str, Any]) -> ToolResult:
        seen["github"] = params["query"]
        return ToolResult(
            success=True,
            tool_name="github_search",
            data={
                "candidates": [
                    {
                        "repository": "AcmeAI/Aurora",
                        "filename": "tech_report.pdf",
                        "committed_at": "2030-01-01T00:00:00Z",
                        "first_party": True,
                    }
                ]
            },
        )

    monkeypatch.setattr(ArxivSearchTool, "execute", fake_arxiv)
    monkeypatch.setattr(GitHubSearchTool, "execute", fake_github)

    result = await OfficialReportSearchTool().execute(
        {"subject": "Aurora technical report PDF", "official_owner": "AcmeAI"}
    )

    assert result.success
    assert result.data["subject"] == "Aurora"
    assert result.data["requested_subject"] == "Aurora technical report PDF"
    assert seen == {
        "arxiv": "Aurora technical report",
        "github": "Aurora technical report PDF",
    }


async def test_slow_source_is_bounded_without_discarding_fast_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_arxiv(self: Any, params: dict[str, Any]) -> ToolResult:
        await asyncio.sleep(1)
        raise AssertionError("wait_for should cancel the slow source")

    async def fast_github(self: Any, params: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=True,
            tool_name="github_search",
            data={
                "candidates": [
                    {
                        "repository": "AcmeAI/Aurora",
                        "filename": "tech_report.pdf",
                        "committed_at": "2030-01-01T00:00:00Z",
                        "first_party": True,
                    }
                ]
            },
        )

    monkeypatch.setattr(ArxivSearchTool, "execute", slow_arxiv)
    monkeypatch.setattr(GitHubSearchTool, "execute", fast_github)
    tool = OfficialReportSearchTool(
        config=AgentConfig(
            _env_file=None,
            official_report_source_timeout_seconds=0.1,
        )
    )

    started = time.monotonic()
    result = await tool.execute({"subject": "Aurora", "official_owner": "AcmeAI"})

    assert time.monotonic() - started < 0.5
    assert result.success
    assert result.data["verified_first_party_candidates"]
    assert result.data["source_status"]["arxiv"] == "arxiv timed out after 0.1s"
    assert result.data["source_status"]["github"] == "ok"

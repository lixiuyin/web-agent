"""Tests for structured GitHub report discovery."""

from __future__ import annotations

from typing import Any

import pytest

from webagent.tools.builtin.github_tools import (
    GitHubSearchTool,
    _report_pdf_paths,
    _repository_query,
)


def test_report_pdf_paths_filters_and_prefers_shallow_files() -> None:
    tree = [
        {"type": "blob", "path": "assets/logo.pdf"},
        {"type": "blob", "path": "docs/archive/technical_report.pdf"},
        {"type": "blob", "path": "tech_report.pdf"},
        {"type": "tree", "path": "report.pdf"},
    ]
    assert _report_pdf_paths(tree) == [
        "tech_report.pdf",
        "docs/archive/technical_report.pdf",
    ]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Qwen technical report PDF latest", "Qwen"),
        ("Qwen3.5-Omni technical report", "Qwen"),
        ("Mistral7B technical report PDF latest", "Mistral"),
        ("最新 Qwen 技术报告 PDF", "Qwen"),
    ],
)
def test_repository_query_removes_file_intent_and_version_lock_in(
    query: str, expected: str
) -> None:
    assert _repository_query(query) == expected


class _GitHubTool(GitHubSearchTool):
    def __init__(self, responses: dict[str, Any]) -> None:
        super().__init__()
        self.responses = responses
        self.urls: list[str] = []

    async def _get_json(self, url: str) -> Any:
        self.urls.append(url)
        for marker, response in self.responses.items():
            if marker in url:
                return response
        raise AssertionError(f"unexpected URL: {url}")


def _responses() -> dict[str, Any]:
    return {
        "/search/repositories": {
            "items": [
                {
                    "full_name": "AcmeAI/Aurora-Next",
                    "default_branch": "main",
                    "description": "Aurora model",
                    "html_url": "https://github.com/AcmeAI/Aurora-Next",
                    "created_at": "2025-01-01T02:50:39Z",
                    "pushed_at": "2025-01-03T07:31:36Z",
                }
            ]
        },
        "/git/trees/main": {
            "tree": [
                {"type": "blob", "path": "README.md"},
                {"type": "blob", "path": "tech_report.pdf"},
            ]
        },
        "/commits?path=tech_report.pdf": [
            {"commit": {"committer": {"date": "2025-01-02T12:29:38Z"}}}
        ],
    }


async def test_finds_first_party_report_with_file_date_and_raw_url() -> None:
    tool = _GitHubTool(_responses())

    result = await tool.execute({"query": "Aurora", "owner": "AcmeAI"})

    assert result.success is True
    candidate = result.data["candidates"][0]
    assert candidate["first_party"] is True
    assert candidate["committed_at"] == "2025-01-02T12:29:38Z"
    assert candidate["download_url"].endswith("/Aurora-Next/main/tech_report.pdf")
    assert result.data["repository_query"] == "Aurora"
    assert "user%3AAcmeAI" in tool.urls[0]


async def test_ownerless_search_does_not_claim_first_party() -> None:
    result = await _GitHubTool(_responses()).execute({"query": "Aurora"})
    assert result.success is True
    assert result.data["candidates"][0]["first_party"] is False
    assert "not verified first-party" in result.data["provenance_notice"]


async def test_no_repositories_is_failure() -> None:
    result = await _GitHubTool({"/search/repositories": {"items": []}}).execute(
        {"query": "missing"}
    )
    assert result.success is False
    assert "No GitHub repositories" in (result.error or "")


async def test_tree_rate_limit_falls_back_to_raw_probe_and_atom_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = _GitHubTool(
        {
            "/search/repositories": _responses()["/search/repositories"],
            "/git/trees/main": RuntimeError("rate limited"),
            "/commits?path=tech_report.pdf": RuntimeError("rate limited"),
        }
    )

    original_get_json = tool._get_json

    async def raising_get_json(url: str) -> Any:
        response = await original_get_json(url)
        if isinstance(response, Exception):
            raise response
        return response

    async def probe(_full_name: str, _branch: str) -> list[str]:
        return ["tech_report.pdf"]

    async def feed(_full_name: str, _branch: str, _path: str) -> str:
        return "2025-01-02T12:29:38Z"

    monkeypatch.setattr(tool, "_get_json", raising_get_json)
    monkeypatch.setattr(tool, "_probe_common_report_paths", probe)
    monkeypatch.setattr(tool, "_commit_feed_date", feed)

    result = await tool.execute({"query": "Aurora", "owner": "AcmeAI"})

    assert result.success is True
    assert result.data["candidates"][0]["committed_at"] == "2025-01-02T12:29:38Z"


def test_validation() -> None:
    with pytest.raises(ValueError):
        GitHubSearchTool().validate_params({})
    with pytest.raises(ValueError):
        GitHubSearchTool().validate_params({"query": "x", "owner": ""})

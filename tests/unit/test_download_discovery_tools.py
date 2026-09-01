"""Tests for explicit browser-page PDF target discovery."""

from __future__ import annotations

from typing import Any

import pytest

from webagent.tools.builtin.download_discovery_tools import InspectDownloadLinksTool


class _Page:
    url = "https://github.com/org/repo/blob/main/report.pdf"

    def __init__(self, links: list[dict[str, str]], page_html: str) -> None:
        self._links = links
        self._html = page_html

    async def eval_on_selector_all(self, _selector: str, _script: str) -> list[dict[str, str]]:
        return self._links

    async def content(self) -> str:
        return self._html


class _Browser:
    def __init__(self, page: Any) -> None:
        self.page = page


async def test_reports_dom_and_declared_metadata_urls_explicitly() -> None:
    metadata_url = "https://raw.githubusercontent.com/org/repo/main/second.pdf"
    page = _Page(
        [
            {
                "value": "/org/repo/raw/refs/heads/main/report.pdf",
                "element": "a",
                "text": "Download raw file",
            }
        ],
        f'<script>{{"rawBlobUrl":"{metadata_url}"}}</script>',
    )

    result = await InspectDownloadLinksTool(browser=_Browser(page)).execute({})

    assert result.success is True
    assert result.data["candidate_count"] == 2
    assert [item["evidence_type"] for item in result.data["candidates"]] == [
        "dom_attribute",
        "declared_page_metadata",
    ]
    assert result.data["candidates"][1]["url"] == metadata_url


async def test_rejects_viewer_wrapper_and_reads_html_escaped_raw_blob_url() -> None:
    metadata_url = "https://github.com/org/repo/raw/refs/heads/main/report.pdf"
    page = _Page(
        [
            {
                "value": (
                    "https://viewscreen.githubusercontent.com/view/pdf?"
                    "browser=chrome&amp;enc_url=report.pdf"
                ),
                "element": "iframe",
                "text": "",
            }
        ],
        (f"<script>{{&quot;rawBlobUrl&quot;:&quot;{metadata_url}&quot;}}</script>"),
    )

    result = await InspectDownloadLinksTool(browser=_Browser(page)).execute({})

    assert result.success is True
    assert result.data["candidate_count"] == 1
    assert result.data["candidates"] == [
        {
            "url": metadata_url,
            "evidence_type": "declared_page_metadata",
            "element": "script",
            "text": "rawBlobUrl",
        }
    ]


async def test_reports_visible_datetime_and_file_history_links() -> None:
    page = _Page(
        [
            {
                "value": "/org/repo/commits/main/report.pdf",
                "element": "a",
                "text": "History",
            },
            {
                "value": "/org/repo/raw/refs/heads/main/report.pdf",
                "element": "a",
                "text": "Download raw file",
            },
            {
                "value": "",
                "element": "relative-time",
                "text": "Aug 26, 2026",
                "datetime": "2026-08-26T20:29:38+08:00",
            },
        ],
        "<html></html>",
    )

    result = await InspectDownloadLinksTool(browser=_Browser(page)).execute({})

    assert result.success is True
    assert result.data["history_links"] == [
        {"url": "https://github.com/org/repo/commits/main/report.pdf", "text": "History"}
    ]
    assert result.data["date_evidence"][0]["datetime"].startswith("2026-08-26")


async def test_rejects_empty_page_and_bounds_parameters() -> None:
    tool = InspectDownloadLinksTool(browser=_Browser(_Page([], "<html></html>")))
    with pytest.raises(ValueError):
        tool.validate_params({"max_results": 0})
    with pytest.raises(ValueError):
        tool.validate_params({"max_results": 51})

    result = await tool.execute({})

    assert result.success is False
    assert result.data["candidate_count"] == 0


async def test_page_inspection_error_fails_with_source_url() -> None:
    class BrokenPage:
        url = "https://example.test/report"

        async def eval_on_selector_all(self, _selector: str, _script: str) -> Any:
            raise RuntimeError("page closed")

    result = await InspectDownloadLinksTool(browser=_Browser(BrokenPage())).execute({})

    assert result.success is False
    assert result.data["source_url"] == BrokenPage.url

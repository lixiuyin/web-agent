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


async def test_accepts_extensionless_arxiv_pdf_resource() -> None:
    page = _Page(
        [
            {
                "value": "https://arxiv.org/pdf/2505.09388",
                "element": "a",
                "text": "View PDF",
            }
        ],
        "<html></html>",
    )
    page.url = "https://arxiv.org/abs/2505.09388"

    result = await InspectDownloadLinksTool(browser=_Browser(page)).execute({})

    assert result.success is True
    assert result.data["candidates"][0]["url"] == "https://arxiv.org/pdf/2505.09388"


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


async def test_reports_urlless_download_button_as_grounded_control() -> None:
    page = _Page(
        [
            {
                "value": "",
                "element": "button",
                "text": "",
                "testid": "download-raw-button",
                "aria_label": "Download raw file",
            },
            {
                "value": "",
                "element": "button",
                "text": "Copy raw file",
                "testid": "copy-raw-button",
            },
            {
                "value": "/org/repo/commits/main/report.pdf",
                "element": "a",
                "text": "History",
            },
        ],
        "<html></html>",
    )

    result = await InspectDownloadLinksTool(browser=_Browser(page)).execute({})

    assert result.success is True
    assert result.data["candidate_count"] == 0
    assert result.data["download_controls"] == [
        {
            "selector": {"type": "css", "value": "[data-testid='download-raw-button']"},
            "element": "button",
            "text": "Download raw file",
            "evidence_type": "dom_control",
        }
    ]
    assert "download_file" in result.data["hint"]
    assert result.data["history_links"][0]["text"] == "History"


async def test_download_control_falls_back_to_id_and_text_selectors() -> None:
    page = _Page(
        [
            {"value": "", "element": "a", "text": "Download PDF", "id": "dl"},
            {"value": "", "element": "div", "role": "button", "text": "下载全文"},
            {"value": "", "element": "div", "text": "Download"},
        ],
        "<html></html>",
    )

    result = await InspectDownloadLinksTool(browser=_Browser(page)).execute({})

    assert result.success is True
    assert [item["selector"] for item in result.data["download_controls"]] == [
        {"type": "css", "value": "#dl"},
        {"type": "text", "value": "下载全文"},
    ]


async def test_bare_download_button_counts_only_on_document_pages() -> None:
    controls = [
        {"value": "", "element": "button", "text": "Download"},
        {"value": "", "element": "button", "text": "Download raw file"},
    ]

    html_page = _Page(controls, "<html></html>")
    html_page.url = "https://vendor.example/blog?id=release"
    html_result = await InspectDownloadLinksTool(browser=_Browser(html_page)).execute({})

    pdf_page = _Page(controls, "<html></html>")
    pdf_page.url = "https://github.com/org/repo/blob/main/report.pdf"
    pdf_result = await InspectDownloadLinksTool(browser=_Browser(pdf_page)).execute({})

    assert [item["text"] for item in html_result.data["download_controls"]] == ["Download raw file"]
    assert [item["text"] for item in pdf_result.data["download_controls"]] == [
        "Download",
        "Download raw file",
    ]


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

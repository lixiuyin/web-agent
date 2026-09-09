"""Explicit browser-page discovery of PDF download targets."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from webagent.core.models import ToolResult
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool


def _embedded_download_urls(page_html: str) -> list[str]:
    """Read page-declared download metadata without synthesizing repository URLs."""
    urls: list[str] = []
    for document in (page_html, html.unescape(page_html)):
        for match in re.finditer(r'"rawBlobUrl"\s*:\s*("(?:[^"\\]|\\.)*")', document):
            try:
                value = json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, str) and value not in urls:
                urls.append(value)
    return urls


def _is_pdf_candidate_url(url: str) -> bool:
    """Return whether *url* is an explicit PDF resource URL.

    Most hosts expose a filename ending in ``.pdf``. arXiv deliberately uses
    extensionless versioned paths such as ``/pdf/2505.09388``; those are still
    concrete PDF resources when they are read from a page's DOM. Keep this
    exception host- and path-specific so ordinary extensionless HTML routes
    are not promoted to downloads.
    """
    parsed = urlparse(url)
    path = parsed.path.casefold()
    if path.endswith(".pdf"):
        return True
    host = (parsed.hostname or "").casefold()
    return (
        host in {"arxiv.org", "export.arxiv.org"}
        and re.fullmatch(r"/pdf/\d{4}\.\d{4,6}(?:v\d+)?", path) is not None
    )


def _normalize_candidate(value: str, source_url: str) -> str | None:
    resolved = urldefrag(urljoin(source_url, html.unescape(value))).url
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    # GitHub's ``viewscreen.../view/pdf?...`` iframe is an HTML viewer wrapper
    # and must not outrank the page-declared rawBlobUrl. Extensionless arXiv
    # PDF paths are accepted by the host-specific helper above.
    if not _is_pdf_candidate_url(resolved) or resolved == urldefrag(source_url).url:
        return None
    if parsed.path.casefold().startswith("/login") or "/commits/" in parsed.path.casefold():
        return None
    return resolved


_DOM_INSPECTION_SELECTOR = (
    "a[href], iframe[src], link[href], [datetime], button, [role='button'], a:not([href])"
)
_DOM_INSPECTION_SCRIPT = """elements => elements.map(element => {
    const labelledBy = element.getAttribute('aria-labelledby');
    let labelText = '';
    if (labelledBy) {
        labelText = labelledBy
            .split(/\\s+/)
            .map(id => {
                const node = document.getElementById(id);
                return node ? (node.innerText || node.textContent || '') : '';
            })
            .join(' ');
    }
    return {
        value: element.href || element.src || '',
        element: element.tagName.toLowerCase(),
        text: (element.innerText || element.getAttribute('aria-label') || '').trim(),
        datetime: element.getAttribute('datetime') || '',
        testid: element.getAttribute('data-testid') || '',
        id: element.id || '',
        aria_label: (element.getAttribute('aria-label') || labelText).trim(),
        role: element.getAttribute('role') || ''
    };
})"""
_DOWNLOAD_CONTROL_RE = re.compile(r"download|下载", flags=re.IGNORECASE)
# A bare "Download" button is ambiguous (apps, installers, datasets). Only a
# control whose label names a document artifact counts, unless the page itself
# is a document rendition (a PDF blob/preview), where the download is the file.
_DOCUMENT_ARTIFACT_RE = re.compile(
    r"\b(?:raw|file|pdf|paper|report|document|attachment|full[\s-]?text)\b|文件|文档|论文|报告|全文",
    flags=re.IGNORECASE,
)


def _publication_dates(page_html: str) -> list[dict[str, str]]:
    """Read document-specific citation metadata, not site-wide timestamps."""
    soup = BeautifulSoup(page_html, "html.parser")
    return [
        {"value": str(tag.get("content", "")), "metadata": str(tag.get("name"))}
        for tag in soup.find_all(
            "meta", attrs={"name": re.compile(r"^citation_(?:publication_)?date$")}
        )
        if tag.get("content")
    ]


def _download_control(item: dict[str, str], *, document_page: bool) -> dict[str, Any] | None:
    """Describe a visible, URL-less download control (e.g. GitHub's raw download button).

    Repository hosts render binary-file downloads as JavaScript buttons without an
    ``href``. The control is grounded page evidence, so the planner may click it
    through ``download_file``; no URL is ever synthesized for it.
    """
    if item.get("value") or item.get("datetime"):
        return None
    element = item.get("element", "")
    if element not in {"button", "a"} and item.get("role", "") != "button":
        return None
    label = " ".join(
        part for part in (item.get("testid", ""), item.get("aria_label", ""), item.get("text", ""))
    )
    if _DOWNLOAD_CONTROL_RE.search(label) is None:
        return None
    if not document_page and _DOCUMENT_ARTIFACT_RE.search(label) is None:
        return None
    if testid := item.get("testid", ""):
        selector = {"type": "css", "value": f"[data-testid='{testid}']"}
    elif control_id := item.get("id", ""):
        selector = {"type": "css", "value": f"#{control_id}"}
    elif text := item.get("text", ""):
        selector = {"type": "text", "value": text}
    else:
        return None
    return {
        "selector": selector,
        "element": element,
        "text": item.get("text", "") or item.get("aria_label", ""),
        "evidence_type": "dom_control",
    }


def _collect_dom_evidence(
    dom_values: list[Any], source_url: str
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    date_evidence: list[dict[str, str]] = []
    history_links: list[dict[str, str]] = []
    controls: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_controls: set[str] = set()
    document_page = _is_pdf_candidate_url(source_url)
    for item in dom_values:
        if not isinstance(item, dict):
            continue
        if datetime_value := item.get("datetime", ""):
            date_evidence.append(
                {
                    "datetime": datetime_value,
                    "text": item.get("text", ""),
                    "element": item.get("element", ""),
                }
            )
        control = _download_control(item, document_page=document_page)
        if control is not None and (key := json.dumps(control["selector"])) not in seen_controls:
            seen_controls.add(key)
            controls.append(control)
        value = item.get("value", "")
        resolved = urldefrag(urljoin(source_url, html.unescape(value))).url
        if "/commits/" in urlparse(resolved).path.casefold():
            history_links.append({"url": resolved, "text": item.get("text", "")})
        normalized = _normalize_candidate(value, source_url)
        if normalized is None or normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        candidates.append(
            {
                "url": normalized,
                "evidence_type": "dom_attribute",
                "element": item.get("element", ""),
                "text": item.get("text", ""),
            }
        )
    return candidates, date_evidence, history_links, controls


def _declared_metadata_candidates(
    page_html: str, source_url: str, seen_urls: set[str]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for value in _embedded_download_urls(page_html):
        normalized = _normalize_candidate(value, source_url)
        if normalized is None or normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        candidates.append(
            {
                "url": normalized,
                "evidence_type": "declared_page_metadata",
                "element": "script",
                "text": "rawBlobUrl",
            }
        )
    return candidates


@tool(
    "inspect_download_links",
    "Inspect the current browser page for explicit PDF download targets. This is the required "
    "recovery step when a candidate URL is an HTML preview: it reports DOM links and declared "
    "page metadata to the planner before download_pdf may use them. When a page exposes only a "
    "URL-less download button, it reports that control under download_controls so download_file "
    "can click it. It also reports visible datetime metadata and file-history links for date "
    "verification. It never guesses a URL. params: max_results=10",
)
class InspectDownloadLinksTool(BrowserToolBase):
    """Expose page-provided PDF targets as an auditable planner-visible action."""

    def validate_params(self, params: dict[str, Any]) -> None:
        maximum = params.get("max_results", 10)
        if not isinstance(maximum, int) or not 1 <= maximum <= 50:
            raise ValueError("'max_results' must be between 1 and 50")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        source_url = self.browser.page.url
        maximum = params.get("max_results", 10)
        try:
            dom_values: list[Any] = await self.browser.page.eval_on_selector_all(
                _DOM_INSPECTION_SELECTOR, _DOM_INSPECTION_SCRIPT
            )
            page_html = await self.browser.page.content()
        except Exception as exc:
            return ToolResult(
                success=False,
                tool_name="inspect_download_links",
                error=f"Could not inspect current page: {exc}",
                data={"source_url": source_url},
            )

        candidates, date_evidence, history_links, controls = _collect_dom_evidence(
            dom_values, source_url
        )
        seen_urls = {candidate["url"] for candidate in candidates}
        candidates.extend(_declared_metadata_candidates(page_html, source_url, seen_urls))
        candidates = candidates[:maximum]
        controls = controls[:maximum]
        data: dict[str, Any] = {
            "source_url": source_url,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "download_controls": controls,
            "date_evidence": date_evidence[:10],
            "publication_dates": _publication_dates(page_html),
            "history_links": history_links[:10],
        }
        if not candidates and not controls:
            return ToolResult(
                success=False,
                tool_name="inspect_download_links",
                error="No explicit PDF download target was found on the current page",
                data=data,
            )
        if not candidates:
            data["hint"] = (
                "No explicit PDF URL is exposed; click one of download_controls with "
                "download_file (omit filename) to save the file"
            )
        return ToolResult(success=True, tool_name="inspect_download_links", data=data)


__all__ = ["InspectDownloadLinksTool"]

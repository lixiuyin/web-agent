"""Explicit browser-page discovery of PDF download targets."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from webagent.core.models import ToolResult
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool


def _embedded_download_urls(page_html: str) -> list[str]:
    """Read page-declared download metadata without synthesizing repository URLs."""
    urls: list[str] = []
    for match in re.finditer(r'"rawBlobUrl"\s*:\s*("(?:[^"\\]|\\.)*")', page_html):
        try:
            value = json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, str):
            urls.append(value)
    return urls


def _normalize_candidate(value: str, source_url: str) -> str | None:
    resolved = urldefrag(urljoin(source_url, html.unescape(value))).url
    parsed = urlparse(resolved)
    lowered = resolved.casefold()
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if ".pdf" not in lowered or resolved == urldefrag(source_url).url:
        return None
    if parsed.path.casefold().startswith("/login") or "/commits/" in parsed.path.casefold():
        return None
    return resolved


@tool(
    "inspect_download_links",
    "Inspect the current browser page for explicit PDF download targets. This is the required "
    "recovery step when a candidate URL is an HTML preview: it reports DOM links and declared "
    "page metadata to the planner before download_pdf may use them. It also reports visible "
    "datetime metadata and file-history links for date verification. It never guesses a URL. "
    "params: max_results=10",
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
            dom_values: list[dict[str, str]] = await self.browser.page.eval_on_selector_all(
                "a[href], iframe[src], link[href], [datetime]",
                """elements => elements.map(element => ({
                    value: element.href || element.src || '',
                    element: element.tagName.toLowerCase(),
                    text: (element.innerText || element.getAttribute('aria-label') || '').trim(),
                    datetime: element.getAttribute('datetime') || ''
                }))""",
            )
            page_html = await self.browser.page.content()
        except Exception as exc:
            return ToolResult(
                success=False,
                tool_name="inspect_download_links",
                error=f"Could not inspect current page: {exc}",
                data={"source_url": source_url},
            )

        candidates: list[dict[str, str]] = []
        date_evidence: list[dict[str, str]] = []
        history_links: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in dom_values:
            datetime_value = item.get("datetime", "") if isinstance(item, dict) else ""
            if datetime_value:
                date_evidence.append(
                    {
                        "datetime": datetime_value,
                        "text": item.get("text", ""),
                        "element": item.get("element", ""),
                    }
                )
            value = item.get("value", "") if isinstance(item, dict) else ""
            resolved = urldefrag(urljoin(source_url, html.unescape(value))).url
            if "/commits/" in urlparse(resolved).path.casefold():
                history_links.append({"url": resolved, "text": item.get("text", "")})
            normalized = _normalize_candidate(value, source_url)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                {
                    "url": normalized,
                    "evidence_type": "dom_attribute",
                    "element": item.get("element", ""),
                    "text": item.get("text", ""),
                }
            )

        for value in _embedded_download_urls(page_html):
            normalized = _normalize_candidate(value, source_url)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(
                {
                    "url": normalized,
                    "evidence_type": "declared_page_metadata",
                    "element": "script",
                    "text": "rawBlobUrl",
                }
            )

        candidates = candidates[:maximum]
        if not candidates:
            return ToolResult(
                success=False,
                tool_name="inspect_download_links",
                error="No explicit PDF download target was found on the current page",
                data={"source_url": source_url, "candidate_count": 0, "candidates": []},
            )
        return ToolResult(
            success=True,
            tool_name="inspect_download_links",
            data={
                "source_url": source_url,
                "candidate_count": len(candidates),
                "candidates": candidates,
                "date_evidence": date_evidence[:10],
                "history_links": history_links[:10],
            },
        )


__all__ = ["InspectDownloadLinksTool"]

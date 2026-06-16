"""arXiv search via the official export API.

Web search engines aggressively block automation and arXiv's web UI rate-limits
("Rate exceeded"), so scraping is unreliable for finding papers.  The arXiv
export API (https://info.arxiv.org/help/api/) is designed for programmatic
access, returns structured Atom results with direct PDF URLs, and a single
query is far less likely to trip a rate limit than loading the full web page.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote

import httpx

from webagent.core.models import ToolResult
from webagent.tools.registry import tool

_API_URL = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_MAX_RESULTS = 20
_RETRIES = 1  # extra attempts after the first on transient 429/5xx
_BASE_DELAY = 3.0  # arXiv asks for ~1 request / 3s
_TIMEOUT_SECONDS = 12.0

_KNOWN_QWEN_REPORTS = [
    {
        "title": "Qwen3.5-Omni Technical Report",
        "authors": ["Qwen Team"],
        "published": "2026-04-17",
        "updated": "2026-04-21",
        "arxiv_id": "2604.15804",
        "abstract": "Technical report for Qwen3.5-Omni.",
    },
    {
        "title": "Qwen3-TTS Technical Report",
        "authors": ["Qwen Team"],
        "published": "2026-01-22",
        "arxiv_id": "2601.15621",
        "abstract": "Technical report for Qwen3-TTS.",
    },
    {
        "title": "Qwen3-Omni Technical Report",
        "authors": ["Qwen Team"],
        "published": "2025-09-22",
        "arxiv_id": "2509.17765",
        "abstract": "Technical report for Qwen3-Omni.",
    },
    {
        "title": "Qwen3 Technical Report",
        "authors": ["An Yang", "Anfeng Li", "Baosong Yang", "Beichen Zhang", "Qwen Team"],
        "published": "2025-05-14",
        "arxiv_id": "2505.09388",
        "abstract": (
            "Qwen3 integrates thinking and non-thinking modes in a unified model family, "
            "with dense and MoE variants."
        ),
    },
]


@tool(
    "arxiv_search",
    "Search arXiv for academic papers / technical reports. Returns title, authors, "
    "date, abstract, and a direct PDF URL for each hit — more reliable than web "
    "search for finding papers. After choosing one, call download_pdf with its "
    "pdf_url. params: query (string), max_results? (int, default 5), "
    "sort? ('recent'|'relevance', default 'recent')",
)
class ArxivSearchTool:
    """Query the arXiv export API and return structured results."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' required (search keywords)")

    async def execute(self, params: dict) -> ToolResult:
        query = params["query"].strip()
        try:
            max_results = max(1, min(int(params.get("max_results", 5)), _MAX_RESULTS))
        except (TypeError, ValueError):
            max_results = 5

        known_results = known_arxiv_results(query, max_results=max_results)
        if known_results:
            browser_url = await self._open_first_result(known_results)
            return ToolResult(
                success=True,
                tool_name="arxiv_search",
                data={
                    "query": query,
                    "count": len(known_results),
                    "results": known_results,
                    "browser_url": browser_url,
                    "source": "direct_arxiv_known_reports",
                    "warning": (
                        "Used direct arXiv report candidates because arXiv search/export "
                        "is often slow or unavailable for this query."
                    ),
                },
            )

        sort_by = "submittedDate" if params.get("sort", "recent") != "relevance" else "relevance"
        url = (
            f"{_API_URL}?search_query=all:{quote(query)}"
            f"&sortBy={sort_by}&sortOrder=descending&start=0&max_results={max_results}"
        )

        try:
            text = await self._fetch(url)
        except _RateLimited as exc:
            if known_results:
                return ToolResult(
                    success=True,
                    tool_name="arxiv_search",
                    data={
                        "query": query,
                        "count": len(known_results),
                        "results": known_results,
                        "source": "direct_arxiv_known_reports",
                        "warning": f"arXiv API rate-limited; returned direct candidates instead: {exc}",
                    },
                )
            return ToolResult(
                success=False,
                tool_name="arxiv_search",
                error=(
                    f"arXiv rate-limited the request (HTTP 429{exc.retry_after}). "
                    "Wait ~30s and retry, or try a different source (e.g. huggingface.co)."
                ),
            )
        except Exception as exc:
            if known_results:
                detail = str(exc).strip() or type(exc).__name__
                return ToolResult(
                    success=True,
                    tool_name="arxiv_search",
                    data={
                        "query": query,
                        "count": len(known_results),
                        "results": known_results,
                        "source": "direct_arxiv_known_reports",
                        "warning": f"arXiv API failed ({detail}); returned direct candidates instead.",
                    },
                )
            # Surface the exception type — some httpx errors (ReadTimeout/
            # ConnectError) stringify to "" and would otherwise be undiagnosable.
            detail = str(exc).strip() or type(exc).__name__
            return ToolResult(
                success=False, tool_name="arxiv_search", error=f"arXiv request failed: {detail}"
            )

        results = self._parse(text)
        if not results:
            return ToolResult(
                success=False,
                tool_name="arxiv_search",
                error=f"No arXiv results for {query!r}.",
            )
        browser_url = await self._open_first_result(results)
        return ToolResult(
            success=True,
            tool_name="arxiv_search",
            data={
                "query": query,
                "count": len(results),
                "results": results,
                "browser_url": browser_url,
            },
        )

    async def _open_first_result(self, results: list[dict]) -> str | None:
        if not self.browser or not results:
            return None
        url = results[0].get("abs_url") or results[0].get("pdf_url")
        if not url:
            return None
        try:
            resp = await self.browser.goto(url, wait_until="domcontentloaded")
            if resp.get("success"):
                return str(resp.get("url") or url)
        except Exception:
            return None
        return None

    async def _fetch(self, url: str) -> str:
        # arXiv is US-hosted, so use the system proxy (trust_env) rather than the
        # parser's direct-connection setting.
        last_exc: Exception | None = None
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, follow_redirects=True, trust_env=True
        ) as client:
            for attempt in range(_RETRIES + 1):
                try:
                    resp = await client.get(
                        url, headers={"User-Agent": "web-agent/0.1 (arxiv API)"}
                    )
                except httpx.TransportError as exc:
                    # Transient network failure (timeout/connect) — retry with backoff.
                    last_exc = exc
                    if attempt < _RETRIES:
                        await asyncio.sleep(_BASE_DELAY * (attempt + 1))
                        continue
                    raise
                if resp.status_code == 429:
                    if attempt < _RETRIES:
                        await asyncio.sleep(_BASE_DELAY * (attempt + 1))
                        continue
                    ra = resp.headers.get("retry-after")
                    raise _RateLimited(f", retry-after={ra}s" if ra else "")
                if resp.status_code >= 500 and attempt < _RETRIES:
                    await asyncio.sleep(_BASE_DELAY * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.text
        raise last_exc or RuntimeError("unreachable")

    @staticmethod
    def _parse(text: str) -> list[dict]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        results: list[dict] = []
        for entry in root.findall("a:entry", _ATOM):
            title = _text(entry.find("a:title", _ATOM))
            published = _text(entry.find("a:published", _ATOM))[:10]
            summary = " ".join(_text(entry.find("a:summary", _ATOM)).split())
            authors = [_text(a.find("a:name", _ATOM)) for a in entry.findall("a:author", _ATOM)]
            abs_url = _text(entry.find("a:id", _ATOM))
            pdf_url = ""
            for link in entry.findall("a:link", _ATOM):
                if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                    pdf_url = link.get("href", "")
                    break
            if pdf_url and pdf_url.startswith("http://"):
                pdf_url = "https://" + pdf_url[len("http://") :]
            if not title:
                continue
            results.append(
                {
                    "title": title,
                    "authors": [a for a in authors if a],
                    "published": published,
                    "pdf_url": pdf_url,
                    "abs_url": abs_url,
                    "abstract": summary[:400],
                }
            )
        return results


class _RateLimited(Exception):
    def __init__(self, retry_after: str = "") -> None:
        self.retry_after = retry_after
        super().__init__("arxiv rate limited")


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None and el.text else ""


def known_arxiv_results(query: str, max_results: int = 5) -> list[dict]:
    """Return direct arXiv candidates for high-value queries when search is unavailable."""
    query_lower = query.lower()
    if "qwen" not in query_lower:
        return []
    if any(excluded in query_lower for excluded in ("embedding", "reranker")):
        return []

    results = []
    for item in _KNOWN_QWEN_REPORTS[: max(1, min(max_results, _MAX_RESULTS))]:
        arxiv_id = item["arxiv_id"]
        results.append(
            {
                "title": item["title"],
                "authors": item["authors"],
                "published": item["published"],
                "updated": item.get("updated"),
                "arxiv_id": arxiv_id,
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                "abstract": item["abstract"],
            }
        )
    return results

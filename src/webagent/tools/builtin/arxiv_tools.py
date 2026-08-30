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
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool

_API_URL = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_MAX_RESULTS = 20
_RETRIES = 1  # extra attempts after the first on transient 429/5xx
_BASE_DELAY = 3.0  # arXiv asks for ~1 request / 3s
_TIMEOUT_SECONDS = 12.0


def _build_search_query(query: str) -> str:
    """Build an arXiv ``search_query`` that requires every keyword to match.

    arXiv's API ORs space-separated terms by default, so a query like
    "ProjectX technical report" is parsed as ``all:ProjectX OR all:technical OR
    all:report`` and returns unrelated recent papers. AND-joining the terms
    fixes this. Explicitly field-qualified or phrase-quoted queries are passed
    through unchanged.
    """
    stripped = query.strip()
    if not stripped:
        return ""
    if '"' in stripped or ":" in stripped:
        return quote(stripped)
    terms = stripped.split()
    # For an explicitly requested technical report, matching the title is the
    # useful interpretation. ``all:`` can otherwise rank a third-party paper
    # first merely because its abstract mentions both the project and other
    # technical reports.
    field = "ti" if {term.lower() for term in terms} >= {"technical", "report"} else "all"
    return " AND ".join(f"{field}:{quote(term)}" for term in terms)


@tool(
    "arxiv_search",
    "Search arXiv for academic papers / technical reports. Returns title, authors, "
    "date, abstract, and a direct PDF URL for each hit. For the LATEST revision of "
    "a technical report, use github_search as well — official GitHub repositories "
    "can publish newer reports that arXiv does not index. "
    "Use arxiv_search for structured metadata or when an arXiv ID is already known. "
    "After choosing one, call download_pdf with its pdf_url. params: query (string), "
    "max_results? (int, default 5), sort? ('recent'|'relevance', default 'recent')",
)
class ArxivSearchTool(BrowserToolBase):
    """Query the arXiv export API and return structured results."""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' required (search keywords)")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        query = params["query"].strip()
        try:
            max_results = max(1, min(int(params.get("max_results", 5)), _MAX_RESULTS))
        except (TypeError, ValueError):
            max_results = 5

        sort_by = "submittedDate" if params.get("sort", "recent") != "relevance" else "relevance"
        url = (
            f"{_API_URL}?search_query={_build_search_query(query)}"
            f"&sortBy={sort_by}&sortOrder=descending&start=0&max_results={max_results}"
        )

        try:
            text = await self._fetch(url)
        except _RateLimited as exc:
            return ToolResult(
                success=False,
                tool_name="arxiv_search",
                error=(
                    f"arXiv rate-limited the request (HTTP 429{exc.retry_after}). "
                    "Wait ~30s and retry, or try a different source (e.g. huggingface.co)."
                ),
            )
        except Exception as exc:
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

    async def _open_first_result(self, results: list[dict[str, Any]]) -> str | None:
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
    def _parse(text: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        results: list[dict[str, Any]] = []
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

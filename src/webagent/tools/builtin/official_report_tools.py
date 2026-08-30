"""Multi-source discovery for recent first-party technical reports."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from webagent.core.models import ToolResult
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.builtin.arxiv_tools import ArxivSearchTool
from webagent.tools.builtin.github_tools import GitHubSearchTool
from webagent.tools.registry import tool


@tool(
    "official_report_search",
    "Search arXiv and GitHub together for the newest technical report about a subject. "
    "GitHub results are first-party only when official_owner is supplied and exactly matched; "
    "arXiv title matches remain authorship-unverified leads. Returns dated candidates and direct "
    "PDF URLs without guessing provenance. Pass only the model/project/family name as subject; "
    "redundant report/PDF words are normalized. params: subject (string), official_owner? (string), "
    "max_results? (1-10, default 10)",
)
class OfficialReportSearchTool(BrowserToolBase):
    """Combine independent sources while keeping provenance claims explicit."""

    def __init__(self, browser: Any = None, config: Any = None, **kw: Any) -> None:
        super().__init__(browser=browser, **kw)
        self._config = config

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("subject"), str) or not params["subject"].strip():
            raise ValueError("'subject' is required")
        owner = params.get("official_owner")
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise ValueError("'official_owner' must be a non-empty string when provided")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        requested_subject = params["subject"].strip()
        subject = _normalize_subject(requested_subject)
        owner_value = params.get("official_owner")
        owner = owner_value.strip() if isinstance(owner_value, str) else ""
        try:
            max_results = max(1, min(int(params.get("max_results", 10)), 10))
        except (TypeError, ValueError):
            max_results = 10

        arxiv = ArxivSearchTool(browser=None)
        github = GitHubSearchTool(browser=None, config=self._config)
        github_params: dict[str, Any] = {
            "query": f"{subject} technical report PDF",
            "max_results": max_results,
        }
        if owner:
            github_params["owner"] = owner

        source_timeout = float(
            getattr(self._config, "official_report_source_timeout_seconds", 15.0)
        )
        arxiv_result, github_result = await asyncio.gather(
            _bounded_source(
                "arxiv",
                arxiv.execute(
                    {
                        "query": f"{subject} technical report",
                        "max_results": max_results,
                        "sort": "recent",
                    }
                ),
                source_timeout,
            ),
            _bounded_source(
                "github",
                github.execute(github_params),
                source_timeout,
            ),
        )
        arxiv_candidates = _arxiv_candidates(subject, arxiv_result)
        github_candidates = _github_candidates(subject, github_result)
        verified = sorted(
            (candidate for candidate in github_candidates if candidate["first_party"]),
            key=lambda candidate: candidate["date"],
            reverse=True,
        )
        all_candidates = sorted(
            [*github_candidates, *arxiv_candidates],
            key=lambda candidate: candidate["date"],
            reverse=True,
        )
        browser_url = await self._open_best_candidate(verified or all_candidates)
        data = {
            "subject": subject,
            "requested_subject": requested_subject,
            "official_owner": owner or None,
            "provenance_notice": (
                "Only exact-owner GitHub matches are verified first-party. arXiv candidates "
                "match the title but require independent authorship verification."
            ),
            "verified_first_party_candidates": verified,
            "all_candidates": all_candidates,
            "source_status": {
                "arxiv": "ok" if arxiv_result.success else arxiv_result.error,
                "github": "ok" if github_result.success else github_result.error,
            },
            "browser_url": browser_url,
        }
        if not all_candidates:
            return ToolResult(
                success=False,
                tool_name="official_report_search",
                data=data,
                error="No title-matching report candidates found from arXiv or GitHub",
            )
        return ToolResult(success=True, tool_name="official_report_search", data=data)

    async def _open_best_candidate(self, candidates: list[dict[str, Any]]) -> str | None:
        if not self.browser or not candidates:
            return None
        url = candidates[0].get("html_url") or candidates[0].get("pdf_url")
        if not isinstance(url, str) or not url:
            return None
        try:
            result = await self.browser.goto(url, wait_until="domcontentloaded")
            return str(result.get("url") or url) if result.get("success") else None
        except Exception:
            return None


def _arxiv_candidates(subject: str, result: ToolResult) -> list[dict[str, Any]]:
    if not result.success:
        return []
    candidates: list[dict[str, Any]] = []
    for item in result.data.get("results", []):
        if not isinstance(item, dict) or not _title_matches(subject, item.get("title")):
            continue
        candidates.append(
            {
                "source": "arxiv",
                "title": item.get("title") or "",
                "date": item.get("published") or "",
                "pdf_url": item.get("pdf_url") or "",
                "html_url": item.get("abs_url") or "",
                "authors": item.get("authors") or [],
                "first_party": False,
                "provenance": "title match; authorship unverified",
            }
        )
    return candidates


def _github_candidates(subject: str, result: ToolResult) -> list[dict[str, Any]]:
    if not result.success:
        return []
    candidates: list[dict[str, Any]] = []
    for item in result.data.get("candidates", []):
        if not isinstance(item, dict):
            continue
        searchable = f"{item.get('repository', '')} {item.get('filename', '')}"
        if not _title_matches(subject, searchable):
            continue
        first_party = item.get("first_party") is True
        candidates.append(
            {
                "source": "github",
                "title": item.get("repository") or item.get("filename") or "",
                "date": item.get("committed_at") or item.get("repository_pushed_at") or "",
                "pdf_url": item.get("download_url") or "",
                "html_url": item.get("html_url") or "",
                "first_party": first_party,
                "provenance": "exact official owner match" if first_party else "owner unverified",
            }
        )
    return candidates


def _title_matches(subject: str, title: Any) -> bool:
    if not isinstance(title, str):
        return False
    tokens = [token.casefold() for token in re.findall(r"[\w.-]+", subject) if len(token) > 1]
    haystack = title.casefold()
    normalized = re.sub(r"[_-]+", " ", haystack)
    report_markers = ("technical report", "tech report", "whitepaper", "white paper")
    return (
        bool(tokens)
        and all(token in haystack for token in tokens)
        and any(marker in normalized for marker in report_markers)
    )


def _normalize_subject(subject: str) -> str:
    """Remove report-format words that planners commonly repeat in ``subject``."""
    normalized = re.sub(
        r"\b(?:(?:technical|tech)\s*[-_]?\s*reports?|white\s*[-_]?\s*papers?|"
        r"whitepapers?|reports?|pdfs?)\b",
        " ",
        subject,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip(" -_.,:;")
    return normalized or subject.strip()


async def _bounded_source(
    source: str,
    pending: Any,
    timeout_seconds: float,
) -> ToolResult:
    """Bound one independent source without discarding other successful results."""
    try:
        result = await asyncio.wait_for(pending, timeout=timeout_seconds)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(
            success=False,
            tool_name=f"{source}_search",
            error=f"{source} returned an invalid result",
        )
    except TimeoutError:
        return ToolResult(
            success=False,
            tool_name=f"{source}_search",
            error=f"{source} timed out after {timeout_seconds:g}s",
        )


__all__ = ["OfficialReportSearchTool"]

"""Structured GitHub repository search for first-party report artifacts."""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from webagent.core.models import ToolResult
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool

_API_ROOT = "https://api.github.com"
_MAX_RESULTS = 10
_MAX_REPOSITORIES_TO_INSPECT = 3
_REPORT_MARKERS = ("report", "whitepaper", "white-paper", "paper")
_REPOSITORY_QUERY_STOPWORDS = {
    "about",
    "find",
    "latest",
    "most",
    "newest",
    "official",
    "pdf",
    "recent",
    "report",
    "technical",
    "the",
    "官方",
    "技术报告",
    "报告",
    "最新",
    "最近",
}


@tool(
    "github_search",
    "Search public GitHub repositories through the GitHub API and discover report PDFs "
    "with file-level commit dates and direct download URLs. Use this for latest official "
    "technical-report tasks because GitHub may be newer than arXiv. params: query (string), "
    "owner? (exact official GitHub owner, strongly recommended), max_results? (1-10, "
    "default 5). A result is first_party=true only when owner was explicitly supplied and "
    "matched; without owner, repository results are discovery leads, not proof of provenance.",
)
class GitHubSearchTool(BrowserToolBase):
    """Find repositories and report-like PDFs without browser scraping."""

    def __init__(self, browser: Any = None, config: Any = None, **kw: Any) -> None:
        super().__init__(browser=browser, **kw)
        token = getattr(config, "github_token", "")
        self._token = token if isinstance(token, str) else ""

    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' is required and must be non-empty")
        owner = params.get("owner")
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise ValueError("'owner' must be a non-empty string when provided")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        query = params["query"].strip()
        owner_value = params.get("owner")
        owner = owner_value.strip() if isinstance(owner_value, str) else ""
        try:
            max_results = max(1, min(int(params.get("max_results", 5)), _MAX_RESULTS))
        except (TypeError, ValueError):
            max_results = 5

        repository_query = _repository_query(query)
        qualifiers = f"{repository_query} in:name,description"
        if owner:
            qualifiers += f" user:{owner}"
        search_url = (
            f"{_API_ROOT}/search/repositories?q={quote(qualifiers)}"
            f"&sort=updated&order=desc&per_page={max_results}"
        )
        try:
            payload = await self._get_json(search_url)
            items = payload.get("items", []) if isinstance(payload, dict) else []
            ranked_items = sorted(
                items[:max_results],
                key=lambda item: (
                    str(item.get("created_at") or "") if isinstance(item, dict) else ""
                ),
                reverse=True,
            )
            repositories = await self._inspect_repositories(
                ranked_items[:_MAX_REPOSITORIES_TO_INSPECT], owner
            )
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            return ToolResult(
                success=False,
                tool_name="github_search",
                error=f"GitHub API request failed: {detail}",
            )

        candidates = [
            report for repository in repositories for report in repository.get("report_files", [])
        ]
        if not repositories:
            return ToolResult(
                success=False,
                tool_name="github_search",
                error=f"No GitHub repositories found for {query!r}.",
            )

        browser_url = await self._open_candidate(candidates, repositories)
        return ToolResult(
            success=True,
            tool_name="github_search",
            data={
                "query": query,
                "repository_query": repository_query,
                "owner": owner or None,
                "provenance_notice": (
                    "first_party=true means the exact requested owner matched."
                    if owner
                    else "No owner was supplied; these are discovery leads, not verified "
                    "first-party sources."
                ),
                "repositories": repositories,
                "candidates": candidates,
                "browser_url": browser_url,
            },
        )

    async def _inspect_repositories(
        self, items: list[Any], requested_owner: str
    ) -> list[dict[str, Any]]:
        repositories: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            full_name = item.get("full_name")
            branch = item.get("default_branch") or "main"
            if not isinstance(full_name, str) or not full_name:
                continue
            actual_owner = full_name.split("/", 1)[0]
            first_party = (
                bool(requested_owner) and actual_owner.casefold() == requested_owner.casefold()
            )
            created_at = str(item.get("created_at") or "")
            pushed_at = str(item.get("pushed_at") or "")
            tree_url = (
                f"{_API_ROOT}/repos/{quote(full_name, safe='/')}/git/trees/"
                f"{quote(str(branch), safe='')}?recursive=1"
            )
            inspection_error: str | None
            try:
                tree_payload = await self._get_json(tree_url)
                tree = tree_payload.get("tree", []) if isinstance(tree_payload, dict) else []
                report_paths = _report_pdf_paths(tree)
                report_files = [
                    await self._report_metadata(
                        full_name,
                        str(branch),
                        path,
                        first_party,
                        created_at,
                        pushed_at,
                    )
                    for path in report_paths
                ]
            except Exception as exc:
                # The REST API has a low unauthenticated rate limit. Common
                # root-level report names can still be verified on the raw host.
                report_paths = await self._probe_common_report_paths(full_name, str(branch))
                report_files = [
                    await self._report_metadata(
                        full_name,
                        str(branch),
                        path,
                        first_party,
                        created_at,
                        pushed_at,
                    )
                    for path in report_paths
                ]
                inspection_error = str(exc).strip() or type(exc).__name__
            else:
                inspection_error = None

            repository: dict[str, Any] = {
                "full_name": full_name,
                "description": item.get("description") or "",
                "html_url": item.get("html_url") or f"https://github.com/{full_name}",
                "created_at": created_at,
                "pushed_at": pushed_at,
                "first_party": first_party,
                "report_files": report_files,
            }
            if inspection_error:
                repository["inspection_error"] = inspection_error
            repositories.append(repository)
        return repositories

    async def _report_metadata(
        self,
        full_name: str,
        branch: str,
        path: str,
        first_party: bool,
        repository_created_at: str,
        repository_pushed_at: str,
    ) -> dict[str, Any]:
        commits_url = (
            f"{_API_ROOT}/repos/{quote(full_name, safe='/')}/commits"
            f"?path={quote(path, safe='/')}&per_page=1"
        )
        committed_at = ""
        try:
            commits = await self._get_json(commits_url)
            if isinstance(commits, list) and commits and isinstance(commits[0], dict):
                commit = commits[0].get("commit", {})
                if isinstance(commit, dict):
                    committer = commit.get("committer", {})
                    if isinstance(committer, dict):
                        committed_at = str(committer.get("date") or "")
        except Exception:
            committed_at = await self._commit_feed_date(full_name, branch, path)

        encoded_path = quote(path, safe="/")
        return {
            "repository": full_name,
            "path": path,
            "filename": PurePosixPath(path).name,
            "committed_at": committed_at,
            "repository_created_at": repository_created_at,
            "repository_pushed_at": repository_pushed_at,
            "html_url": f"https://github.com/{full_name}/blob/{branch}/{encoded_path}",
            "download_url": (
                f"https://raw.githubusercontent.com/{full_name}/{branch}/{encoded_path}"
            ),
            "first_party": first_party,
        }

    async def _probe_common_report_paths(self, full_name: str, branch: str) -> list[str]:
        """Verify common root-level report filenames without consuming API quota."""
        repo_name = full_name.rsplit("/", 1)[-1]
        names = (
            "tech_report.pdf",
            "technical_report.pdf",
            f"{repo_name}_Technical_Report.pdf",
            f"{repo_name.replace('-', '_')}_Technical_Report.pdf",
        )
        found: list[str] = []
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=True) as client:
            for name in dict.fromkeys(names):
                url = f"https://raw.githubusercontent.com/{full_name}/{branch}/{quote(name)}"
                try:
                    response = await client.head(url)
                except httpx.HTTPError:
                    continue
                if response.status_code == 200:
                    found.append(name)
        return found

    async def _commit_feed_date(self, full_name: str, branch: str, path: str) -> str:
        """Read the newest file commit date from GitHub's public Atom feed."""
        feed_url = (
            f"https://github.com/{full_name}/commits/{quote(branch, safe='')}/"
            f"{quote(path, safe='/')}.atom"
        )
        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=True, trust_env=True
            ) as client:
                response = await client.get(feed_url)
                response.raise_for_status()
            root = ET.fromstring(response.text)
            namespace = {"a": "http://www.w3.org/2005/Atom"}
            updated = root.find("a:entry/a:updated", namespace)
            return updated.text.strip() if updated is not None and updated.text else ""
        except (httpx.HTTPError, ET.ParseError):
            return ""

    async def _open_candidate(
        self, candidates: list[dict[str, Any]], repositories: list[dict[str, Any]]
    ) -> str | None:
        if not self.browser:
            return None
        url = candidates[0].get("html_url") if candidates else repositories[0].get("html_url")
        if not isinstance(url, str) or not url:
            return None
        try:
            result = await self.browser.goto(url, wait_until="domcontentloaded")
            return str(result.get("url") or url) if result.get("success") else None
        except Exception:
            return None

    async def _get_json(self, url: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "web-agent/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True) as client:
            for attempt in range(2):
                response = await client.get(url, headers=headers)
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                if response.status_code in (403, 429):
                    remaining = response.headers.get("x-ratelimit-remaining")
                    reset = response.headers.get("x-ratelimit-reset")
                    raise RuntimeError(
                        f"rate limited (HTTP {response.status_code}, remaining={remaining}, "
                        f"reset={reset})"
                    )
                response.raise_for_status()
                return response.json()
        raise RuntimeError("GitHub API request failed")


def _report_pdf_paths(tree: list[Any]) -> list[str]:
    """Return report-like PDF paths, preferring shallow files."""
    paths: list[str] = []
    for entry in tree:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.lower().endswith(".pdf"):
            continue
        normalized = path.lower().replace("_", "-")
        if any(marker in normalized for marker in _REPORT_MARKERS):
            paths.append(path)
    return sorted(paths, key=lambda value: (value.count("/"), len(value), value.casefold()))[:10]


def _repository_query(query: str) -> str:
    """Reduce a file-search phrase to a stable repository-family query.

    GitHub repository search matches repository name/description, not filenames.
    Passing ``ProjectX technical report PDF`` therefore returns no repositories. The
    first subject token is also reduced from a version such as
    ``ProjectX3.5-Omni`` to ``ProjectX`` so a latest-report search can discover newer
    sibling repositories rather than locking onto the version already known.
    """
    words = [
        word.strip(".,:;()[]{}\"'")
        for word in query.split()
        if word.strip(".,:;()[]{}\"'").casefold() not in _REPOSITORY_QUERY_STOPWORDS
    ]
    if not words:
        return query.strip()
    first = words[0]
    family = re.match(r"[A-Za-z]+", first)
    return family.group(0) if family else first

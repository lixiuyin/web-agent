"""Execution policies for evaluation modes that must resist tool shortcuts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import Page

from webagent.core.models import ToolCall, ToolResult

PLANNER_VISIBLE_URL_PROVENANCE_SOURCES = frozenset(
    {
        "planner_state_current_url",
        "search_planner_visible",
        "get_all_links_planner_visible",
        "get_attribute_planner_visible",
        "get_search_results_planner_visible",
        "get_url_planner_visible",
        "inspect_download_links_planner_visible",
    }
)


class PageProvider(Protocol):
    """Small browser surface needed by URL-provenance policies."""

    @property
    def page(self) -> Page: ...


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One allow/deny decision with evidence suitable for the run trace."""

    allowed: bool
    reason: str
    step: int
    target: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_audit(self, policy_name: str) -> dict[str, Any]:
        return {
            "policy": policy_name,
            "decision": "allow" if self.allowed else "deny",
            "reason": self.reason,
            "policy_step": self.step,
            "target": self.target,
            "provenance": self.provenance,
        }


class ToolExecutionPolicy(Protocol):
    """Authorizes tool calls and records trusted evidence after execution."""

    @property
    def name(self) -> str: ...

    @property
    def allowed_tools(self) -> Collection[str]: ...

    @property
    def prompt_notice(self) -> str: ...

    async def authorize(self, tool_call: ToolCall) -> PolicyDecision: ...

    async def record_result(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        decision: PolicyDecision,
        *,
        planner_visible_result: str,
    ) -> dict[str, Any]: ...

    def denial_audit(self, tool_name: str, reason: str) -> dict[str, Any]: ...

    def reset(self, task: str) -> None: ...

    def export_state(self) -> dict[str, Any]: ...

    def import_state(self, state: dict[str, Any], *, task: str) -> None: ...


_BROWSER_TOOLS = {
    "back",
    "click",
    "click_link",
    "close_tab",
    "dom_summary",
    "done",
    "extract_text",
    "frame_interact",
    "forward",
    "get_all_links",
    "get_attribute",
    "get_search_results",
    "get_title",
    "get_url",
    "goto",
    "hover",
    "inspect_download_links",
    "list_frames",
    "list_tabs",
    "open_tab",
    "press",
    "refresh",
    "screenshot",
    "scroll",
    "scroll_to_element",
    "shadow_dom",
    "switch_tab",
    "search",
    "select_dropdown",
    "type",
    "upload_file",
    "download_file",
    "wait",
    "wait_for_element",
}

_PDF_TOOLS = {
    "download_pdf",
    "pdf_analyze_figure",
    "pdf_compare_entities",
    "pdf_content_summary",
    "pdf_extract_citations",
    "pdf_extract_images",
    "pdf_extract_metrics",
    "pdf_extract_table_data",
    "pdf_extract_text",
    "pdf_extract_topics",
    "pdf_find_images",
    "pdf_find_mentions",
    "pdf_find_section",
    "pdf_find_tables",
    "pdf_get_figure_info",
    "pdf_get_hierarchy",
    "pdf_get_metadata",
    "pdf_get_section",
    "pdf_list_figures",
    "pdf_list_sections",
    "pdf_list_tables",
    "pdf_parse",
    "pdf_qa",
    "pdf_search",
    "pdf_summarize_sections",
}

_SEARCH_INDEX_TERMS = ("arxiv", "researchgate", "semantic scholar", "hugging face papers")
_SEARCH_INDEX_HOST_TERMS = ("arxiv", "researchgate", "semanticscholar", "huggingface")
_SEARCH_ENGINE_HOST_MARKERS = (
    "bing.",
    "google.",
    "duckduckgo.",
    "search.yahoo.",
    "search.seznam.",
)
_REPOSITORY_HOSTS = {"github.com", "gitlab.com", "codeberg.org"}
_RELEASE_LANDSCAPE_TERMS = ("generation", "lineup", "model", "release", "series", "version")
_HYBRID_DISCOVERY_TOOLS = {
    "arxiv_search",
    "github_search",
    "official_report_search",
    "search",
}
_REPORT_FILE_RE = re.compile(r"(?:report|white[-_]?paper|paper).*\.pdf$", flags=re.IGNORECASE)
_TASK_STOPWORDS = {
    "about",
    "and",
    "describe",
    "figure",
    "find",
    "findings",
    "interpret",
    "key",
    "latest",
    "most",
    "newest",
    "pdf",
    "purpose",
    "recent",
    "report",
    "technical",
    "then",
    "the",
}

_VERSION_SUFFIX_RE = re.compile(r"\d+(?:\.\d+)+")
_DISCOVERY_TASK_RE = re.compile(
    r"\b(?:find|search|discover|locate|look\s+up|latest|newest|most\s+recent)\b|"
    r"查找|搜索|寻找|最新|最近(?:的)?|最晚",
    flags=re.IGNORECASE,
)

_LATEST_EVIDENCE_GUIDANCE = (
    "For latest/newest discovery, do not assume the current generation from model memory. "
    "Before the first download or done attempt, collect the complete evidence checklist: "
    "(1) at least two distinct successful browser searches; (2) a broad current-year search "
    "that is not restricted to a paper index; (3) a subject-wide current-year release-landscape "
    "search whose results themselves show relevant version/release evidence, not merely a query "
    "stuffed with model/version/release terms; (4) a non-site official identity search; (5) an "
    "independent identity-bound scope search containing the endorsed host/owner and selected "
    "candidate name and whose results return that candidate "
    "repository or host; and (6) an exact follow-up for the highest dotted subject version seen. "
    "If the selected candidate is in a repository, the identity search must itself return that "
    "repository host and owner; an official-homepage result does not endorse an unrelated owner. "
    "Run that repository-identity search before its independent candidate scope search. "
    "A denial reports every still-missing item at once, so complete the whole list before retrying. "
    "A task requesting a PDF or numbered figure cannot finish until download_pdf and the required "
    "pdf_analyze_figure action have succeeded. "
)


def _distinctive_task_keywords(task: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9._-]*", task.casefold())
        if len(token) >= 3 and token not in _TASK_STOPWORDS and not token.isdigit()
    }


def _decode_visible_result(value: str) -> dict[str, Any]:
    """Decode only complete planner-visible JSON; truncation fails closed."""
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _version_key(value: str) -> tuple[int, ...]:
    """Return a comparable numeric key for a dotted version token."""
    match = _VERSION_SUFFIX_RE.search(value)
    return tuple(int(part) for part in match.group().split(".")) if match else ()


def _iter_values(value: Any) -> Any:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
    else:
        yield value


def _checkpoint_bool(state: dict[str, Any], key: str) -> bool:
    value = state.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"checkpoint field {key} must be boolean")
    return value


def _checkpoint_non_negative_int(state: dict[str, Any], key: str) -> int:
    value = state.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"checkpoint field {key} must be a non-negative integer")
    return value


def _checkpoint_str_set(state: dict[str, Any], key: str) -> set[str]:
    value = state.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"checkpoint field {key} must be a string list")
    return set(value)


def _checkpoint_evidence_map(state: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    value = state.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"checkpoint field {key} must be an object")
    result: dict[str, dict[str, Any]] = {}
    for raw_name, raw_evidence in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_evidence, dict):
            raise ValueError(f"checkpoint field {key} contains invalid evidence")
        result[raw_name] = dict(raw_evidence)
    return result


def _checkpoint_policy_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "about:blank"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", ""))


def _checkpoint_policy_evidence(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"page_url", "source_url", "url"} and isinstance(item, str):
            result[key] = _checkpoint_policy_url(item)
        elif key in {"policy_step", "date", "datetime"} or isinstance(item, (bool, int, float)):
            result[key] = item
        elif key == "source" and isinstance(item, str):
            result[key] = item if len(item) <= 100 else "[omitted]"
    return result


def _checkpoint_policy_evidence_map(
    value: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        _checkpoint_policy_url(url): _checkpoint_policy_evidence(evidence)
        for url, evidence in value.items()
    }


@dataclass(frozen=True, slots=True)
class _SiteScope:
    host: str
    path_prefix: str


def _site_scope(query: str) -> _SiteScope | None:
    match = re.search(r"(?:^|\s)site:([^\s]+)", query, flags=re.IGNORECASE)
    if match is None:
        return None
    value = match.group(1).strip().rstrip("/")
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    if not parsed.hostname:
        return None
    path = parsed.path.rstrip("/")
    return _SiteScope(parsed.hostname.casefold(), path.casefold())


def _url_matches_scope(url: str, scope: _SiteScope) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    if host != scope.host and not host.endswith(f".{scope.host}"):
        return False
    path = parsed.path.casefold().rstrip("/")
    return (
        not scope.path_prefix
        or path == scope.path_prefix
        or path.startswith(f"{scope.path_prefix}/")
    )


def _repository_owner(path: str) -> str | None:
    segments = [segment for segment in path.casefold().split("/") if segment]
    if not segments:
        return None
    if segments[0] in {"orgs", "users"}:
        return segments[1] if len(segments) > 1 else None
    if segments[0] in {"about", "collections", "enterprise", "features", "login", "search"}:
        return None
    return segments[0]


def _repository_identity(url: str) -> tuple[str, str, str] | None:
    """Normalize repository and raw-file URLs to (host, owner, repository)."""
    parsed = urlsplit(url)
    host = parsed.hostname.casefold() if parsed.hostname else ""
    segments = [segment.casefold() for segment in parsed.path.split("/") if segment]
    if host == "raw.githubusercontent.com":
        host = "github.com"
    if host not in _REPOSITORY_HOSTS or len(segments) < 2:
        return None
    if segments[0] in {"orgs", "users"}:
        return None
    return host, segments[0], segments[1].removesuffix(".git")


def _result_matches_scope(data: dict[str, Any], scope: _SiteScope) -> bool:
    values = data.get("results", [])
    if not isinstance(values, list):
        return False
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        if _url_matches_scope(item["url"], scope):
            return True
    return False


def _canonical_url(url: str, base_url: str = "") -> str | None:
    try:
        resolved = urljoin(base_url, url.strip())
        parsed = urlsplit(resolved)
        scheme = parsed.scheme.casefold()
        netloc = parsed.netloc.casefold()
    except (TypeError, ValueError):
        return None
    if scheme not in {"http", "https"} or not netloc:
        return None
    return urlunsplit(
        (
            scheme,
            netloc,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )


class SearchEngineOnlyPolicy:
    """Require browser search and evidence-derived URLs for report discovery.

    The policy fails closed for direct navigation/downloads. A URL becomes
    trusted only after it appears in the exact result text shown to the planner,
    or as the current page URL already present in the planner observation.
    """

    name = "search_engine_only"
    allowed_tools = frozenset(_BROWSER_TOOLS | _PDF_TOOLS)
    prompt_notice = (
        "EVALUATION POLICY — SEARCH ENGINE ONLY: Your first action MUST be the browser "
        "search tool. official_report_search, github_search, and arxiv_search are unavailable. "
        "Do not guess URLs: goto and download_pdf accept only URLs observed in search results "
        "or links on pages you visited. "
        + _LATEST_EVIDENCE_GUIDANCE
        + "For latest/newest tasks, run at least two differently "
        "worded searches; a missing result date means unknown and requires opening the candidate, "
        "not ranking it as older. If any visible result suggests a higher dotted subject version "
        "than previously seen, run a successful exact-version follow-up search before downloading "
        "or finishing; third-party leads may be rejected only after that corroboration search. "
        "At least one successful latest/newest query must include "
        f"the current year ({datetime.now(UTC).year}) or set recency=year, without restricting it to "
        "arXiv or another paper index, because official repositories can publish first. Before "
        "downloading, also run a broad subject + current-year release-landscape search containing "
        "a term such as model, version, release, series, generation, or lineup; this query must not "
        "be restricted to a paper index or one candidate version. Then first run a non-site "
        "search for the subject's official website/repository, "
        "then run a successful independent identity-bound scope search. Repository searches may "
        "use the owner path (site:github.com/Owner), the repository host plus the exact endorsed "
        "owner token (site:github.com Owner), or a plain query containing both the repository "
        "host and exact owner (GitHub Owner). The returned URL must be under that same owner; a "
        "bare host query is never sufficient. For latest/newest tasks, that scope query must use "
        "the subject (a version-qualified subject is acceptable). Recency is established "
        "separately by the required current-year landscape searches. "
        "The result must actually match the scope; paper indexes do not count. "
        "If a PDF URL opens an HTML preview, navigate to that page and call "
        "inspect_download_links; download_pdf never discovers hidden retry URLs itself. "
        "If all search engines fail, report the failure honestly."
    )

    def __init__(self, browser: PageProvider, *, artifacts_dir: Path) -> None:
        self._browser = browser
        self._artifacts_dir = artifacts_dir.resolve()
        self.reset("")

    def reset(self, task: str) -> None:
        """Clear evidence between tasks and derive task-specific rigor requirements."""
        self._task = task
        self._search_completed = False
        self._successful_searches = 0
        self._successful_queries: set[str] = set()
        self._broad_current_year_search_completed = False
        self._release_landscape_search_completed = False
        self._official_identity_search_completed = False
        self._official_scope_search_completed = False
        self._current_year = datetime.now(UTC).year
        self._step = 0
        self._observed_urls: dict[str, dict[str, Any]] = {}
        self._visited_urls: set[str] = set()
        self._downloaded_paths: dict[Path, dict[str, Any]] = {}
        self._official_identity_urls: set[str] = set()
        self._official_scope_result_urls: set[str] = set()
        self._release_landscape_evidence_urls: set[str] = set()
        self._selected_candidate_url: str | None = None
        self._figure_analysis_completed = False
        self._task_keywords = _distinctive_task_keywords(task)
        self._version_frontier: str | None = None
        self._version_frontier_key: tuple[int, ...] = ()
        self._version_frontier_resolved = True
        self._latest_task = bool(
            re.search(
                r"\b(?:latest|newest|most\s+recent)\b|最新|最近(?:的)?|最晚",
                task,
                flags=re.IGNORECASE,
            )
        )
        self._requires_pdf_artifact = re.search(r"\bpdf\b|PDF|可移植文档", task) is not None
        self._requires_figure_analysis = (
            re.search(r"\bfigure\s*\d+|图\s*\d+|图表", task, flags=re.IGNORECASE) is not None
        )

    def export_state(self) -> dict[str, Any]:
        """Return JSON-safe policy evidence for an ordinary-run checkpoint.

        The state contains only URLs, local artifact paths, counters, and derived
        search evidence. It deliberately excludes browser cookies, response bodies,
        credentials, and planner prompts.
        """
        return {
            "schema_version": 3,
            "policy": self.name,
            "task_sha256": hashlib.sha256(self._task.encode("utf-8")).hexdigest(),
            "current_year": self._current_year,
            "search_completed": self._search_completed,
            "successful_searches": self._successful_searches,
            "successful_queries": sorted(self._successful_queries),
            "broad_current_year_search_completed": self._broad_current_year_search_completed,
            "release_landscape_search_completed": self._release_landscape_search_completed,
            "official_identity_search_completed": self._official_identity_search_completed,
            "official_scope_search_completed": self._official_scope_search_completed,
            "step": self._step,
            "observed_urls": _checkpoint_policy_evidence_map(self._observed_urls),
            "visited_urls": sorted(_checkpoint_policy_url(url) for url in self._visited_urls),
            "downloaded_paths": {
                path.relative_to(
                    self._artifacts_dir.parent
                ).as_posix(): _checkpoint_policy_evidence(evidence)
                for path, evidence in self._downloaded_paths.items()
                if path == self._artifacts_dir.parent
                or path.is_relative_to(self._artifacts_dir.parent)
            },
            "official_identity_urls": sorted(
                _checkpoint_policy_url(url) for url in self._official_identity_urls
            ),
            "official_scope_result_urls": sorted(
                _checkpoint_policy_url(url) for url in self._official_scope_result_urls
            ),
            "release_landscape_evidence_urls": sorted(
                _checkpoint_policy_url(url) for url in self._release_landscape_evidence_urls
            ),
            "selected_candidate_url": (
                _checkpoint_policy_url(self._selected_candidate_url)
                if self._selected_candidate_url
                else None
            ),
            "version_frontier": self._version_frontier,
            "version_frontier_key": list(self._version_frontier_key),
            "version_frontier_resolved": self._version_frontier_resolved,
            "figure_analysis_completed": self._figure_analysis_completed,
        }

    def import_state(self, state: dict[str, Any], *, task: str) -> None:
        """Restore a checkpoint produced by :meth:`export_state`, failing closed."""
        if state.get("schema_version") != 3 or state.get("policy") != self.name:
            raise ValueError("checkpoint policy schema/name mismatch")
        if state.get("task_sha256") != hashlib.sha256(task.encode("utf-8")).hexdigest():
            raise ValueError("checkpoint policy task mismatch")
        if state.get("current_year") != datetime.now(UTC).year:
            raise ValueError("checkpoint policy evidence is from a different calendar year")

        self.reset(task)
        self._search_completed = _checkpoint_bool(state, "search_completed")
        self._successful_searches = _checkpoint_non_negative_int(state, "successful_searches")
        self._successful_queries = _checkpoint_str_set(state, "successful_queries")
        self._broad_current_year_search_completed = _checkpoint_bool(
            state, "broad_current_year_search_completed"
        )
        self._release_landscape_search_completed = _checkpoint_bool(
            state, "release_landscape_search_completed"
        )
        self._official_identity_search_completed = _checkpoint_bool(
            state, "official_identity_search_completed"
        )
        self._official_scope_search_completed = _checkpoint_bool(
            state, "official_scope_search_completed"
        )
        self._step = _checkpoint_non_negative_int(state, "step")
        self._observed_urls = _checkpoint_evidence_map(state, "observed_urls")
        self._visited_urls = _checkpoint_str_set(state, "visited_urls")
        downloaded = _checkpoint_evidence_map(state, "downloaded_paths")
        restored_downloads: dict[Path, dict[str, Any]] = {}
        output_root = self._artifacts_dir.parent
        for path, evidence in downloaded.items():
            resolved = (output_root / path).resolve()
            if resolved != output_root and not resolved.is_relative_to(output_root):
                raise ValueError("checkpoint downloaded path escapes the output root")
            restored_downloads[resolved] = evidence
        self._downloaded_paths = restored_downloads
        self._official_identity_urls = _checkpoint_str_set(state, "official_identity_urls")
        self._official_scope_result_urls = _checkpoint_str_set(state, "official_scope_result_urls")
        self._release_landscape_evidence_urls = _checkpoint_str_set(
            state, "release_landscape_evidence_urls"
        )
        selected = state.get("selected_candidate_url")
        self._selected_candidate_url = selected if isinstance(selected, str) else None
        frontier = state.get("version_frontier")
        self._version_frontier = frontier if isinstance(frontier, str) else None
        raw_key = state.get("version_frontier_key")
        if not isinstance(raw_key, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in raw_key
        ):
            raise ValueError("checkpoint field version_frontier_key is invalid")
        self._version_frontier_key = tuple(raw_key)
        self._version_frontier_resolved = _checkpoint_bool(state, "version_frontier_resolved")
        self._figure_analysis_completed = state.get("figure_analysis_completed") is True

    async def authorize(self, tool_call: ToolCall) -> PolicyDecision:
        self._step += 1
        name = tool_call.tool_name.casefold()
        if name not in self.allowed_tools:
            return self._deny(name, "tool is excluded from search-engine-only evaluation")
        if name == "download_file" and (filename := tool_call.parameters.get("filename")):
            normalized = Path(str(filename)).name
            if not normalized or normalized.casefold() not in self._task.casefold():
                return self._deny(
                    name,
                    "omit filename to preserve the browser-suggested name unless the task "
                    "explicitly requests that exact filename",
                )
        if not self._search_completed and name != "search":
            return self._deny(name, "first successful action must be browser search")
        if name == "search":
            return PolicyDecision(
                True, "browser search is the required discovery action", self._step
            )

        # Capture the current page before evaluating latest-report gates. A
        # click may have navigated to a PDF preview since the preceding tool
        # result, and that page is legitimate browser-grounded evidence.
        self._record_current_page_url()
        if self._latest_task and name in {"done", "download_pdf"}:
            target_url = tool_call.parameters.get("url") if name == "download_pdf" else None
            missing = self._latest_missing_prerequisites(target_url)
            if missing:
                return self._deny_missing_latest(name, missing)

        if name == "done":
            return self._authorize_done(tool_call)
        if name in {"goto", "download_pdf"} or (
            name == "open_tab" and tool_call.parameters.get("url")
        ):
            return self._authorize_url(name, tool_call.parameters.get("url"))
        if name.startswith("pdf_"):
            return self._authorize_pdf_path(name, tool_call.parameters.get("path"))
        return PolicyDecision(
            True,
            "browser interaction is grounded in the current page",
            self._step,
        )

    async def record_result(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        decision: PolicyDecision,
        *,
        planner_visible_result: str,
    ) -> dict[str, Any]:
        audit = decision.as_audit(self.name)
        if not decision.allowed:
            return audit

        name = tool_call.tool_name.casefold()
        before_count = len(self._observed_urls)
        visible_data = _decode_visible_result(planner_visible_result)
        if (
            name in {"search", "get_search_results"}
            and result.success
            and self._has_search_evidence(visible_data)
        ):
            query = (
                tool_call.parameters.get("query") if name == "search" else visible_data.get("query")
            )
            recency = tool_call.parameters.get("recency") if name == "search" else None
            self._record_search_evidence(query, visible_data, recency=recency)
        self._record_visible_urls(visible_data, source=f"{name}_planner_visible")
        if name in {"goto", "open_tab"} and result.success:
            self._record_navigated_candidate(tool_call, visible_data)
        if name == "inspect_download_links" and result.success:
            self._record_selected_candidate(visible_data)
        if name == "download_pdf" and result.success:
            self._record_download(result, tool_call.parameters.get("url"))
        if name == "pdf_analyze_figure" and result.success:
            self._figure_analysis_completed = True
        if result.success:
            self._record_current_page_visit()

        latest_missing = self._latest_missing_prerequisites() if self._latest_task else ()

        audit.update(
            {
                "search_completed": self._search_completed,
                "successful_searches": self._successful_searches,
                "broad_current_year_search_completed": (self._broad_current_year_search_completed),
                "release_landscape_search_completed": self._release_landscape_search_completed,
                "release_landscape_evidence_count": len(self._release_landscape_evidence_urls),
                "official_identity_search_completed": self._official_identity_search_completed,
                "official_scope_search_completed": self._official_scope_search_completed,
                "official_scope_result_count": len(self._official_scope_result_urls),
                "selected_candidate_url": self._selected_candidate_url,
                "selected_candidate_identity_endorsed": (
                    self._identity_endorses_target(self._selected_candidate_url)
                    if self._selected_candidate_url is not None
                    else None
                ),
                "version_frontier": self._version_frontier,
                "newer_version_leads_resolved": self._version_frontier_resolved,
                "latest_evidence_complete": self._latest_task and not latest_missing,
                "latest_missing_prerequisites": list(latest_missing),
                "new_urls_observed": len(self._observed_urls) - before_count,
                "observed_url_count": len(self._observed_urls),
                "downloaded_artifact_count": len(self._downloaded_paths),
                "figure_analysis_completed": self._figure_analysis_completed,
            }
        )
        return audit

    def _latest_missing_prerequisites(self, target_url: Any = None) -> tuple[str, ...]:
        if not isinstance(target_url, str):
            target_url = self._selected_candidate_url
        missing: list[str] = []
        if self._successful_searches < 2:
            missing.append("at least two successful browser searches with distinct queries")
        if not self._broad_current_year_search_completed:
            missing.append(
                f"a broad {self._current_year} search without an arXiv/aggregator restriction"
            )
        if not self._release_landscape_search_completed:
            missing.append(
                "a subject-wide current-year release landscape search whose results show "
                "relevant model/version/release evidence"
            )
        target_identity_endorsed = (
            self._identity_endorses_target(target_url) if isinstance(target_url, str) else True
        )
        if not self._official_identity_search_completed:
            missing.append("a non-site official identity search for the subject")
        elif isinstance(target_url, str) and not target_identity_endorsed:
            missing.append(
                "a non-site official identity search whose results endorse the selected "
                f"target host/owner ({self._target_identity_label(target_url)})"
            )
        if not self._official_scope_search_completed:
            missing.append(
                "an independent scope search whose query contains the endorsed host/owner and "
                "selected candidate name, "
                "with results that cover that candidate "
                "(paper indexes do not count)"
            )
        elif isinstance(target_url, str) and not self._scope_covers_target_url(target_url):
            missing.append(
                "an identity-bound scope result corroborating the selected candidate repository "
                "or host"
            )
        if not self._version_frontier_resolved:
            missing.append(
                f"an exact version follow-up search for the highest observed version "
                f"{self._version_frontier!r}"
            )
        return tuple(missing)

    def _latest_prerequisite_failure(self, target_url: Any = None) -> str | None:
        """Compatibility wrapper returning the complete, not fail-fast, checklist."""
        missing = self._latest_missing_prerequisites(target_url)
        return self._format_missing_latest(missing) if missing else None

    @staticmethod
    def _format_missing_latest(missing: tuple[str, ...]) -> str:
        bullets = "\n".join(f"- {item}" for item in missing)
        return (
            "latest/newest evidence is incomplete; complete every missing prerequisite "
            f"before retrying:\n{bullets}"
        )

    def _deny_missing_latest(self, target: str, missing: tuple[str, ...]) -> PolicyDecision:
        return PolicyDecision(
            False,
            self._format_missing_latest(missing),
            self._step,
            target=target,
            provenance={"missing_prerequisites": list(missing)},
        )

    def _record_search_evidence(
        self,
        query: Any,
        data: dict[str, Any],
        *,
        recency: Any = None,
    ) -> None:
        self._search_completed = True
        if not isinstance(query, str) or not query.strip():
            return
        normalized = " ".join(query.casefold().split())
        self._successful_queries.add(hashlib.sha256(normalized.encode("utf-8")).hexdigest())
        self._successful_searches = len(self._successful_queries)
        current_year_evidence = str(self._current_year) in normalized or recency == "year"
        self._record_current_year_search(
            normalized,
            data,
            current_year_evidence=current_year_evidence,
        )
        site_scope = _site_scope(normalized)
        identity_already_completed = self._official_identity_search_completed
        explicit_identity_urls = self._explicit_official_identity_urls(data)
        if (
            site_scope is None
            and self._query_matches_task(normalized)
            and (re.search(r"\bofficial\b", normalized) or explicit_identity_urls)
        ):
            self._official_identity_urls.update(explicit_identity_urls or self._result_urls(data))
            self._official_identity_search_completed = bool(self._official_identity_urls)
        if (
            identity_already_completed
            and self._identity_bound_scope_result(site_scope, normalized, data)
            and self._official_scope_query_is_broad(normalized)
        ):
            self._official_scope_search_completed = True
            self._official_scope_result_urls.update(self._result_urls(data))
        self._update_version_frontier(normalized, data)

    def _record_current_year_search(
        self,
        query: str,
        data: dict[str, Any],
        *,
        current_year_evidence: bool,
    ) -> None:
        if not current_year_evidence or any(term in query for term in _SEARCH_INDEX_TERMS):
            return
        self._broad_current_year_search_completed = True
        landscape_urls = self._release_landscape_result_evidence(data)
        if (
            any(term in query for term in _RELEASE_LANDSCAPE_TERMS)
            and self._query_matches_task(query)
            and not self._query_has_subject_version(query)
            and landscape_urls
        ):
            self._release_landscape_search_completed = True
            self._release_landscape_evidence_urls.update(landscape_urls)

    def _record_download(self, result: ToolResult, source_url: Any) -> None:
        path_value = result.data.get("path")
        if not isinstance(path_value, str):
            return
        path = self._resolve_artifact_path(path_value)
        if path is not None:
            self._downloaded_paths[path] = {
                "source_tool": "download_pdf",
                "policy_step": self._step,
                "source_url": source_url,
            }

    def _record_navigated_candidate(self, tool_call: ToolCall, data: dict[str, Any]) -> None:
        values = (data.get("url"), tool_call.parameters.get("url"))
        for value in values:
            canonical = _canonical_url(value) if isinstance(value, str) else None
            if canonical is not None and urlsplit(canonical).path.casefold().endswith(".pdf"):
                self._selected_candidate_url = canonical
                return

    def _record_selected_candidate(self, data: dict[str, Any]) -> None:
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            return
        ranked: list[tuple[int, str]] = []
        source_url = data.get("source_url")
        candidate_values: list[dict[str, Any]] = list(candidates)
        if isinstance(source_url, str):
            candidate_values.append(
                {
                    "url": source_url,
                    "evidence_type": "source_page",
                }
            )
        for candidate in candidate_values:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("url"), str):
                continue
            canonical = _canonical_url(candidate["url"])
            if canonical is None:
                continue
            # Preview pages can expose transient iframe/blob resources before the
            # actual declared download URL. Prefer a repository-addressable URL
            # so later owner/scope evidence binds to the selected artifact rather
            # than to an implementation detail of the preview renderer.
            score = 2 if _repository_identity(canonical) is not None else 0
            if candidate.get("evidence_type") == "declared_page_metadata":
                score += 1
            ranked.append((score, canonical))
        if ranked:
            self._selected_candidate_url = max(ranked, key=lambda item: item[0])[1]

    def denial_audit(self, tool_name: str, reason: str) -> dict[str, Any]:
        """Build an audit record when the executor rejects a hidden tool early."""
        self._step += 1
        return self._deny(tool_name, reason).as_audit(self.name)

    def _deny(self, target: str, reason: str) -> PolicyDecision:
        return PolicyDecision(False, reason, self._step, target=target)

    def _authorize_url(self, tool_name: str, value: Any) -> PolicyDecision:
        if not isinstance(value, str):
            return self._deny(tool_name, "URL-bearing tool requires a string URL")
        canonical = _canonical_url(value, self._browser.page.url)
        if canonical is None:
            return self._deny(value, "target is not a valid HTTP(S) URL")
        current = _canonical_url(self._browser.page.url)
        if tool_name == "goto" and canonical == current:
            return self._deny(
                canonical,
                "already on this exact URL; interact with the current page instead, or use "
                "reload only when an explicit refresh is required",
            )
        evidence = self._observed_urls.get(canonical)
        if evidence is None:
            return self._deny(canonical, "target URL was not observed in prior browser evidence")
        return PolicyDecision(
            True,
            "target URL is grounded in prior browser evidence",
            self._step,
            target=canonical,
            provenance=evidence,
        )

    def _authorize_pdf_path(self, tool_name: str, value: Any) -> PolicyDecision:
        if not isinstance(value, str):
            return self._deny(tool_name, "PDF tool requires a downloaded path")
        path = self._resolve_artifact_path(value)
        evidence = self._downloaded_paths.get(path) if path is not None else None
        if path is None or evidence is None:
            return self._deny(str(value), "PDF path was not produced by an allowed download")
        return PolicyDecision(
            True,
            "PDF path was produced by an evidence-grounded download",
            self._step,
            target=str(path),
            provenance=evidence,
        )

    def _record_current_page_url(self) -> None:
        current = _canonical_url(self._browser.page.url)
        if current is not None:
            self._record_url(
                current,
                source="planner_state_current_url",
                page_url=self._browser.page.url,
            )
            if urlsplit(current).path.casefold().endswith(".pdf"):
                self._selected_candidate_url = current

    def _record_current_page_visit(self) -> None:
        current = _canonical_url(self._browser.page.url)
        if current is not None:
            self._record_url(current, source="visited_page", page_url=self._browser.page.url)
            self._visited_urls.add(current)
            if urlsplit(current).path.casefold().endswith(".pdf"):
                self._selected_candidate_url = current

    def _authorize_done(self, tool_call: ToolCall) -> PolicyDecision:
        missing_deliverables: list[str] = []
        if self._requires_pdf_artifact and not self._downloaded_paths:
            missing_deliverables.append("download the requested PDF with download_pdf")
        if self._requires_figure_analysis and not self._figure_analysis_completed:
            missing_deliverables.append(
                "analyze the requested figure with pdf_analyze_figure using the downloaded PDF"
            )
        if missing_deliverables:
            return self._deny(
                "done",
                "required task deliverables are incomplete: " + "; ".join(missing_deliverables),
            )
        content_visits = {
            url
            for url in self._visited_urls
            if not any(
                marker in (urlsplit(url).hostname or "") for marker in _SEARCH_ENGINE_HOST_MARKERS
            )
        }
        if not content_visits:
            return self._deny("done", "completion requires visiting a non-search evidence page")
        summary = tool_call.parameters.get("summary")
        if isinstance(summary, str):
            cited = {
                canonical
                for raw in re.findall(r"https?://[^\s<>\"']+", summary)
                if (canonical := _canonical_url(raw.rstrip(".,;:!?)]}>`。，；：！？）】》")))
                is not None
            }
            unobserved = sorted(cited - set(self._observed_urls))
            if unobserved:
                return self._deny(
                    "done",
                    "completion cites URL(s) absent from browser evidence: "
                    + ", ".join(unobserved[:3]),
                )
        return PolicyDecision(
            True,
            "completion is grounded in a visited evidence page",
            self._step,
        )

    @staticmethod
    def _has_search_evidence(data: dict[str, Any]) -> bool:
        results = data.get("results")
        return bool(
            isinstance(results, list)
            and any(
                isinstance(item, dict) and isinstance(item.get("url") or item.get("link"), str)
                for item in results
            )
        )

    def _record_visible_urls(self, data: dict[str, Any], *, source: str) -> None:
        """Trust only complete URL values in the exact JSON shown to the planner."""
        page_url = str(data.get("source_url") or data.get("url") or self._browser.page.url)
        for value in _iter_values(data):
            if isinstance(value, str) and _canonical_url(value, page_url) is not None:
                self._record_url(value, source=source, page_url=page_url)

    @staticmethod
    def _result_urls(data: dict[str, Any]) -> set[str]:
        urls: set[str] = set()
        results = data.get("results", [])
        if not isinstance(results, list):
            return urls
        for item in results:
            if not isinstance(item, dict):
                continue
            value = item.get("url") or item.get("link")
            if not isinstance(value, str):
                continue
            canonical = _canonical_url(value)
            if canonical is not None:
                urls.add(canonical)
        return urls

    def _scope_was_endorsed(self, scope: _SiteScope) -> bool:
        if scope.host in _REPOSITORY_HOSTS:
            if not scope.path_prefix:
                return False
            target_owner = _repository_owner(scope.path_prefix)
            if target_owner is None:
                return False
            return any(
                urlsplit(url).hostname == scope.host
                and _repository_owner(urlsplit(url).path.casefold()) == target_owner
                for url in self._official_identity_urls
            )
        return any(_url_matches_scope(url, scope) for url in self._official_identity_urls)

    def _identity_bound_site_result(
        self, scope: _SiteScope, query: str, data: dict[str, Any]
    ) -> bool:
        """Accept path scopes or an owner-token fallback for repository search engines.

        Some engines treat ``site:github.com/Owner`` as an invalid or empty scope. The
        fallback keeps the independent search but requires all three bindings: the owner
        appeared in the prior official-identity results, the exact owner token is present
        in the new query, and the new result URL is under that same repository owner.
        """
        if scope.host not in _REPOSITORY_HOSTS or scope.path_prefix:
            return _result_matches_scope(data, scope) and self._scope_was_endorsed(scope)

        query_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
        endorsed_owners = {
            owner
            for url in self._official_identity_urls
            if urlsplit(url).hostname == scope.host
            if (owner := _repository_owner(urlsplit(url).path)) is not None
        }
        query_owners = endorsed_owners & query_tokens
        if not query_owners:
            return False
        return any(
            urlsplit(url).hostname == scope.host
            and _repository_owner(urlsplit(url).path) in query_owners
            for url in self._result_urls(data)
        )

    def _identity_bound_scope_result(
        self, site_scope: _SiteScope | None, query: str, data: dict[str, Any]
    ) -> bool:
        """Validate either a site operator or a plain host-and-owner corroboration query."""
        if site_scope is not None:
            if any(term in site_scope.host for term in _SEARCH_INDEX_HOST_TERMS):
                return False
            return self._identity_bound_site_result(site_scope, query, data)

        query_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
        result_urls = self._result_urls(data)
        for identity_url in self._official_identity_urls:
            parsed = urlsplit(identity_url)
            host = parsed.hostname.casefold() if parsed.hostname else ""
            if not host or any(term in host for term in _SEARCH_INDEX_HOST_TERMS):
                continue
            if host in _REPOSITORY_HOSTS:
                owner = _repository_owner(parsed.path)
                host_token = host.split(".", 1)[0]
                if owner not in query_tokens or not ({host, host_token} & query_tokens):
                    continue
                if any(
                    urlsplit(url).hostname == host
                    and _repository_owner(urlsplit(url).path) == owner
                    for url in result_urls
                ):
                    return True
                continue

            visible_host = host.removeprefix("www.")
            if visible_host not in query and host not in query:
                continue
            scope = _SiteScope(host=host, path_prefix="")
            if any(_url_matches_scope(url, scope) for url in result_urls):
                return True
        return False

    def _query_matches_task(self, query: str) -> bool:
        if not self._task_keywords:
            return True
        query_tokens = set(re.findall(r"[a-z0-9][a-z0-9._-]*", query.casefold()))
        return any(
            task_token == query_token
            or (len(task_token) >= 4 and query_token.startswith(task_token))
            for task_token in self._task_keywords
            for query_token in query_tokens
        )

    def _query_has_subject_version(self, query: str) -> bool:
        return any(
            re.search(
                rf"(?<![a-z0-9]){re.escape(keyword)}[-_ ]*{_VERSION_SUFFIX_RE.pattern}",
                query,
                flags=re.IGNORECASE,
            )
            is not None
            for keyword in self._task_keywords
        )

    def _official_scope_query_is_broad(self, query: str) -> bool:
        return not self._latest_task or self._query_matches_task(query)

    def _explicit_official_identity_urls(self, data: dict[str, Any]) -> set[str]:
        """Return result URLs whose visible text explicitly claims official status."""
        identity_urls: set[str] = set()
        results = data.get("results", [])
        if not isinstance(results, list):
            return identity_urls
        for item in results:
            if not isinstance(item, dict):
                continue
            searchable = " ".join(
                str(item.get(field, "")).casefold() for field in ("title", "snippet")
            )
            if re.search(r"\bofficial\b", searchable) is None:
                continue
            value = item.get("url") or item.get("link")
            canonical = _canonical_url(value) if isinstance(value, str) else None
            if canonical is not None:
                identity_urls.add(canonical)
        return identity_urls

    def _release_landscape_result_evidence(self, data: dict[str, Any]) -> set[str]:
        """Require release/version semantics in results, not only in the query text."""
        evidence: set[str] = set()
        results = data.get("results", [])
        if not isinstance(results, list):
            return evidence
        for item in results:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            searchable = " ".join(
                str(item.get(field, "")).casefold() for field in ("title", "url", "snippet")
            )
            if not self._query_matches_task(searchable):
                continue
            if not (
                any(term in searchable for term in _RELEASE_LANDSCAPE_TERMS)
                or self._result_version_leads({"results": [item]})
            ):
                continue
            canonical = _canonical_url(item["url"])
            if canonical is not None:
                evidence.add(canonical)
        return evidence

    def _scope_covers_target_url(self, target_url: str) -> bool:
        target = _canonical_url(target_url)
        if target is None:
            return False
        # The evidence can arrive in either order. If an earlier successful
        # SERP exposed the exact candidate, and a later independent current-year
        # scope search established the endorsed owner/host, requiring the later
        # SERP to rediscover the identical repository adds no provenance value
        # and can create an endless loop on stale search indexes.
        target_evidence = self._observed_urls.get(target, {})
        if target_evidence.get("source") in {
            "search_planner_visible",
            "get_search_results_planner_visible",
        } and self._identity_endorses_target(target):
            return True
        target_repository = _repository_identity(target)
        if target_repository is not None:
            return any(
                _repository_identity(result_url) == target_repository
                for result_url in self._official_scope_result_urls
            )
        target_host = urlsplit(target).hostname
        return any(
            urlsplit(result_url).hostname == target_host
            for result_url in self._official_scope_result_urls
        )

    def _identity_endorses_target(self, target_url: str) -> bool:
        target = _canonical_url(target_url)
        if target is None:
            return False
        target_repository = _repository_identity(target)
        if target_repository is not None:
            target_host, target_owner, _ = target_repository
            return any(
                (identity := _repository_identity(identity_url)) is not None
                and identity[:2] == (target_host, target_owner)
                for identity_url in self._official_identity_urls
            )
        target_hostname = urlsplit(target).hostname
        return any(
            urlsplit(identity_url).hostname == target_hostname
            for identity_url in self._official_identity_urls
        )

    @staticmethod
    def _target_identity_label(target_url: str) -> str:
        target = _canonical_url(target_url)
        if target is None:
            return "unknown"
        repository = _repository_identity(target)
        if repository is not None:
            host, owner, _ = repository
            return f"{host}/{owner}"
        return urlsplit(target).hostname or "unknown"

    def _update_version_frontier(self, query: str, data: dict[str, Any]) -> None:
        """Require explicit follow-up for the highest subject-version lead on a SERP.

        Search results often expose a newer release first through a third-party page. Such
        a page is not first-party proof, but dismissing it without an exact-version search
        is also unsound. Only tokens joined to a distinctive task keyword are considered,
        which avoids treating dates and arXiv identifiers as product versions.
        """
        if self._version_frontier is not None and self._version_frontier in query:
            self._version_frontier_resolved = True
        leads = self._result_version_leads(data)
        if leads:
            lead = max(leads, key=_version_key)
            lead_key = _version_key(lead)
            if lead_key > self._version_frontier_key:
                self._version_frontier = lead
                self._version_frontier_key = lead_key
                self._version_frontier_resolved = lead in query
            elif lead == self._version_frontier and lead in query:
                self._version_frontier_resolved = True

    def _result_version_leads(self, data: dict[str, Any]) -> set[str]:
        results = data.get("results", [])
        if not isinstance(results, list) or not self._task_keywords:
            return set()
        leads: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            searchable = " ".join(str(item.get(field, "")).casefold() for field in ("title", "url"))
            for keyword in self._task_keywords:
                pattern = re.compile(
                    rf"(?<![a-z0-9])({re.escape(keyword)}[-_ ]*"
                    rf"{_VERSION_SUFFIX_RE.pattern})(?![a-z0-9.])",
                    flags=re.IGNORECASE,
                )
                leads.update(
                    match.group(1).replace("-", "").replace("_", "").replace(" ", "")
                    for match in pattern.finditer(searchable)
                )
        return leads

    def _record_url(self, url: str, *, source: str, page_url: str) -> None:
        canonical = _canonical_url(url, page_url)
        if canonical is None:
            return
        evidence = {
            "source": source,
            "policy_step": self._step,
            "page_url": page_url,
        }
        previous = self._observed_urls.get(canonical)
        if previous is None:
            self._observed_urls[canonical] = evidence
            return
        previous_source = previous.get("source")
        if (
            source in PLANNER_VISIBLE_URL_PROVENANCE_SOURCES
            and previous_source not in PLANNER_VISIBLE_URL_PROVENANCE_SOURCES
        ):
            # A click can visit a page before its URL has appeared in a compact
            # planner-visible result. Upgrade that internal observation once the
            # browser state or a later tool explicitly exposes the URL.
            self._observed_urls[canonical] = evidence

    def _resolve_artifact_path(self, value: str) -> Path | None:
        path = Path(value)
        if not path.is_absolute():
            path = self._artifacts_dir / path
        resolved = path.resolve()
        output_root = self._artifacts_dir.parent
        if resolved != output_root and not resolved.is_relative_to(output_root):
            return None
        return resolved


class BrowserGroundedPolicy(SearchEngineOnlyPolicy):
    """Bind URL and PDF actions to user/browser evidence without benchmark rigor.

    Ordinary runs may start on an already-open page or on a URL explicitly supplied
    by the user, so they do not require search as the first action. Unlike hybrid
    mode, however, a planner cannot invent a navigation or download URL.
    """

    name = "browser_grounded"
    prompt_notice = (
        "BROWSER-GROUNDED MODE: Direct arXiv/GitHub discovery APIs are unavailable. "
        "Navigate or download only URLs explicitly supplied by the user or observed in "
        "browser/tool evidence. Discovery tasks without a user URL or already-loaded HTTP(S) "
        "page must start with browser search. Latest/newest web discovery tasks must also "
        "satisfy independent recency and official-source evidence checks before download or "
        "completion. " + _LATEST_EVIDENCE_GUIDANCE
    )

    def __init__(
        self,
        browser: PageProvider,
        *,
        artifacts_dir: Path,
        allowed_tools: Collection[str],
        require_browser_search: bool = True,
        official_report_max_attempts: int = 2,
        evidence_repeat_limit: int = 3,
    ) -> None:
        self.allowed_tools = frozenset(name.casefold() for name in allowed_tools)
        self._require_browser_search = require_browser_search
        self._hybrid_mode = not require_browser_search
        self._official_report_max_attempts = official_report_max_attempts
        self._evidence_repeat_limit = evidence_repeat_limit
        if not require_browser_search:
            self.prompt_notice = (
                "HYBRID DISCOVERY MODE: Direct first-party discovery tools are available. For a "
                "latest/newest technical-report or PDF task, call official_report_search first "
                "with only the project/family name as subject and the known official repository "
                "owner when available (for Qwen, official_owner is QwenLM). Prefer its newest "
                "verified_first_party_candidate. A dated report PDF under the exact requested "
                "owner satisfies identity and candidate-scope provenance. Then run exactly one "
                "subject-wide current-year release-landscape search; investigate only a genuinely "
                "higher observed version. As soon as that cross-check is complete, call "
                "download_pdf with the policy-selected pdf_url instead of rewriting searches or "
                "calling official_report_search again. Repeated discovery calls are policy-bounded. "
                "Never call done before all requested artifact and figure-analysis deliverables "
                "have succeeded."
            )
        super().__init__(browser, artifacts_dir=artifacts_dir)

    def reset(self, task: str) -> None:
        super().reset(task)
        self._hybrid_direct_evidence_verified = False
        self._hybrid_candidate_url: str | None = None
        self._hybrid_candidate_date: str | None = None
        self._hybrid_candidate_owner: str | None = None
        self._hybrid_candidate_version_key: tuple[int, ...] = ()
        self._hybrid_subject = ""
        self._hybrid_official_report_attempts: dict[str, int] = {}
        self._hybrid_successful_topics: set[str] = set()
        self._hybrid_missing_signature: tuple[str, ...] = ()
        self._hybrid_missing_repeat_count = 0
        self._hybrid_cross_check_exhausted = False
        self._hybrid_required_next_action: dict[str, Any] | None = None
        task_urls = re.findall(r"https?://[^\s<>\"']+", task, flags=re.IGNORECASE)
        page = getattr(self._browser, "page", None)
        page_url = getattr(page, "url", None)
        current_page_url = _canonical_url(page_url) if isinstance(page_url, str) else None
        self._discovery_required = (
            self._require_browser_search
            and not task_urls
            and current_page_url is None
            and _DISCOVERY_TASK_RE.search(task) is not None
        )
        for match in task_urls:
            url = match.rstrip(".,);]")
            self._record_url(url, source="user_task", page_url=url)

    def export_state(self) -> dict[str, Any]:
        state = super().export_state()
        state["hybrid"] = {
            "direct_evidence_verified": self._hybrid_direct_evidence_verified,
            "candidate_url": (
                _checkpoint_policy_url(self._hybrid_candidate_url)
                if self._hybrid_candidate_url
                else None
            ),
            "candidate_date": self._hybrid_candidate_date,
            "candidate_owner": self._hybrid_candidate_owner,
            "candidate_version_key": list(self._hybrid_candidate_version_key),
            "subject": self._hybrid_subject,
            "official_report_attempts": dict(self._hybrid_official_report_attempts),
            "successful_topics": sorted(self._hybrid_successful_topics),
            "missing_signature": list(self._hybrid_missing_signature),
            "missing_repeat_count": self._hybrid_missing_repeat_count,
            "cross_check_exhausted": self._hybrid_cross_check_exhausted,
            "required_next_action": self._hybrid_required_next_action,
        }
        return state

    def import_state(self, state: dict[str, Any], *, task: str) -> None:
        raw_hybrid = state.get("hybrid")
        super().import_state(state, task=task)
        # Version-3 checkpoints written before Hybrid evidence accounting remain
        # resumable; they simply restart the bounded direct-source bookkeeping.
        if raw_hybrid is None:
            return
        if not isinstance(raw_hybrid, dict):
            raise ValueError("checkpoint field hybrid must be an object")
        verified = raw_hybrid.get("direct_evidence_verified")
        exhausted = raw_hybrid.get("cross_check_exhausted")
        if not isinstance(verified, bool) or not isinstance(exhausted, bool):
            raise ValueError("checkpoint Hybrid flags must be boolean")
        candidate_url = raw_hybrid.get("candidate_url")
        candidate_date = raw_hybrid.get("candidate_date")
        candidate_owner = raw_hybrid.get("candidate_owner")
        subject = raw_hybrid.get("subject")
        if candidate_url is not None and not isinstance(candidate_url, str):
            raise ValueError("checkpoint Hybrid candidate URL is invalid")
        if candidate_date is not None and not isinstance(candidate_date, str):
            raise ValueError("checkpoint Hybrid candidate date is invalid")
        if candidate_owner is not None and not isinstance(candidate_owner, str):
            raise ValueError("checkpoint Hybrid candidate owner is invalid")
        if not isinstance(subject, str):
            raise ValueError("checkpoint Hybrid subject is invalid")
        raw_version_key = raw_hybrid.get("candidate_version_key")
        if not isinstance(raw_version_key, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in raw_version_key
        ):
            raise ValueError("checkpoint Hybrid candidate version is invalid")
        attempts = raw_hybrid.get("official_report_attempts")
        if not isinstance(attempts, dict) or not all(
            isinstance(key, str)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for key, value in attempts.items()
        ):
            raise ValueError("checkpoint Hybrid attempt counters are invalid")
        topics = raw_hybrid.get("successful_topics")
        signature = raw_hybrid.get("missing_signature")
        repeat_count = raw_hybrid.get("missing_repeat_count")
        action = raw_hybrid.get("required_next_action")
        if not isinstance(topics, list) or not all(isinstance(item, str) for item in topics):
            raise ValueError("checkpoint Hybrid successful topics are invalid")
        if not isinstance(signature, list) or not all(isinstance(item, str) for item in signature):
            raise ValueError("checkpoint Hybrid missing signature is invalid")
        if not isinstance(repeat_count, int) or isinstance(repeat_count, bool) or repeat_count < 0:
            raise ValueError("checkpoint Hybrid repeat count is invalid")
        if action is not None and not isinstance(action, dict):
            raise ValueError("checkpoint Hybrid next action is invalid")
        self._hybrid_direct_evidence_verified = verified
        self._hybrid_candidate_url = candidate_url
        self._hybrid_candidate_date = candidate_date
        self._hybrid_candidate_owner = candidate_owner
        self._hybrid_candidate_version_key = tuple(raw_version_key)
        self._hybrid_subject = subject
        self._hybrid_official_report_attempts = dict(attempts)
        self._hybrid_successful_topics = set(topics)
        self._hybrid_missing_signature = tuple(signature)
        self._hybrid_missing_repeat_count = repeat_count
        self._hybrid_cross_check_exhausted = exhausted
        self._hybrid_required_next_action = dict(action) if action is not None else None

    async def record_result(
        self,
        tool_call: ToolCall,
        result: ToolResult,
        decision: PolicyDecision,
        *,
        planner_visible_result: str,
    ) -> dict[str, Any]:
        name = tool_call.tool_name.casefold()
        visible_data = _decode_visible_result(planner_visible_result)
        if (
            self._hybrid_mode
            and result.success
            and name
            in {
                "official_report_search",
                "github_search",
            }
        ):
            self._record_hybrid_direct_evidence(tool_call, visible_data)

        audit = await super().record_result(
            tool_call,
            result,
            decision,
            planner_visible_result=planner_visible_result,
        )
        if not self._hybrid_mode:
            return audit

        if name == "search" and result.success:
            self._reconcile_hybrid_version_cross_check()
        if name in _HYBRID_DISCOVERY_TOOLS:
            self._update_hybrid_missing_progress()

        missing = self._latest_missing_prerequisites() if self._latest_task else ()
        self._hybrid_required_next_action = self._hybrid_next_action(missing)
        audit.update(
            {
                "official_identity_search_completed": self._official_identity_search_completed,
                "official_scope_search_completed": self._official_scope_search_completed,
                "selected_candidate_url": self._selected_candidate_url,
                "selected_candidate_identity_endorsed": (
                    self._identity_endorses_target(self._selected_candidate_url)
                    if self._selected_candidate_url is not None
                    else None
                ),
                "newer_version_leads_resolved": self._version_frontier_resolved,
                "latest_evidence_complete": self._latest_task and not missing,
                "latest_missing_prerequisites": list(missing),
                "hybrid_direct_evidence_verified": self._hybrid_direct_evidence_verified,
                "hybrid_candidate_date": self._hybrid_candidate_date,
                "hybrid_candidate_owner": self._hybrid_candidate_owner,
                "hybrid_missing_repeat_count": self._hybrid_missing_repeat_count,
                "hybrid_cross_check_exhausted": self._hybrid_cross_check_exhausted,
                "required_next_action": self._hybrid_required_next_action,
            }
        )
        return audit

    def _record_hybrid_direct_evidence(self, tool_call: ToolCall, data: dict[str, Any]) -> None:
        candidates: list[dict[str, Any]] = []
        owner_value: Any = None
        if tool_call.tool_name.casefold() == "official_report_search":
            raw = data.get("verified_first_party_candidates")
            owner_value = tool_call.parameters.get("official_owner")
            if isinstance(raw, list):
                candidates = [item for item in raw if isinstance(item, dict)]
        elif tool_call.tool_name.casefold() == "github_search":
            raw = data.get("candidates")
            owner_value = tool_call.parameters.get("owner")
            if isinstance(raw, list):
                candidates = [item for item in raw if isinstance(item, dict)]
        owner = owner_value.strip().casefold() if isinstance(owner_value, str) else ""
        if not owner:
            return

        verified: list[tuple[str, str, str, tuple[int, ...], dict[str, Any]]] = []
        for candidate in candidates:
            if candidate.get("first_party") is not True:
                continue
            date_value = candidate.get("date") or candidate.get("committed_at")
            pdf_value = candidate.get("pdf_url") or candidate.get("download_url")
            html_value = candidate.get("html_url")
            if (
                not isinstance(date_value, str)
                or re.match(r"^\d{4}-\d{2}-\d{2}", date_value) is None
            ):
                continue
            if not isinstance(pdf_value, str) or not isinstance(html_value, str):
                continue
            pdf_url = _canonical_url(pdf_value)
            html_url = _canonical_url(html_value)
            identity = _repository_identity(html_url or "") or _repository_identity(pdf_url or "")
            if (
                pdf_url is None
                or html_url is None
                or identity is None
                or identity[1] != owner
                or _REPORT_FILE_RE.search(urlsplit(pdf_url).path) is None
            ):
                continue
            searchable = " ".join(
                str(candidate.get(field, "")) for field in ("title", "pdf_url", "download_url")
            )
            leads = self._result_version_leads(
                {"results": [{"title": searchable, "url": html_url}]}
            )
            version_key = max((_version_key(lead) for lead in leads), default=())
            verified.append((date_value, pdf_url, html_url, version_key, candidate))
        if not verified:
            return

        date_value, pdf_url, html_url, version_key, _ = max(
            verified, key=lambda item: (item[0], item[3])
        )
        subject_value = tool_call.parameters.get("subject") or tool_call.parameters.get("query")
        self._hybrid_subject = (
            subject_value.strip() if isinstance(subject_value, str) else self._hybrid_subject
        )
        self._hybrid_direct_evidence_verified = True
        self._hybrid_candidate_url = pdf_url
        self._hybrid_candidate_date = date_value
        self._hybrid_candidate_owner = owner
        self._hybrid_candidate_version_key = version_key
        self._selected_candidate_url = pdf_url
        self._official_identity_search_completed = True
        self._official_scope_search_completed = True
        self._official_identity_urls.update({html_url, pdf_url})
        self._official_scope_result_urls.update({html_url, pdf_url})
        self._record_url(
            pdf_url,
            source=f"{tool_call.tool_name.casefold()}_planner_visible",
            page_url=html_url,
        )
        self._record_url(
            html_url,
            source=f"{tool_call.tool_name.casefold()}_planner_visible",
            page_url=html_url,
        )
        topic = self._hybrid_topic_key(tool_call)
        if topic:
            self._hybrid_successful_topics.add(topic)

    def _reconcile_hybrid_version_cross_check(self) -> None:
        if (
            not self._hybrid_direct_evidence_verified
            or not self._release_landscape_search_completed
        ):
            return
        if self._version_frontier_key <= self._hybrid_candidate_version_key:
            self._version_frontier_resolved = True

    def _update_hybrid_missing_progress(self) -> None:
        if not self._hybrid_direct_evidence_verified or not self._latest_task:
            return
        missing = self._latest_missing_prerequisites()
        if not missing:
            self._hybrid_missing_signature = ()
            self._hybrid_missing_repeat_count = 0
            return
        if missing == self._hybrid_missing_signature:
            self._hybrid_missing_repeat_count += 1
        else:
            self._hybrid_missing_signature = missing
            self._hybrid_missing_repeat_count = 1
        if self._hybrid_missing_repeat_count >= self._evidence_repeat_limit:
            self._hybrid_cross_check_exhausted = True
            self._hybrid_missing_signature = ()

    def _hybrid_topic_key(self, tool_call: ToolCall) -> str:
        value = tool_call.parameters.get("subject") or tool_call.parameters.get("query")
        if not isinstance(value, str):
            return ""
        normalized = " ".join(value.casefold().split())
        token_match = re.search(r"[a-z][a-z0-9._-]*", normalized)
        token = token_match.group() if token_match is not None else normalized.split(" ", 1)[0]
        family_match = re.match(r"[a-z]+", token)
        family = family_match.group() if family_match is not None else token
        owner_value = tool_call.parameters.get("official_owner") or tool_call.parameters.get(
            "owner"
        )
        owner = owner_value.strip().casefold() if isinstance(owner_value, str) else ""
        return f"{owner}:{family}" if family else ""

    def _hybrid_next_action(self, missing: tuple[str, ...]) -> dict[str, Any] | None:
        if not self._hybrid_direct_evidence_verified:
            return None
        if not missing and self._hybrid_candidate_url and not self._downloaded_paths:
            return {"tool": "download_pdf", "parameters": {"url": self._hybrid_candidate_url}}
        if any("release landscape" in item for item in missing):
            subject = self._hybrid_subject or next(iter(sorted(self._task_keywords)), "subject")
            return {
                "tool": "search",
                "parameters": {
                    "query": f"{subject} model version release lineup {self._current_year}",
                    "recency": "year",
                },
            }
        if not self._version_frontier_resolved and self._version_frontier:
            return {
                "tool": "search",
                "parameters": {
                    "query": f"{self._version_frontier} official technical report",
                    "recency": "year",
                },
            }
        return None

    def _authorize_hybrid_discovery(self, tool_call: ToolCall) -> PolicyDecision | None:
        name = tool_call.tool_name.casefold()
        if name == "official_report_search":
            topic = self._hybrid_topic_key(tool_call)
            attempts = self._hybrid_official_report_attempts.get(topic, 0)
            if (
                topic in self._hybrid_successful_topics
                or attempts >= self._official_report_max_attempts
            ):
                action = self._hybrid_next_action(self._latest_missing_prerequisites())
                return self._deny_hybrid_progress(
                    name,
                    "official_report_search is already exhausted for this subject family",
                    action,
                )
            self._hybrid_official_report_attempts[topic] = attempts + 1

        missing = self._latest_missing_prerequisites() if self._latest_task else ()
        if self._hybrid_direct_evidence_verified and not missing and not self._downloaded_paths:
            return self._deny_hybrid_progress(
                name,
                "Hybrid evidence is complete; additional discovery is not allowed",
                self._hybrid_next_action(()),
            )
        if self._hybrid_direct_evidence_verified and self._hybrid_missing_repeat_count >= max(
            1, self._evidence_repeat_limit - 1
        ):
            action = self._hybrid_next_action(missing)
            if action is not None and not self._tool_call_matches_action(tool_call, action):
                return self._deny_hybrid_progress(
                    name,
                    "the same Hybrid evidence gap repeated; execute the exact bounded "
                    "corroboration action",
                    action,
                )
        return None

    def _deny_hybrid_progress(
        self, target: str, reason: str, action: dict[str, Any] | None
    ) -> PolicyDecision:
        suffix = (
            f"; required next action: {json.dumps(action, ensure_ascii=False)}" if action else ""
        )
        return PolicyDecision(
            False,
            reason + suffix,
            self._step,
            target=target,
            provenance={"required_next_action": action} if action else {},
        )

    @staticmethod
    def _tool_call_matches_action(tool_call: ToolCall, action: dict[str, Any]) -> bool:
        return tool_call.tool_name.casefold() == action.get(
            "tool"
        ) and tool_call.parameters == action.get("parameters")

    def _latest_missing_prerequisites(self, target_url: Any = None) -> tuple[str, ...]:
        if not self._hybrid_mode or not self._hybrid_direct_evidence_verified:
            return super()._latest_missing_prerequisites(target_url)
        if self._hybrid_cross_check_exhausted:
            return ()
        missing: list[str] = []
        if not self._release_landscape_search_completed:
            missing.append(
                "a subject-wide current-year release landscape search whose results show "
                "relevant model/version/release evidence"
            )
        if not self._version_frontier_resolved:
            missing.append(
                f"an exact version follow-up search for the highest observed version "
                f"{self._version_frontier!r}"
            )
        return tuple(missing)

    async def authorize(self, tool_call: ToolCall) -> PolicyDecision:
        self._step += 1
        name = tool_call.tool_name.casefold()
        if name not in self.allowed_tools:
            return self._deny(name, "tool is excluded from browser-grounded mode")
        if name == "download_file" and (filename := tool_call.parameters.get("filename")):
            normalized = Path(str(filename)).name
            if not normalized or normalized.casefold() not in self._task.casefold():
                return self._deny(
                    name,
                    "omit filename to preserve the browser-suggested name unless the task "
                    "explicitly requests that exact filename",
                )
        if self._discovery_required and not self._search_completed and name != "search":
            return self._deny(name, "discovery task must begin with browser search")
        if self._hybrid_mode and name in _HYBRID_DISCOVERY_TOOLS:
            hybrid_decision = self._authorize_hybrid_discovery(tool_call)
            if hybrid_decision is not None:
                return hybrid_decision
        if name == "search":
            return PolicyDecision(True, "browser search provides grounded discovery", self._step)

        self._record_current_page_url()
        if (
            (self._discovery_required or self._hybrid_mode)
            and self._latest_task
            and name in {"done", "download_pdf"}
        ):
            target_url = tool_call.parameters.get("url") if name == "download_pdf" else None
            missing = self._latest_missing_prerequisites(target_url)
            if missing:
                if self._hybrid_mode and self._hybrid_direct_evidence_verified:
                    return self._deny_hybrid_progress(
                        name,
                        self._format_missing_latest(missing),
                        self._hybrid_next_action(missing),
                    )
                return self._deny_missing_latest(name, missing)

        if name == "done":
            return self._authorize_done(tool_call)
        if name in {"goto", "download_pdf"} or (
            name == "open_tab" and tool_call.parameters.get("url")
        ):
            return self._authorize_url(name, tool_call.parameters.get("url"))
        if name.startswith("pdf_"):
            return self._authorize_pdf_path(name, tool_call.parameters.get("path"))
        return PolicyDecision(
            True,
            "browser interaction is grounded in the current page or local workspace",
            self._step,
        )


__all__ = [
    "BrowserGroundedPolicy",
    "PolicyDecision",
    "SearchEngineOnlyPolicy",
    "ToolExecutionPolicy",
]

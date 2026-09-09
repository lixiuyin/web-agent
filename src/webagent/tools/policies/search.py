"""Browser-search-only authorization and discovery evidence state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from webagent.browser.context_projection import observation_evidence_text
from webagent.core.models import ToolCall, ToolResult
from webagent.tools.policies.candidates import CandidateLedger, normalize_name
from webagent.tools.policies.contracts import (
    PLANNER_VISIBLE_URL_PROVENANCE_SOURCES,
    PageProvider,
    PolicyDecision,
)
from webagent.tools.policies.discovery import DiscoveryEvidence
from webagent.tools.policies.evidence import (
    _BROWSER_TOOLS,
    _DOWNLOAD_TOOLS,
    _LATEST_EVIDENCE_GUIDANCE,
    _MAX_LABELS_PER_URL,
    _PDF_TOOLS,
    _RELEASE_LANDSCAPE_TERMS,
    _SEARCH_ENGINE_HOST_MARKERS,
    _SEARCH_INDEX_TERMS,
    _canonical_url,
    _checkpoint_bool,
    _checkpoint_evidence_map,
    _checkpoint_label_map,
    _checkpoint_non_negative_int,
    _checkpoint_policy_evidence,
    _checkpoint_policy_evidence_map,
    _checkpoint_policy_url,
    _checkpoint_str_set,
    _decode_visible_result,
    _distinctive_task_keywords,
    _is_pdf_artifact,
    _iter_labeled_urls,
    _looks_like_pdf_resource,
    _query_precisely_targets_url,
    _repository_identity,
    _site_scope,
    _SiteScope,
    _visible_result_urls,
    _visible_text_urls,
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
        "or links on pages you visited. Once a relevant official candidate is visible, inspect "
        "structured results and visit that exact primary source before more query rewrites or "
        "completion. "
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
        "then establish independent identity-bound scope evidence. This may be either a successful "
        "scope search or an exact repository link enumerated from the endorsed owner's rendered "
        "repository index. Repository searches may "
        "use the owner path (site:github.com/Owner), the repository host plus the exact endorsed "
        "owner token (site:github.com Owner), or a plain query containing both the repository "
        "host and exact owner (GitHub Owner). The returned URL must be under that same owner; a "
        "bare host query is never sufficient. For latest/newest tasks, that scope query must use "
        "the subject (a version-qualified subject is acceptable). Recency is established "
        "separately by the required current-year landscape searches. "
        "The result must actually match the scope; paper indexes do not count. "
        "If a PDF URL opens an HTML preview, navigate to that page and call "
        "inspect_download_links; download_pdf never discovers hidden retry URLs itself. "
        "When inspect_download_links reports download_controls but no URL candidates, call "
        "download_file with one of those selectors; the saved PDF is then usable by pdf_ tools. "
        "If all search engines fail, report the failure honestly."
    )

    def __init__(self, browser: PageProvider, *, artifacts_dir: Path) -> None:
        self._browser = browser
        self._artifacts_dir = artifacts_dir.resolve()
        self._discovery = DiscoveryEvidence(self)
        self.reset("")

    def reset(self, task: str) -> None:
        """Clear evidence between tasks and derive task-specific rigor requirements."""
        self._task = task
        self._candidate_ledger = CandidateLedger()
        self._current_page_has_pdf = False
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
        self._observed_labels: dict[str, list[str]] = {}
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
        self._report_discovery_attempts = 0
        self._pending_url_evidence_target: str | None = None
        self._pending_url_search_attempts = 0
        self._denied_calls: dict[str, str] = {}
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
            "candidate_ledger": self._candidate_ledger.export(),
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
            "observed_labels": {
                _checkpoint_policy_url(url): list(labels)
                for url, labels in self._observed_labels.items()
            },
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
            "report_discovery_attempts": self._report_discovery_attempts,
            "figure_analysis_completed": self._figure_analysis_completed,
            "pending_url_evidence_target": self._pending_url_evidence_target,
            "pending_url_search_attempts": self._pending_url_search_attempts,
        }

    def planner_evidence_hint(self) -> str:
        hint = self._navigation_evidence_hint()
        if self._latest_task and self._requires_pdf_artifact:
            evidence = self._candidate_assessment()
            if priority := self._candidate_progress_hint():
                return priority + "\n" + hint
            if evidence["partial_completion_allowed"]:
                hint += "\nBOUNDED RESEARCH STOP: The dated official PDF is verified, but sibling release mentions remain unverified after follow-up searches. They are not proven newer PDFs. Complete the requested PDF/figure analysis, then use done with the useful findings and explicitly state that latestness remains unconfirmed. The framework will preserve this as BLOCKED / partial findings, not a successful latest-report result. Do not repeat equivalent searches indefinitely.\n"
            if variant := self._candidate_ledger.next_unsearched_variant():
                hint += f'\nNEXT UNRESOLVED RELEASE VARIANT: {variant}. Search "{variant}" official technical report {self._current_year}, or open its observed source. Do not substitute the base model: this distinct variant has not been investigated.\n'
            if report_source := self._candidate_ledger.next_report_source(
                minimum_version=self._version_frontier_key
            ):
                return self._report_source_hint(report_source) + "\n" + hint
            # Once recovery has reached an exact-version official repository,
            # the rendered page can expose the report before the candidate
            # ledger knows its URL. Inspect that page instead of sending the
            # planner back through the search/repository recovery loop.
            if self._current_page_exposes_frontier_report():
                return self._release_navigation_hint() + "\n" + hint
            if self._version_frontier:
                report_hint = (
                    self._report_discovery_gap_hint()
                    if self._report_discovery_attempts < 2
                    else self._report_discovery_recovery_hint()
                )
                return report_hint + "\n" + hint
            hint += self._release_navigation_hint()
            hint += (
                "\nCANDIDATE COMPARISON (search activity is not latestness proof): "
                + json.dumps(evidence, ensure_ascii=False)
            )
        return hint

    @staticmethod
    def _report_source_hint(source: dict[str, str]) -> str:
        return (
            "PRIORITY REPORT SOURCE: open the already observed highest-version report candidate "
            f"{source['url']} with goto before exploring generic model cards or publisher navigation. "
            "Then call inspect_download_links on that page. This is a candidate lead, not proof of "
            "official provenance or latestness: keep the normal identity, scope, exact-version, and "
            f"date checks. Candidate: {source['name']} ({source['source']})."
        )

    def _report_discovery_gap_hint(self) -> str:
        return (
            "PRIORITY REPORT DISCOVERY GAP: the highest observed subject release is "
            f"{self._version_frontier}, but no report-shaped source for that version has been "
            "observed. Search for the unquoted version name plus 'technical report PDF' without "
            "an arXiv, paper-index, or site restriction. Do not inspect an older report or revisit "
            "a generic model card until this focused search has returned a report candidate. "
            f"Recommended query: {self._version_frontier} technical report PDF."
        )

    def _report_discovery_recovery_hint(self) -> str:
        routes = sorted(self._official_identity_urls)[:8]
        owners = sorted(
            {
                identity[1]
                for url in self._official_identity_urls
                if (identity := _repository_identity(url)) is not None
            }
        )
        return (
            "BOUNDED REPORT DISCOVERY RECOVERY: two focused highest-version PDF searches did "
            "not expose a usable report candidate. Do not repeat that query or trust residual "
            "results from a failed search page. Open an already observed official website or "
            "repository, enumerate its release/news links, and for a repository click the visible "
            "owner breadcrumb using a text selector (do not wait for get_all_links to expose its "
            "href). On the organization page, first click the visible Repositories tab, then use "
            "get_all_links with the highest version token to find same-version report repositories. "
            "Do not filter the organization overview for generic 'report' links. Older reports "
            "remain comparison evidence, "
            "not the latest answer. Observed owner labels: "
            + json.dumps(owners)
            + ". Observed official routes: "
            + json.dumps(routes)
        )

    def _current_page_exposes_frontier_report(self) -> bool:
        if not self._current_page_has_pdf or not self._version_frontier:
            return False
        current = _canonical_url(self._browser.page.url)
        return bool(
            current
            and normalize_name(self._version_frontier) in normalize_name(current)
            and self._identity_endorses_target(current)
        )

    def _candidate_progress_hint(self) -> str:
        candidate = self._selected_candidate_url
        if not candidate or not self._latest_task or not self._requires_pdf_artifact:
            return ""
        selected = self._candidate_assessment()["selected"]
        if not selected or not selected.get("file_inspected"):
            return (
                f"PRIORITY: inspect the observed report file before any more searches. Open {candidate} "
                "with goto, then call inspect_download_links on its own preview to bind a report-file date. This read-only "
                "inspection does not require completing scope searches first. Do not revisit the repository listing."
            )
        if not self._downloaded_paths:
            missing = self._latest_missing_prerequisites(candidate)
            if missing:
                if any("release landscape" in item for item in missing):
                    return self._release_landscape_query_hint()
                return (
                    f"PRIORITY: complete only the download prerequisites for {candidate}: "
                    + "; ".join(missing)
                )
            return f"PRIORITY: download the inspected report {candidate} using its observed raw URL or download control before researching sibling release mentions."
        if self._requires_figure_analysis and not self._figure_analysis_completed:
            return (
                "PRIORITY: analyze the requested figure in the downloaded PDF with pdf_analyze_figure before more release searches. Downloaded paths: "
                + json.dumps([str(path) for path in self._downloaded_paths])
            )
        assessment = self._candidate_assessment()
        if not assessment["missing"] and not self._latest_missing_prerequisites(candidate):
            return (
                "DELIVERABLES READY: use done now. Bound latestness to the observed official candidates. "
                "Cite the selected file's exact ISO date and date kind, not dates of other repository files: "
                + json.dumps(selected.get("dates", []))
                + ". Preserve the figure analysis's distinction between visible labels, caption statements, "
                "and inference; adjacent components do not establish a serial arrow chain."
            )
        return ""

    def _release_landscape_query_hint(self) -> str:
        subject = min(self._task_keywords, key=lambda value: (len(value), value), default="subject")
        family_match = re.match(r"[a-z]+", subject.casefold())
        family = family_match.group() if family_match is not None else subject
        query = f"{family} model version release lineup {self._current_year}"
        return (
            "REQUIRED RELEASE-LANDSCAPE SEARCH: use a subject-wide current-year query without "
            "any dotted or named model version. A query containing a candidate such as 3.8, "
            "Flash-Next, Max, or a model size cannot satisfy this prerequisite. Execute search "
            f"with query={query!r} and recency='year', then inspect its results."
        )

    def _release_navigation_hint(self) -> str:
        if self._selected_candidate_url:
            return ""
        if self._current_page_has_pdf:
            return "\nCURRENT PAGE EXPOSES A PDF/REPORT: call inspect_download_links on this page before leaving it for another search or a base-model repository. Inspect the observed artifact first; then compare its date with other candidates."
        routes = [url for url in self._observed_urls if self._is_release_navigation(url)]
        if not routes:
            return ""
        if self._browser.page.url in routes:
            return "\nCURRENT PAGE IS A PUBLISHER NAVIGATION PAGE: Before leaving for another search, call get_all_links(contains='blog') (or inspect its News/Models navigation controls) to expose the release index."
        return (
            "\nRELEASE DISCOVERY ROUTE: No report selected. A model repository/card without a PDF is not the whole release landscape. "
            "Open one of these already observed publisher navigation pages, inspect its Blog/News/Models links with get_all_links, "
            "and enumerate recent release variants (not only the current base-model card). Do not keep searching/filtering report/pdf/arxiv synonyms on the same page. "
            "Publisher identity still requires the normal evidence checks. Observed routes: "
            + json.dumps(routes[:8])
        )

    def _is_release_navigation(self, url: str) -> bool:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.path.rstrip("/") not in {"", "/home", "/blog", "/blogs", "/news", "/releases"}:
            return False
        return self._identity_endorses_target(url) or any(
            host.removeprefix("www.").startswith(keyword + ".") for keyword in self._task_keywords
        )

    def _navigation_evidence_hint(self) -> str:
        """Give the planner a deterministic recovery action for a guessed URL."""
        target = self._pending_url_evidence_target
        if target is None:
            return self._candidate_evidence_hint()
        if self._pending_url_search_attempts >= 2:
            return (
                "EXACT URL EVIDENCE UNRESOLVED: "
                f"{target} was guessed and two precise searches did not expose it. Do not search "
                "for or navigate to that URL again. Use links, tabs, or site navigation already "
                "visible on an observed official page to reach the needed page, or finish without "
                "mentioning the unverified URL."
            )
        return (
            "EXACT URL EVIDENCE REQUIRED: "
            f"{target} was guessed but has not appeared in visible browser evidence. The next "
            "search must query the exact quoted URL (or use an exact site:host/path query); do not "
            "issue a broad topical rewrite or retry goto. Grounded clicks, tabs, and links on an "
            "already observed page are also allowed."
        )

    def _candidate_evidence_hint(self) -> str:
        """Surface the still-missing binding evidence for the selected candidate.

        Denials already list the checklist, but a planner that changes tactics
        between steps loses that text. Repeating the current gap for the selected
        candidate on every step keeps the recovery route visible.
        """
        candidate = self._selected_candidate_url
        if not self._latest_task or candidate is None:
            return ""
        if not (self._official_identity_search_completed and self._official_scope_search_completed):
            return ""
        missing = self._latest_missing_prerequisites(candidate)
        if not missing:
            return ""
        return f"CANDIDATE EVIDENCE INCOMPLETE for {candidate}: " + "; ".join(missing)

    @staticmethod
    def _call_signature(tool_call: ToolCall) -> str:
        return json.dumps(
            [tool_call.tool_name.casefold(), tool_call.parameters],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )

    def _preflight_repeated_denial(self, tool_call: ToolCall) -> str | None:
        """Reject an exact repeat of a denied call while the evidence is unchanged.

        Every allowed action clears the record, so the planner is only stopped
        from re-issuing a call whose outcome is already known.
        """
        reason = self._denied_calls.get(self._call_signature(tool_call))
        if reason is None:
            return None
        return (
            "this exact call was already denied and no evidence has changed since; "
            "perform a different evidence-gathering action first. Denial reason: " + reason
        )

    def _report_discovery_gap_active(self) -> bool:
        return bool(
            self._latest_task
            and self._requires_pdf_artifact
            and not self._selected_candidate_url
            and self._version_frontier
            and self._report_discovery_attempts < 2
            and self._candidate_ledger.next_report_source(
                minimum_version=self._version_frontier_key
            )
            is None
        )

    def validate_planner_call(self, tool_call: ToolCall) -> str | None:
        """Preflight recoverable evidence mistakes before they consume an action step."""
        name = tool_call.tool_name.casefold()
        repeated = self._preflight_repeated_denial(tool_call)
        if repeated is not None:
            return repeated
        if name in {"goto", "open_tab"} and self._report_discovery_gap_active():
            return self._report_discovery_gap_hint()
        if name == "search" and (search_error := self._planner_search_error(tool_call)):
            return search_error
        if name == "done":
            unobserved = self._completion_unobserved_urls(tool_call)
            if unobserved:
                return (
                    "done summary contains URL(s) absent from browser evidence: "
                    + ", ".join(unobserved[:3])
                    + "; remove every such URL and cite only the final allowlist"
                )
            return self._completion_date_error(tool_call)
        return None

    def _planner_search_error(self, tool_call: ToolCall) -> str | None:
        if self._invalid_release_landscape_query(tool_call):
            return self._release_landscape_query_hint()
        if self._latest_task and self._requires_pdf_artifact and self._selected_candidate_url:
            selected = self._candidate_assessment()["selected"]
            if selected and not selected.get("file_inspected"):
                return self._candidate_progress_hint()
        target = self._pending_url_evidence_target
        if target is None:
            return None
        if self._pending_url_search_attempts >= 2:
            return (
                "two exact searches already failed to expose the guessed URL; use visible "
                "links, tabs, or site navigation instead"
            )
        if not _query_precisely_targets_url(tool_call.parameters.get("query"), target):
            return f"search for the exact quoted URL {target!r}, not a broad topical rewrite"
        return None

    def _invalid_release_landscape_query(self, tool_call: ToolCall) -> bool:
        if not self._latest_task or self._release_landscape_search_completed:
            return False
        query = tool_call.parameters.get("query")
        if not isinstance(query, str):
            return False
        normalized = " ".join(query.casefold().split())
        current_year = (
            str(self._current_year) in normalized or tool_call.parameters.get("recency") == "year"
        )
        return bool(
            current_year
            and self._query_matches_task(normalized)
            and any(term in normalized for term in _RELEASE_LANDSCAPE_TERMS)
            and self._query_has_subject_version(normalized)
        )

    def _completion_date_error(self, tool_call: ToolCall) -> str | None:
        if not self._latest_task or not self._requires_pdf_artifact:
            return None
        selected = self._candidate_assessment()["selected"] or {}
        dates = selected.get("dates", [])
        summary = str(tool_call.parameters.get("summary", ""))
        if dates and not any(item["value"] in summary for item in dates):
            return (
                "Include the selected report file's exact ISO date and explain its date kind: "
                + json.dumps(dates)
                + ". Do not merge it with dates of other repository files."
            )
        return None

    def terminal_evidence_hint(self) -> str:
        """Expose a bounded exact-URL allowlist for the final planner action."""
        current = _canonical_url(self._browser.page.url)
        content_visits = {
            url
            for url in self._visited_urls
            if not any(
                marker in (urlsplit(url).hostname or "") for marker in _SEARCH_ENGINE_HOST_MARKERS
            )
        }
        ordered = ([current] if current in content_visits else []) + sorted(
            content_visits - ({current} if current is not None else set())
        )
        if not ordered:
            return ""
        return (
            "FINAL CITATION ALLOWLIST — visited non-search pages: "
            + ", ".join(ordered[:12])
            + ". Cite the primary source using exactly one URL from this list. Omit every "
            "canonical, stable, alternate-version, or supporting URL that is not explicitly "
            "present in prior visible evidence; do not reconstruct URL variants from memory."
        )

    def import_state(self, state: dict[str, Any], *, task: str) -> None:
        """Restore a checkpoint produced by :meth:`export_state`, failing closed."""
        if state.get("schema_version") != 3 or state.get("policy") != self.name:
            raise ValueError("checkpoint policy schema/name mismatch")
        if state.get("task_sha256") != hashlib.sha256(task.encode("utf-8")).hexdigest():
            raise ValueError("checkpoint policy task mismatch")
        if state.get("current_year") != datetime.now(UTC).year:
            raise ValueError("checkpoint policy evidence is from a different calendar year")

        self.reset(task)
        if "candidate_ledger" in state:
            self._candidate_ledger.restore(state["candidate_ledger"])
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
        self._observed_labels = _checkpoint_label_map(state, "observed_labels")
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
        report_attempts = state.get("report_discovery_attempts", 0)
        if (
            not isinstance(report_attempts, int)
            or isinstance(report_attempts, bool)
            or report_attempts < 0
        ):
            raise ValueError("checkpoint field report_discovery_attempts is invalid")
        self._report_discovery_attempts = report_attempts
        self._figure_analysis_completed = state.get("figure_analysis_completed") is True
        pending = state.get("pending_url_evidence_target")
        self._pending_url_evidence_target = pending if isinstance(pending, str) else None
        attempts = state.get("pending_url_search_attempts", 0)
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            raise ValueError("checkpoint field pending_url_search_attempts is invalid")
        self._pending_url_search_attempts = attempts

    async def authorize(self, tool_call: ToolCall) -> PolicyDecision:
        self._step += 1
        name = tool_call.tool_name.casefold()
        if name not in self.allowed_tools:
            return self._deny(name, "tool is excluded from search-engine-only evaluation")
        filename_denial = self._authorize_download_filename(tool_call)
        if filename_denial is not None:
            return filename_denial
        if not self._search_completed and name != "search":
            return self._deny(name, "first successful action must be browser search")
        if name == "search":
            recovery = self._authorize_exact_url_recovery_search(tool_call)
            if recovery is not None:
                return recovery
            return PolicyDecision(
                True, "browser search is the required discovery action", self._step
            )

        # Capture the current page before evaluating latest-report gates. A
        # click may have navigated to a PDF preview since the preceding tool
        # result, and that page is legitimate browser-grounded evidence.
        self._record_current_page_url()
        if self._latest_task and self._requires_pdf_artifact and name == "done":
            assessment = self._candidate_assessment()
            candidate_missing = assessment["missing"]
            if candidate_missing and not assessment["partial_completion_allowed"]:
                return self._deny_missing_latest(name, tuple(candidate_missing))
        if self._latest_task and (name == "done" or name in _DOWNLOAD_TOOLS):
            missing = self._latest_missing_prerequisites(self._latest_target_url(tool_call))
            if missing:
                return self._deny_missing_latest(name, missing)

        return self._authorize_grounded_action(tool_call)

    def _latest_target_url(self, tool_call: ToolCall) -> Any:
        """Return the URL a download binds to for owner/host evidence checks.

        ``download_pdf`` names its target. ``download_file`` clicks a control on
        the current page, so that page is the artifact's provenance. ``done`` has
        no target and falls back to the selected candidate.
        """
        name = tool_call.tool_name.casefold()
        if name == "download_pdf":
            return tool_call.parameters.get("url")
        if name == "download_file":
            return _canonical_url(self._browser.page.url)
        return None

    def _authorize_grounded_action(self, tool_call: ToolCall) -> PolicyDecision:
        name = tool_call.tool_name
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

    def _authorize_download_filename(self, tool_call: ToolCall) -> PolicyDecision | None:
        name = tool_call.tool_name
        if name == "download_file" and (filename := tool_call.parameters.get("filename")):
            normalized = Path(str(filename)).name
            if not normalized or normalized.casefold() not in self._task.casefold():
                return self._deny(
                    name,
                    "omit filename to preserve the browser-suggested name unless the task "
                    "explicitly requests that exact filename",
                )
        return None

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
            self._denied_calls[self._call_signature(tool_call)] = decision.reason
            return audit
        # Any executed action may change the evidence, so earlier denials no
        # longer predict the outcome of the same call.
        self._denied_calls.clear()

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
        self._update_exact_url_recovery(name, tool_call, result)
        self._record_candidate_evidence(name, tool_call, visible_data, result.success)
        self._record_report_discovery_attempt(name, tool_call)
        if name in {"goto", "open_tab"} and result.success:
            self._record_navigated_candidate(tool_call, visible_data)
            self._record_organization_scope(visible_data, decision)
        if name == "inspect_download_links" and result.success:
            self._record_selected_candidate(visible_data)
        if name in _DOWNLOAD_TOOLS and result.success:
            self._record_download(name, tool_call, result)
        if name == "pdf_analyze_figure" and result.success:
            self._figure_analysis_completed = self._figure_analysis_completed or bool(
                visible_data.get("found") is True
                and visible_data.get("vision_analysis")
                and visible_data.get("vision_metadata", {}).get("finish_reason") != "length"
            )
        if result.success:
            self._record_current_page_visit()

        latest_missing = self._latest_missing_prerequisites() if self._latest_task else ()
        if self._latest_task and self._requires_pdf_artifact:
            latest_missing += tuple(self._candidate_assessment()["missing"])

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
                "report_discovery_attempts": self._report_discovery_attempts,
                "latest_evidence_complete": self._latest_task and not latest_missing,
                "latest_missing_prerequisites": list(latest_missing),
                "candidate_ledger": self._candidate_assessment(),
                "new_urls_observed": len(self._observed_urls) - before_count,
                "observed_url_count": len(self._observed_urls),
                "downloaded_artifact_count": len(self._downloaded_paths),
                "figure_analysis_completed": self._figure_analysis_completed,
            }
        )
        return audit

    def _record_report_discovery_attempt(self, name: str, tool_call: ToolCall) -> None:
        if name != "search" or not self._is_report_discovery_query(
            tool_call.parameters.get("query")
        ):
            return
        if (
            self._candidate_ledger.next_report_source(minimum_version=self._version_frontier_key)
            is None
        ):
            self._report_discovery_attempts += 1

    def _is_report_discovery_query(self, query: Any) -> bool:
        if not isinstance(query, str) or not self._version_frontier:
            return False
        normalized = normalize_name(query)
        return bool(
            normalize_name(self._version_frontier) in normalized
            and "report" in normalized
            and re.search(r"\bpdf\b", normalized)
            and "arxiv" not in normalized
            and "site:" not in normalized
        )

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
        missing.extend(self._latest_target_prerequisites(target_url))
        if not self._version_frontier_resolved:
            missing.append(
                f"an exact version follow-up search for the highest observed version "
                f"{self._version_frontier!r}"
            )
        return tuple(missing)

    def _candidate_assessment(self) -> dict[str, Any]:
        return self._candidate_ledger.assessment(self._selected_candidate_url, self._task_keywords)

    def record_observation(self, state: Any) -> None:
        # A complete observation is evidence even before the first tool action.
        # Bind it to the captured URL, not the possibly changed live browser URL.
        current = _canonical_url(state.url)
        if state.observation_metadata.get("status") == "complete" and current is not None:
            self._record_url(current, source="planner_state_current_url", page_url=state.url)
            self._visited_urls.add(current)
            for url in _visible_text_urls(observation_evidence_text(state)):
                self._record_url(url, source="planner_observation_text", page_url=state.url)
        text = observation_evidence_text(state)
        self._current_page_has_pdf = ".pdf" in text.casefold()
        self._candidate_ledger.page(
            state.url,
            text,
            official=self._identity_endorses_target(state.url),
            keywords=self._task_keywords,
        )
        self._refresh_version_frontier()

    def _refresh_version_frontier(self) -> None:
        frontier = self._version_frontier or ""
        if any(
            lead.get("status") == "official_checked"
            and (name == frontier or name.startswith(frontier + "-"))
            for name, lead in self._candidate_ledger.leads.items()
        ):
            self._version_frontier_resolved = True

    def _record_candidate_evidence(
        self, name: str, call: ToolCall, data: dict[str, Any], success: bool
    ) -> None:
        if name == "search":
            self._candidate_ledger.query_attempt(str(call.parameters.get("query", "")))
        if name == "inspect_download_links":
            self._candidate_ledger.inspection_attempt(str(data.get("source_url", "")), success)
        if not success:
            return
        if name in {"search", "get_search_results"}:
            query = (
                str(call.parameters.get("query", ""))
                if name == "search"
                else str(data.get("query", ""))
            )
            self._candidate_ledger.search(query, data, self._task_keywords)
        if name in {"goto", "open_tab"}:
            url = str(data.get("url", ""))
            self._candidate_ledger.page(
                url,
                str(data.get("title", "")),
                official=self._identity_endorses_target(url),
                keywords=self._task_keywords,
            )
            self._refresh_version_frontier()
        if name == "inspect_download_links":
            self._candidate_ledger.inspect(
                data, official=self._identity_endorses_target(str(data.get("source_url", "")))
            )

    def _latest_target_prerequisites(self, target_url: Any) -> list[str]:
        """Return identity/scope items, with a concrete route for arXiv candidates."""
        has_target = isinstance(target_url, str)
        identity_endorsed = not has_target or self._identity_endorses_target(target_url)
        scope_covered = not has_target or self._scope_covers_target_url(target_url)
        subject_guidance = (
            self._discovery._document_subject_guidance(target_url) if has_target else None
        )
        subject_items = [subject_guidance] if subject_guidance is not None else []
        if (
            has_target
            and not (identity_endorsed and scope_covered)
            and self._official_identity_search_completed
            and self._official_scope_search_completed
        ):
            arxiv_guidance = self._discovery._arxiv_link_guidance(target_url)
            if arxiv_guidance is not None:
                return [arxiv_guidance, *subject_items]
        missing: list[str] = []
        if not self._official_identity_search_completed:
            missing.append("a non-site official identity search for the subject")
        elif not identity_endorsed:
            missing.append(
                "a non-site official identity search whose results endorse the selected "
                f"target host/owner ({self._target_identity_label(target_url)})"
            )
        if not self._official_scope_search_completed:
            missing.append(
                "an independent scope search whose query contains the endorsed host/owner and "
                "selected candidate name with results that cover it, or an exact candidate "
                "repository link enumerated from that endorsed owner's repository index "
                "(paper indexes do not count)"
            )
        elif not scope_covered:
            missing.append(
                "an identity-bound scope result corroborating the selected candidate repository "
                "or host"
            )
        return [*missing, *subject_items]

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
        if site_scope is None and self._query_matches_task(normalized):
            self._official_identity_urls.update(self._identity_result_urls(normalized, data))
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

    def _record_download(self, tool_name: str, tool_call: ToolCall, result: ToolResult) -> None:
        """Register a saved artifact so pdf_ tools may consume it.

        ``download_pdf`` already authorized its URL against observed evidence.
        ``download_file`` clicks a control on the current page, so the page URL
        is the provenance; only PDF payloads are registered because the task
        deliverable and the pdf_ tools both require a PDF.
        """
        path_value = result.data.get("path")
        if not isinstance(path_value, str):
            return
        path = self._resolve_artifact_path(path_value)
        if path is None:
            return
        if tool_name == "download_file":
            if not _is_pdf_artifact(path):
                return
            self._downloaded_paths[path] = {
                "source_tool": "download_file",
                "policy_step": self._step,
                "source_url": None,
                "page_url": self._browser.page.url,
                "selector": tool_call.parameters.get("selector"),
            }
            return
        source_url = tool_call.parameters.get("url")
        self._downloaded_paths[path] = {
            "source_tool": "download_pdf",
            "policy_step": self._step,
            "source_url": source_url,
        }
        # The downloaded document is the deliverable, so later completion gates
        # bind to it rather than to an earlier, abandoned candidate.
        canonical = _canonical_url(source_url) if isinstance(source_url, str) else None
        if canonical is not None:
            self._selected_candidate_url = canonical

    def _record_navigated_candidate(self, tool_call: ToolCall, data: dict[str, Any]) -> None:
        """Bind the candidate when navigation lands on a document or its paper page.

        An arXiv ``/abs/`` page is the document's own identity page, so opening it
        selects that paper and lets the per-step evidence hint report what is
        still missing before the planner spends a download attempt.
        """
        values = (data.get("url"), tool_call.parameters.get("url"))
        for value in values:
            canonical = _canonical_url(value) if isinstance(value, str) else None
            if canonical is None:
                continue
            if _looks_like_pdf_resource(canonical) or self._discovery._is_arxiv_document(canonical):
                self._selected_candidate_url = canonical
                return

    def _record_organization_scope(self, data: dict[str, Any], decision: PolicyDecision) -> None:
        """Accept a visible official-org repository link as scope evidence.

        Search engines are one way to bind an endorsed owner to an exact
        repository, but the owner's rendered repository index is stronger
        first-party evidence and remains usable when external search fails.
        """
        raw_target = data.get("url")
        target = _canonical_url(raw_target) if isinstance(raw_target, str) else None
        provenance = decision.provenance
        if (
            target is None
            or provenance.get("source") != "get_all_links_planner_visible"
            or not self._official_identity_search_completed
            or not self._identity_endorses_target(target)
            or not self._version_frontier
            or normalize_name(self._version_frontier) not in normalize_name(target)
        ):
            return
        source = _canonical_url(str(provenance.get("page_url", "")))
        identity = _repository_identity(target)
        if source is None or identity is None:
            return
        source_parts = [part.casefold() for part in urlsplit(source).path.split("/") if part]
        host, owner, _ = identity
        if (urlsplit(source).hostname or "").casefold() == host and source_parts == [
            "orgs",
            owner,
            "repositories",
        ]:
            self._official_scope_search_completed = True
            self._official_scope_result_urls.add(target)

    def _record_selected_candidate(self, data: dict[str, Any]) -> None:
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            return
        ranked: list[tuple[int, str]] = []
        source_url = data.get("source_url")
        candidate_values: list[dict[str, Any]] = list(candidates)
        # A PDF preview page (repository blob, arXiv rendition) is itself the
        # candidate document; an ordinary HTML page that merely exposes a
        # download control is not, so it must not become the bound candidate.
        if isinstance(source_url, str) and _looks_like_pdf_resource(source_url):
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
            if self._pending_url_evidence_target != canonical:
                self._pending_url_search_attempts = 0
            self._pending_url_evidence_target = canonical
            return self._deny(
                canonical,
                "target URL was not observed in prior browser evidence; recover with a search "
                f"for the exact quoted URL {canonical!r}, or navigate through visible official "
                "links instead of guessing the path",
            )
        return PolicyDecision(
            True,
            "target URL is grounded in prior browser evidence",
            self._step,
            target=canonical,
            provenance=evidence,
        )

    def _authorize_exact_url_recovery_search(self, tool_call: ToolCall) -> PolicyDecision | None:
        target = self._pending_url_evidence_target
        if target is None:
            return None
        query = tool_call.parameters.get("query")
        if self._pending_url_search_attempts >= 2:
            return self._deny(
                "search",
                "two exact searches did not expose the guessed URL; stop repeating searches and "
                "use visible links/tabs/site navigation, or omit the unverified URL",
            )
        if not _query_precisely_targets_url(query, target):
            return self._deny(
                "search",
                f"pending URL evidence requires an exact quoted URL query for {target}; broad "
                "topical rewrites are not a valid evidence-recovery action",
            )
        return None

    def _update_exact_url_recovery(
        self, name: str, tool_call: ToolCall, result: ToolResult
    ) -> None:
        target = self._pending_url_evidence_target
        if target is None:
            return
        if target in self._observed_urls:
            self._pending_url_evidence_target = None
            self._pending_url_search_attempts = 0
            return
        if (
            name == "search"
            and result.success
            and _query_precisely_targets_url(tool_call.parameters.get("query"), target)
        ):
            self._pending_url_search_attempts += 1

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
            if _looks_like_pdf_resource(current):
                self._selected_candidate_url = current

    def _record_current_page_visit(self) -> None:
        current = _canonical_url(self._browser.page.url)
        if current is not None:
            self._record_url(current, source="visited_page", page_url=self._browser.page.url)
            self._visited_urls.add(current)
            if _looks_like_pdf_resource(current):
                self._selected_candidate_url = current

    def _authorize_done(self, tool_call: ToolCall) -> PolicyDecision:
        missing_deliverables: list[str] = []
        if self._requires_pdf_artifact and not self._downloaded_paths:
            missing_deliverables.append(
                "download the requested PDF with download_pdf on an observed URL or "
                "download_file on a visible download control"
            )
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
        unobserved = self._completion_unobserved_urls(tool_call)
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

    def _completion_unobserved_urls(self, tool_call: ToolCall) -> list[str]:
        summary = tool_call.parameters.get("summary")
        if not isinstance(summary, str):
            return []
        cited = {
            canonical
            for raw in re.findall(r"https?://[^\s<>\"']+", summary)
            if (canonical := _canonical_url(raw.rstrip(".,;:!?)]}>`*。，；：！？）】》")))
            is not None
        }
        return sorted(cited - set(self._observed_urls))

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
        """Register explicit URLs in exactly the JSON shown to the planner."""
        if source not in PLANNER_VISIBLE_URL_PROVENANCE_SOURCES:
            return
        if source == "frame_interact_planner_visible" and data.get("action") != "extract_text":
            return
        if source == "get_attribute_planner_visible" and data.get("attribute") not in {
            "href",
            "src",
            "action",
        }:
            return
        if source in {"search_planner_visible", "get_search_results_planner_visible"}:
            data = {"url": data.get("url"), "results": data.get("results", [])}
        page_url = str(data.get("source_url") or data.get("url") or self._browser.page.url)
        for url in _visible_result_urls(data, page_url):
            self._record_url(url, source=source, page_url=page_url)
        for url, label in _iter_labeled_urls(data):
            self._record_label(url, label, page_url=page_url)

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

    def _record_url(
        self, url: str, *, source: str, page_url: str, label: str | None = None
    ) -> None:
        canonical = _canonical_url(url, page_url)
        if canonical is None:
            return
        evidence = {
            "source": source,
            "policy_step": self._step,
            "page_url": page_url,
        }
        previous = self._observed_urls.get(canonical)
        if previous is None or self._observation_upgrades(previous, evidence, canonical):
            self._observed_urls[canonical] = evidence
        if label is not None:
            self._record_label(canonical, label, page_url=page_url)

    def _record_label(self, url: str, label: str, *, page_url: str) -> None:
        """Keep the visible text (title, anchor text, snippet) the planner saw for a URL.

        Labels describe what the document *is*, independent of which page exposed
        it, so they accumulate across observations and are never downgraded.
        """
        canonical = _canonical_url(url, page_url)
        if canonical is None or not label.strip():
            return
        labels = self._observed_labels.setdefault(canonical, [])
        if label not in labels and len(labels) < _MAX_LABELS_PER_URL:
            labels.append(label)

    @staticmethod
    def _observation_upgrades(
        previous: dict[str, Any], evidence: dict[str, Any], canonical: str
    ) -> bool:
        """Decide whether a new observation carries stronger provenance.

        A click can visit a page before its URL has appeared in a compact
        planner-visible result. A direct page-link enumeration also strengthens
        a search-result observation because it supplies the page context that
        endorsed the link. Among direct page observations, a link seen on a
        different site outranks one the page exposed about itself, because only
        third-party context can endorse the target.
        """
        source = evidence["source"]
        previous_source = previous.get("source")
        if source not in PLANNER_VISIBLE_URL_PROVENANCE_SOURCES:
            return False
        if previous_source not in PLANNER_VISIBLE_URL_PROVENANCE_SOURCES:
            return True
        direct_page_sources = {
            "get_all_links_planner_visible",
            "get_attribute_planner_visible",
            "inspect_download_links_planner_visible",
        }
        search_result_sources = {
            "search_planner_visible",
            "get_search_results_planner_visible",
        }
        if source not in direct_page_sources:
            return False
        if previous_source in search_result_sources:
            return True
        if previous_source not in direct_page_sources:
            return False
        target_host = urlsplit(canonical).hostname
        previous_page = _canonical_url(str(previous.get("page_url") or ""))
        new_page = _canonical_url(str(evidence.get("page_url") or ""))
        previous_self_hosted = (
            previous_page is not None and urlsplit(previous_page).hostname == target_host
        )
        new_cross_site = new_page is not None and urlsplit(new_page).hostname != target_host
        return previous_self_hosted and new_cross_site

    def _resolve_artifact_path(self, value: str) -> Path | None:
        path = Path(value)
        if not path.is_absolute():
            path = self._artifacts_dir / path
        resolved = path.resolve()
        output_root = self._artifacts_dir.parent
        if resolved != output_root and not resolved.is_relative_to(output_root):
            return None
        return resolved

    def _scope_was_endorsed(self, scope: _SiteScope) -> bool:
        return self._discovery._scope_was_endorsed(scope)

    def _identity_bound_site_result(
        self, scope: _SiteScope, query: str, data: dict[str, Any]
    ) -> bool:
        return self._discovery._identity_bound_site_result(scope, query, data)

    def _identity_bound_scope_result(
        self, site_scope: _SiteScope | None, query: str, data: dict[str, Any]
    ) -> bool:
        return self._discovery._identity_bound_scope_result(site_scope, query, data)

    def _query_matches_task(self, query: str) -> bool:
        return self._discovery._query_matches_task(query)

    def _query_has_subject_version(self, query: str) -> bool:
        return self._discovery._query_has_subject_version(query)

    def _official_scope_query_is_broad(self, query: str) -> bool:
        return self._discovery._official_scope_query_is_broad(query)

    def _identity_result_urls(self, query: str, data: dict[str, Any]) -> set[str]:
        return self._discovery._identity_result_urls(query, data)

    def _release_landscape_result_evidence(self, data: dict[str, Any]) -> set[str]:
        return self._discovery._release_landscape_result_evidence(data)

    def _scope_covers_target_url(self, target_url: str) -> bool:
        return self._discovery._scope_covers_target_url(target_url)

    def _identity_endorses_target(self, target_url: str) -> bool:
        return self._discovery._identity_endorses_target(target_url)

    def _update_version_frontier(self, query: str, data: dict[str, Any]) -> None:
        return self._discovery._update_version_frontier(query, data)

    def _result_version_leads(self, data: dict[str, Any]) -> set[str]:
        return self._discovery._result_version_leads(data)

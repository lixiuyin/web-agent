"""Browser-grounded authorization with optional first-party discovery."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from webagent.core.models import ToolCall, ToolResult
from webagent.tools.policies.contracts import (
    PageProvider,
    PolicyDecision,
    ToolExecutionPolicy,
)
from webagent.tools.policies.evidence import (
    _DISCOVERY_TASK_RE,
    _DOWNLOAD_TOOLS,
    _HYBRID_DISCOVERY_TOOLS,
    _LATEST_EVIDENCE_GUIDANCE,
    _REPORT_FILE_RE,
    _canonical_url,
    _checkpoint_policy_url,
    _decode_visible_result,
    _repository_identity,
    _version_key,
)
from webagent.tools.policies.search import SearchEngineOnlyPolicy


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
        candidate_url, candidate_date, candidate_owner, subject = self._hybrid_candidate_identity(
            raw_hybrid
        )
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

    @staticmethod
    def _hybrid_candidate_identity(
        raw_hybrid: dict[str, Any],
    ) -> tuple[str | None, str | None, str | None, str]:
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
        return candidate_url, candidate_date, candidate_owner, subject

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

    def _qualify_hybrid_candidate(
        self, candidate: dict[str, Any], owner: str
    ) -> tuple[str, str, str, tuple[int, ...], dict[str, Any]] | None:
        if candidate.get("first_party") is not True:
            return None
        date_value = candidate.get("date") or candidate.get("committed_at")
        pdf_value = candidate.get("pdf_url") or candidate.get("download_url")
        html_value = candidate.get("html_url")
        if not isinstance(date_value, str) or re.match(r"^\d{4}-\d{2}-\d{2}", date_value) is None:
            return None
        if not isinstance(pdf_value, str) or not isinstance(html_value, str):
            return None
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
            return None
        searchable = " ".join(
            str(candidate.get(field, "")) for field in ("title", "pdf_url", "download_url")
        )
        leads = self._result_version_leads({"results": [{"title": searchable, "url": html_url}]})
        version_key = max((_version_key(lead) for lead in leads), default=())
        return date_value, pdf_url, html_url, version_key, candidate

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
            evidence = self._qualify_hybrid_candidate(candidate, owner)
            if evidence is not None:
                verified.append(evidence)
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
        filename_denial = self._authorize_download_filename(tool_call)
        if filename_denial is not None:
            return filename_denial
        if self._discovery_required and not self._search_completed and name != "search":
            return self._deny(name, "discovery task must begin with browser search")
        if self._hybrid_mode and name in _HYBRID_DISCOVERY_TOOLS:
            hybrid_decision = self._authorize_hybrid_discovery(tool_call)
            if hybrid_decision is not None:
                return hybrid_decision
        if name == "search":
            recovery = self._authorize_exact_url_recovery_search(tool_call)
            if recovery is not None:
                return recovery
            return PolicyDecision(True, "browser search provides grounded discovery", self._step)

        self._record_current_page_url()
        latest_denial = self._authorize_latest_completion(tool_call)
        if latest_denial is not None:
            return latest_denial

        return self._authorize_grounded_action(tool_call)

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
            "browser interaction is grounded in the current page or local workspace",
            self._step,
        )

    def _authorize_latest_completion(self, tool_call: ToolCall) -> PolicyDecision | None:
        name = tool_call.tool_name
        if (
            (self._discovery_required or self._hybrid_mode)
            and self._latest_task
            and (name == "done" or name in _DOWNLOAD_TOOLS)
        ):
            missing = self._latest_missing_prerequisites(self._latest_target_url(tool_call))
            if missing:
                if self._hybrid_mode and self._hybrid_direct_evidence_verified:
                    return self._deny_hybrid_progress(
                        name,
                        self._format_missing_latest(missing),
                        self._hybrid_next_action(missing),
                    )
                return self._deny_missing_latest(name, missing)

        return None


__all__ = [
    "BrowserGroundedPolicy",
    "PolicyDecision",
    "SearchEngineOnlyPolicy",
    "ToolExecutionPolicy",
]

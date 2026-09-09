"""Candidate-specific latest-report evidence, distinct from search activity."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from webagent.utils.urls import document_key

_REPORT_SIGNAL_RE = re.compile(
    r"(?:\btechnical\s+report\b|\btech[-_ ]?report\b|\bwhite\s*paper\b|"
    r"\bresearch\s+paper\b|\barxiv\b|\.pdf(?:\b|$))",
    flags=re.IGNORECASE,
)


def versions(
    text: str, keywords: set[str], *, include_integer: bool = False
) -> dict[str, tuple[int, ...]]:
    found: dict[str, tuple[int, ...]] = {}
    number = r"\d+(?:\.\d+)*" if include_integer else r"\d+(?:\.\d+)+"
    for keyword in keywords:
        pattern = rf"(?<![a-z0-9])({re.escape(keyword)}[-_ ]*({number})(?:-[a-z][a-z0-9-]*)?)(?![a-z0-9]|\.\d|\s*(?:billion|million)\b)"
        for match in re.finditer(pattern, text.casefold()):
            found[normalize_name(match[1])] = tuple(map(int, match[2].split(".")))
    return found


def normalize_name(text: str) -> str:
    return re.sub(r"(?<=[a-z])[-_ ]+(?=\d)", "", text.casefold())


def _valid_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value[:10].replace("/", "-"))
        return parsed.isoformat() if parsed <= datetime.now(UTC).date() else None
    except ValueError:
        return None


def _inspection_candidates(data: dict[str, Any], source: str) -> list[dict[str, Any]]:
    candidates = list(data.get("candidates", []))
    # A browser's URL-less download button still identifies the current PDF
    # preview, without inventing a raw URL that the page never exposed.
    if urlsplit(source).path.lower().endswith(".pdf") and data.get("download_controls"):
        candidates.append({"url": source, "text": "PDF preview with observed download control"})
    return candidates


def _candidate_url_rank(url: str) -> int:
    """Prefer document-shaped sources without treating their host as trusted."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return -1
    path = parsed.path.casefold()
    rank = 0
    if path.endswith(".pdf"):
        rank += 4
    if any(marker in path for marker in ("/blob/", "/raw/", "/commits/")):
        rank += 2
    if parsed.netloc.casefold() in {"arxiv.org", "export.arxiv.org"}:
        rank += 1
    if "report" in path or "paper" in path:
        rank += 1
    return rank


def _document_matches_lead(document: dict[str, Any], lead: dict[str, Any]) -> bool:
    context = normalize_name(
        " ".join(str(document.get(key, "")) for key in ("url", "source_page", "title"))
    )
    return str(lead["name"]) in context


class CandidateLedger:
    def __init__(self) -> None:
        self.leads: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, dict[str, Any]] = {}

    def search(self, query: str, data: dict[str, Any], keywords: set[str]) -> None:
        for result in data.get("results", []):
            text = " ".join(str(result.get(key, "")) for key in ("title", "url", "snippet"))
            # The task asks for the newest *report*, not the newest product release.
            # Keep general release names in the separate version-frontier workflow,
            # but do not let an unsourced model announcement become a report
            # candidate merely because the search query contained "technical report".
            if not _REPORT_SIGNAL_RE.search(text):
                continue
            for name, version in versions(text, keywords).items():
                lead = self.leads.setdefault(
                    name,
                    {"name": name, "version": list(version), "urls": [], "status": "discovered"},
                )
                url = result.get("url", "")
                if url not in lead["urls"]:
                    lead["urls"].append(url)
                report_urls = lead.setdefault("report_urls", [])
                if url not in report_urls:
                    report_urls.append(url)
                if name in normalize_name(query) and lead["status"] != "official_checked":
                    lead["status"] = "searched"
                    # query_attempt runs before results are ingested, so a lead
                    # first discovered by this very query has no counter yet.
                    # Preserve existing counts without counting the query twice.
                    lead["query_attempts"] = max(1, lead.get("query_attempts", 0))

    def page(self, url: str, text: str, *, official: bool, keywords: set[str]) -> None:
        if not official:
            return
        names = versions(url + " " + text, keywords)
        for name, version in names.items():
            self.leads.setdefault(
                name,
                {"name": name, "version": list(version), "urls": [url], "status": "discovered"},
            )
        # A release index mentioning a model is discovery, not inspection of its page.
        checked = versions(url, keywords)
        if parse_qs(urlsplit(url).query).get("id"):
            # Article slugs can name a family while its visible heading names
            # a variant. A bare blog index does not establish this binding.
            checked.update(versions(text[:1600], keywords))
        for name, lead in self.leads.items():
            if name in checked or any(n.startswith(name + "-") for n in checked):
                lead.update(status="official_checked", checked_url=url)

    def query_attempt(self, query: str) -> None:
        for name, lead in self.leads.items():
            if name in normalize_name(query):
                lead["query_attempts"] = lead.get("query_attempts", 0) + 1

    def next_unsearched_variant(self) -> str | None:
        frontier = max((tuple(lead["version"]) for lead in self.leads.values()), default=())
        pending = [
            lead
            for name, lead in self.leads.items()
            if "-" in name
            and tuple(lead["version"]) >= frontier
            and lead["status"] == "discovered"
            and not lead.get("query_attempts")
        ]
        if not pending:
            return None
        return str(max(pending, key=lambda lead: (lead["version"], lead["name"]))["name"])

    def next_report_source(self, *, minimum_version: tuple[int, ...] = ()) -> dict[str, str] | None:
        """Return the strongest unresolved report-shaped source at the frontier.

        This is a navigation hint, not an endorsement. The caller must retain its
        normal identity, scope, inspection, and download gates.
        """
        frontier = max((tuple(lead["version"]) for lead in self.leads.values()), default=())
        if frontier < minimum_version:
            return None
        leads = [lead for lead in self.leads.values() if tuple(lead["version"]) == frontier]
        ranked: list[tuple[int, int, str, str, str]] = []
        for lead in leads:
            matching_documents = [
                document
                for document in self.documents.values()
                if _document_matches_lead(document, lead)
            ]
            if any(document.get("file_inspected") for document in matching_documents):
                continue
            for document in matching_documents:
                url = str(document.get("url", ""))
                if (rank := _candidate_url_rank(url)) >= 0:
                    ranked.append((1, rank, str(lead["name"]), url, "document"))
            for url_value in lead.get("report_urls", []):
                url = str(url_value)
                if (rank := _candidate_url_rank(url)) >= 0:
                    ranked.append((0, rank, str(lead["name"]), url, "search_result"))
        if not ranked:
            return None
        _, _, name, url, source = max(ranked)
        return {"name": name, "url": url, "source": source}

    def inspect(self, data: dict[str, Any], *, official: bool) -> None:
        source = data.get("source_url", "")
        if not isinstance(source, str) or not official:
            return
        for candidate in _inspection_candidates(data, source):
            url = candidate.get("url")
            if not isinstance(url, str):
                continue
            key = document_key(url)
            entry = self.documents.setdefault(
                key,
                {
                    "url": url,
                    "source_page": source,
                    "official": True,
                    "dates": [],
                    "title": candidate.get("text", ""),
                },
            )
            # Dates from a repository listing belong to different files. Only a
            # document's own preview can bind its displayed time to that report.
            if document_key(source) != key:
                continue
            entry["file_inspected"] = True
            publication_dates = data.get("publication_dates", [])
            if publication_dates:
                entry["dates"] = [
                    {"value": value, "kind": "report_published", "source": source}
                    for item in publication_dates
                    if (value := _valid_date(item.get("value")))
                ]
            elif urlsplit(source).path.lower().endswith(".pdf"):
                dates = {
                    _valid_date(item.get("datetime")) for item in data.get("date_evidence", [])
                }
                dates.discard(None)
                if len(dates) == 1:
                    entry["dates"] = [
                        {
                            "value": next(iter(dates)),
                            "kind": "report_file_updated",
                            "source": source,
                        }
                    ]

    def inspection_attempt(self, source: str, success: bool) -> None:
        entry = self.documents.get(document_key(source))
        if entry is not None:
            entry["file_inspected"] = True
            entry["inspection_status"] = "complete" if success else "failed"

    def assessment(self, target: str | None, keywords: set[str]) -> dict[str, Any]:
        entry = self.documents.get(document_key(target or ""))
        missing: list[str] = []
        if entry is None or not entry["dates"]:
            missing.append(
                "inspect the selected report's own preview with inspect_download_links to bind an explicit report-file date; repository listing dates are insufficient"
            )
        target_versions = versions(target or "", keywords, include_integer=True)
        target_version = max(target_versions.values(), default=())
        unresolved = [
            name
            for name, lead in self.leads.items()
            if target_version
            and (
                tuple(lead["version"]) > target_version
                or (
                    tuple(lead["version"]) == target_version
                    and lead["status"] != "official_checked"
                    and not any(n.startswith(name) for n in target_versions)
                )
            )
        ]
        if unresolved:
            missing.append(
                "resolve newer release leads on official pages before selecting an older report: "
                + ", ".join(unresolved)
            )
        limitations = [
            name
            for name in unresolved
            if tuple(self.leads[name]["version"]) == target_version
            and self.leads[name].get("query_attempts", 0) > 0
        ]
        if entry and entry["dates"]:
            selected_date = entry["dates"][0]["value"]
            newer = [
                doc["url"]
                for doc in self.documents.values()
                if doc["dates"] and doc["dates"][0]["value"] > selected_date
            ]
            if newer:
                missing.append("newer dated official report candidates remain: " + ", ".join(newer))
        return deepcopy(
            {
                "schema_version": 1,
                "status": "supported_within_observed_candidates" if not missing else "unresolved",
                "selected": entry,
                "missing": missing,
                "partial_completion_allowed": bool(
                    unresolved and set(unresolved) == set(limitations) and len(missing) == 1
                ),
                "unverified_release_leads": limitations,
                "leads": list(self.leads.values()),
                "documents": list(self.documents.values()),
                "coverage": "observed browser evidence only; not an exhaustive web guarantee",
            }
        )

    def export(self) -> dict[str, Any]:
        return deepcopy({"leads": self.leads, "documents": self.documents})

    def restore(self, value: Any) -> None:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("leads"), dict)
            or not isinstance(value.get("documents"), dict)
        ):
            raise ValueError("invalid candidate ledger checkpoint")
        self.leads = deepcopy(value["leads"])
        self.documents = deepcopy(value["documents"])

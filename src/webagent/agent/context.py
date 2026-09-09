"""Compact, evidence-preserving tool results for planner history and traces."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


def planner_context(tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Project verbose tool output onto the evidence needed by later steps."""
    projector = _TOOL_PROJECTORS.get(tool_name)
    return projector(data) if projector is not None else data


def planner_result_preview(tool_name: str, data: dict[str, Any], *, success: bool) -> str:
    """Return the exact serialized tool data made visible to the planner.

    URL-provenance enforcement consumes this same string, so a value truncated
    out of planner history cannot silently authorize a later navigation.
    """
    projected = planner_context(tool_name, data)
    preview = json.dumps(projected, ensure_ascii=False)
    if success:
        max_len = 5000 if projected is not data else 500
    else:
        max_len = 2000
    if len(preview) > max_len:
        return _bounded_json(projected, max_len)
    return preview


def _bounded_json(data: dict[str, Any], limit: int) -> str:
    """Remove whole records, never cut a URL or emit invalid JSON."""
    value = deepcopy(data)
    original_links = len(value.get("links", []))
    omitted: dict[str, int] = {}
    while len(json.dumps(value, ensure_ascii=False)) > limit and value:
        key = max(value, key=lambda k: len(json.dumps(value[k], ensure_ascii=False)))
        item = value[key]
        if isinstance(item, list) and item:
            item.pop()
        else:
            del value[key]
        omitted[key] = omitted.get(key, 0) + 1
    value["preview_omitted"] = omitted
    if "links" in value and original_links != len(value["links"]):
        visible = len(value["links"])
        value.update(visible_count=visible, next_offset=data.get("offset", 0) + visible)
        value["omitted_count"] = data.get("returned", original_links) - visible
    return json.dumps(value, ensure_ascii=False)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _pick(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: data[key] for key in keys if key in data}


def _report_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        item,
        "source",
        "title",
        "date",
        "pdf_url",
        "html_url",
        "first_party",
        "provenance",
    )


def _project_github_search(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        "query",
        "repository_query",
        "owner",
        "provenance_notice",
        "candidates",
        "browser_url",
    )


def _project_official_report_search(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(
        data,
        "subject",
        "requested_subject",
        "official_owner",
        "provenance_notice",
        "source_status",
        "browser_url",
    )
    projected["verified_first_party_candidates"] = [
        _report_candidate(item)
        for item in _dict_items(data.get("verified_first_party_candidates"))[:10]
    ]
    projected["all_candidates"] = [
        _report_candidate(item) for item in _dict_items(data.get("all_candidates"))[:10]
    ]
    return projected


def _project_arxiv_search(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(data, "query", "count", "browser_url")
    projected["results"] = [
        _pick(item, "title", "authors", "published", "pdf_url", "abs_url")
        for item in _dict_items(data.get("results"))[:10]
    ]
    return projected


def _project_search(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(
        data,
        "query",
        "engine",
        "url",
        "title",
        "attempted_engines",
        "failure_category",
        "search_attempts",
        "search_market",
        "results_scope",
        "novel_result_count",
        "repeated_result_set",
        "recovery_hint",
    )
    projected["results"] = [
        _pick(item, "title", "url", "date", "snippet")
        for item in _dict_items(data.get("results"))[:10]
    ]
    return projected


def _project_get_search_results(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(
        data,
        "engine",
        "query",
        "count",
        "total_available",
        "more_results",
    )
    projected["results"] = [
        _pick(item, "title", "url", "link", "snippet")
        for item in _dict_items(data.get("results"))[:10]
    ]
    return projected


def _project_get_all_links(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(
        data, "source_url", "total_count", "returned", "offset", "filtered_count", "next_offset"
    )
    projected["links"] = [
        _pick(item, "href", "text") for item in _dict_items(data.get("links"))[:20]
    ]
    visible = len(projected["links"])
    projected["visible_count"] = visible
    projected["omitted_count"] = max(0, int(data.get("returned", visible)) - visible)
    if projected["omitted_count"]:
        projected["next_offset"] = int(data.get("offset", 0)) + visible
    return projected


def _project_download_pdf(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        "path",
        "filename",
        "source_url",
        "downloaded_bytes",
        "browser_url",
        "ssl_warning",
    )


def _project_inspect_download_links(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(data, "source_url", "candidate_count", "hint", "publication_dates")
    projected["candidates"] = [
        _pick(item, "url", "evidence_type", "element", "text")
        for item in _dict_items(data.get("candidates"))[:10]
    ]
    projected["download_controls"] = [
        _pick(item, "selector", "element", "text")
        for item in _dict_items(data.get("download_controls"))[:5]
    ]
    projected["date_evidence"] = [
        _pick(item, "datetime", "text", "element")
        for item in _dict_items(data.get("date_evidence"))[:10]
    ]
    projected["history_links"] = [
        _pick(item, "url", "text") for item in _dict_items(data.get("history_links"))[:10]
    ]
    return projected


def _project_pdf_parse(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(
        data,
        "markdown_path",
        "json_path",
        "image_count",
        "table_count",
        "section_count",
        "output_dir",
        "method",
        "backend",
    )
    projected["figures"] = [
        _pick(item, "path", "page", "caption", "figure_number")
        for item in _dict_items(data.get("images"))[:5]
    ]
    projected["tables"] = [
        _pick(item, "page", "caption", "table_number")
        for item in _dict_items(data.get("tables"))[:3]
    ]
    return projected


def _project_pdf_get_figure_info(data: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        data,
        "found",
        "figure_number",
        "page",
        "caption",
        "image_path",
        "vision_analysis",
        "vision_duration_seconds",
        "vision_metadata",
        "local_figure_fast_path",
        "related_tables",
        "message",
    )


def _project_pdf_qa(data: dict[str, Any]) -> dict[str, Any]:
    projected = _pick(data, "question", "query", "context", "context_length", "sources")
    projected["found_figures"] = [
        _pick(item, "page", "caption", "figure_number", "path")
        for item in _dict_items(data.get("found_figures"))[:3]
    ]
    return projected


_TOOL_PROJECTORS = {
    "github_search": _project_github_search,
    "official_report_search": _project_official_report_search,
    "arxiv_search": _project_arxiv_search,
    "search": _project_search,
    "get_search_results": _project_get_search_results,
    "get_all_links": _project_get_all_links,
    "download_pdf": _project_download_pdf,
    "inspect_download_links": _project_inspect_download_links,
    "pdf_parse": _project_pdf_parse,
    "pdf_get_figure_info": _project_pdf_get_figure_info,
    "pdf_analyze_figure": _project_pdf_get_figure_info,
    "pdf_qa": _project_pdf_qa,
    "pdf_search": _project_pdf_qa,
}


__all__ = ["planner_context", "planner_result_preview"]

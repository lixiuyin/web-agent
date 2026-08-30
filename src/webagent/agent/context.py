"""Compact, evidence-preserving tool results for planner history and traces."""

from __future__ import annotations

import json
from typing import Any


def planner_context(tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Project verbose tool output onto the evidence needed by later steps."""
    if tool_name == "github_search":
        return _pick(
            data,
            "query",
            "repository_query",
            "owner",
            "provenance_notice",
            "candidates",
            "browser_url",
        )
    if tool_name == "official_report_search":
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
    if tool_name == "arxiv_search":
        projected = _pick(data, "query", "count", "browser_url")
        projected["results"] = [
            _pick(item, "title", "authors", "published", "pdf_url", "abs_url")
            for item in _dict_items(data.get("results"))[:10]
        ]
        return projected
    if tool_name == "search":
        projected = _pick(
            data,
            "query",
            "engine",
            "url",
            "title",
            "attempted_engines",
            "failure_category",
            "search_attempts",
        )
        projected["results"] = [
            _pick(item, "title", "url", "date", "snippet")
            for item in _dict_items(data.get("results"))[:10]
        ]
        return projected
    if tool_name == "download_pdf":
        return _pick(
            data,
            "path",
            "filename",
            "source_url",
            "downloaded_bytes",
            "browser_url",
            "ssl_warning",
        )
    if tool_name == "inspect_download_links":
        projected = _pick(data, "source_url", "candidate_count")
        projected["candidates"] = [
            _pick(item, "url", "evidence_type", "element", "text")
            for item in _dict_items(data.get("candidates"))[:10]
        ]
        projected["date_evidence"] = [
            _pick(item, "datetime", "text", "element")
            for item in _dict_items(data.get("date_evidence"))[:10]
        ]
        projected["history_links"] = [
            _pick(item, "url", "text") for item in _dict_items(data.get("history_links"))[:10]
        ]
        return projected
    if tool_name == "pdf_parse":
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
    if tool_name in {"pdf_get_figure_info", "pdf_analyze_figure"}:
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
    if tool_name in {"pdf_qa", "pdf_search"}:
        projected = _pick(data, "question", "query", "context", "context_length", "sources")
        projected["found_figures"] = [
            _pick(item, "page", "caption", "figure_number", "path")
            for item in _dict_items(data.get("found_figures"))[:3]
        ]
        return projected
    return data


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
        return preview[:max_len] + "..."
    return preview


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


__all__ = ["planner_context", "planner_result_preview"]

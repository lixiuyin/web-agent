"""Compact JSON Schemas for provider-native tool calling.

The runtime validators on each tool remain the final authority.  These schemas
serve two narrower purposes: they let providers constrain tool-call generation,
and they give planners machine-readable parameter documentation.  Keeping the
catalog here avoids trying to infer types from human prose in tool descriptions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

JsonSchema = dict[str, Any]


def _object(
    properties: dict[str, JsonSchema] | None = None,
    *,
    required: tuple[str, ...] = (),
    additional: bool = False,
    any_of: list[JsonSchema] | None = None,
) -> JsonSchema:
    schema: JsonSchema = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": additional,
    }
    if required:
        schema["required"] = list(required)
    if any_of:
        schema["anyOf"] = any_of
    return schema


def _string(*, enum: tuple[str, ...] = (), description: str = "") -> JsonSchema:
    schema: JsonSchema = {"type": "string"}
    if enum:
        schema["enum"] = list(enum)
    if description:
        schema["description"] = description
    return schema


def _integer(
    *, minimum: int | None = None, maximum: int | None = None, default: int | None = None
) -> JsonSchema:
    schema: JsonSchema = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    if maximum is not None:
        schema["maximum"] = maximum
    if default is not None:
        schema["default"] = default
    return schema


def _boolean(*, default: bool | None = None) -> JsonSchema:
    schema: JsonSchema = {"type": "boolean"}
    if default is not None:
        schema["default"] = default
    return schema


def _array(items: JsonSchema) -> JsonSchema:
    return {"type": "array", "items": items}


_SELECTOR = _object(
    {
        "type": _string(enum=("text", "css")),
        "value": _string(description="Visible text or a CSS selector"),
    },
    required=("type", "value"),
)
_CSS_SELECTOR = _object(
    {"type": {"type": "string", "const": "css"}, "value": _string()},
    required=("type", "value"),
)
_PATH = _string(description="Local path returned by a prior allowed tool")
_URL = _string(description="HTTP(S) URL observed in browser-grounded evidence")
_EMPTY = _object()


TOOL_PARAMETER_SCHEMAS: dict[str, JsonSchema] = {
    # Browser navigation and interaction.
    "goto": _object(
        {
            "url": _URL,
            "wait_until": _string(enum=("load", "domcontentloaded", "networkidle")),
        },
        required=("url",),
    ),
    "click": _object(
        {"selector": _SELECTOR, "force": _boolean(default=False)},
        required=("selector",),
    ),
    "click_link": _object({"text": _string(), "fuzzy": _boolean(default=True)}, required=("text",)),
    "type": _object(
        {
            "selector": _SELECTOR,
            "text": _string(),
            "delay_ms": _integer(minimum=0, maximum=60_000, default=50),
            "clear_first": _boolean(default=True),
        },
        required=("selector", "text"),
    ),
    "press": _object({"key": _string(), "selector": _SELECTOR}, required=("key",)),
    "scroll": _object(
        {
            "direction": _string(enum=("up", "down")),
            "amount_px": _integer(minimum=1, default=500),
        }
    ),
    "wait": _object({"ms": _integer(minimum=0, maximum=60_000, default=1000)}),
    "forward": _object({"steps": _integer(minimum=1, default=1)}),
    "back": _object({"steps": _integer(minimum=1, default=1)}),
    "hover": _object({"selector": _SELECTOR}, required=("selector",)),
    "select_dropdown": _object(
        {
            "selector": _SELECTOR,
            "value": _string(),
            "label": _string(),
            "index": _integer(minimum=0),
        },
        required=("selector",),
        any_of=[{"required": ["value"]}, {"required": ["label"]}, {"required": ["index"]}],
    ),
    "wait_for_element": _object(
        {
            "selector": _SELECTOR,
            "state": _string(enum=("visible", "hidden", "attached", "detached")),
            "timeout_ms": _integer(minimum=0, maximum=120_000, default=30_000),
        },
        required=("selector",),
    ),
    "get_attribute": _object(
        {"selector": _SELECTOR, "attribute": _string()},
        required=("selector", "attribute"),
    ),
    "get_all_links": _object(
        {
            "skip_anchors": _boolean(default=False),
            "skip_javascript": _boolean(default=False),
            "filter_external_only": _boolean(default=False),
            "max_results": _integer(minimum=0, maximum=1000, default=100),
        }
    ),
    "get_url": _EMPTY,
    "get_title": _EMPTY,
    "refresh": _EMPTY,
    "scroll_to_element": _object({"selector": _SELECTOR}, required=("selector",)),
    "get_search_results": _object(
        {
            "max_results": _integer(minimum=1, maximum=100, default=10),
            "show_all": _boolean(default=False),
        }
    ),
    "screenshot": _object({"full_page": _boolean(default=False), "label": _string()}),
    "dom_summary": _EMPTY,
    "extract_text": _object({"selector": _SELECTOR}, required=("selector",)),
    # Frames, tabs, files, and complex DOM surfaces.
    "list_frames": _EMPTY,
    "frame_interact": _object(
        {
            "frame_index": _integer(minimum=0),
            "action": _string(enum=("click", "type", "extract_text")),
            "selector": _SELECTOR,
            "text": _string(),
        },
        required=("frame_index", "action", "selector"),
    ),
    "list_tabs": _EMPTY,
    "switch_tab": _object({"index": _integer(minimum=0)}, required=("index",)),
    "open_tab": _object({"url": _URL}),
    "close_tab": _object({"index": _integer(minimum=0)}),
    "upload_file": _object({"selector": _SELECTOR, "path": _PATH}, required=("selector", "path")),
    "download_file": _object(
        {"selector": _SELECTOR, "filename": _string()}, required=("selector",)
    ),
    "shadow_dom": _object(
        {
            "action": _string(enum=("click", "type", "extract_text")),
            "selector": _CSS_SELECTOR,
            "text": _string(),
        },
        required=("action", "selector"),
    ),
    "save_image": _object({"base64": _string(), "path": _string()}, required=("base64", "path")),
    "write_text": _object({"path": _string(), "content": _string()}, required=("path", "content")),
    "read_image": _object(
        {"path": _PATH, "open_browser": _boolean(default=True)}, required=("path",)
    ),
    "analyze_image": _object({"path": _PATH, "question": _string()}, required=("path", "question")),
    # Browser-grounded and optional API-augmented discovery.
    "search": _object(
        {
            "query": _string(),
            "engine": _string(
                enum=("bing", "seznam", "yahoo_japan", "yahoo", "duckduckgo", "google")
            ),
            "recency": _string(enum=("week", "month", "year", "latest")),
        },
        required=("query",),
    ),
    "inspect_download_links": _object(
        {"max_results": _integer(minimum=1, maximum=100, default=10)}
    ),
    "arxiv_search": _object(
        {
            "query": _string(),
            "max_results": _integer(minimum=1, maximum=100, default=5),
            "sort": _string(enum=("recent", "relevance")),
        },
        required=("query",),
    ),
    "github_search": _object(
        {
            "query": _string(),
            "owner": _string(),
            "max_results": _integer(minimum=1, maximum=10, default=5),
        },
        required=("query",),
    ),
    "official_report_search": _object(
        {
            "subject": _string(),
            "official_owner": _string(),
            "max_results": _integer(minimum=1, maximum=10, default=10),
        },
        required=("subject",),
    ),
    # Download and document workflows.
    "download_pdf": _object({"url": _URL, "filename": _string()}, required=("url",)),
    "pdf_parse": _object({"path": _PATH, "output_dir": _string()}, required=("path",)),
    "pdf_content_summary": _object({"path": _PATH}, required=("path",)),
    "pdf_find_images": _object(
        {"path": _PATH, "keyword": _string(), "case_sensitive": _boolean(default=False)},
        required=("path", "keyword"),
    ),
    "pdf_find_tables": _object(
        {"path": _PATH, "keyword": _string(), "case_sensitive": _boolean(default=False)},
        required=("path", "keyword"),
    ),
    "pdf_find_section": _object(
        {"path": _PATH, "title": _string(), "case_sensitive": _boolean(default=False)},
        required=("path", "title"),
    ),
    "pdf_extract_text": _object({"path": _PATH}, required=("path",)),
    "pdf_extract_images": _object({"path": _PATH}, required=("path",)),
    "pdf_get_figure_info": _object(
        {"path": _PATH, "figure_number": {"type": ["string", "integer"]}},
        required=("path", "figure_number"),
    ),
    "pdf_extract_table_data": _object(
        {"path": _PATH, "table_number": _string(), "query": _string()},
        required=("path", "table_number"),
    ),
    "pdf_find_mentions": _object(
        {
            "path": _PATH,
            "type": _string(enum=("figure", "table")),
            "number": _string(),
        },
        required=("path", "type", "number"),
    ),
    "pdf_get_section": _object(
        {"path": _PATH, "section_title": _string()}, required=("path", "section_title")
    ),
    "pdf_get_hierarchy": _object({"path": _PATH}, required=("path",)),
    "pdf_get_metadata": _object({"path": _PATH}, required=("path",)),
    "pdf_extract_metrics": _object({"path": _PATH}, required=("path",)),
    "pdf_extract_topics": _object(
        {"path": _PATH, "top_n": _integer(minimum=1, default=20)}, required=("path",)
    ),
    "pdf_extract_citations": _object({"path": _PATH}, required=("path",)),
    "pdf_summarize_sections": _object({"path": _PATH}, required=("path",)),
    "pdf_compare_entities": _object({"path": _PATH, "entity": _string()}, required=("path",)),
    "pdf_qa": _object({"path": _PATH, "question": _string()}, required=("path", "question")),
    "pdf_search": _object(
        {
            "path": _PATH,
            "query": _string(),
            "max_results": _integer(minimum=1, maximum=100, default=5),
        },
        required=("path", "query"),
    ),
    "pdf_list_figures": _object({"path": _PATH}, required=("path",)),
    "pdf_list_tables": _object({"path": _PATH}, required=("path",)),
    "pdf_list_sections": _object({"path": _PATH}, required=("path",)),
    "pdf_analyze_figure": _object(
        {
            "path": _PATH,
            "figure_number_or_caption": _string(),
            "question": _string(),
        },
        required=("path", "figure_number_or_caption"),
    ),
    # Terminal action.
    "done": _object(
        {
            "summary": _string(),
            "attachments": _array(_string()),
            "success_probability": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        required=("summary",),
    ),
    "remember": _object(
        {
            "note": _string(
                description="Short non-sensitive fact to retain across the rolling context"
            )
        },
        required=("note",),
    ),
}


def generic_parameter_schema() -> JsonSchema:
    """Return a safe, legal schema for third-party tools without a catalog entry."""
    return _object(additional=True)


def parameter_schema_for(name: str, explicit: JsonSchema | None = None) -> JsonSchema:
    """Return an isolated schema copy so callers cannot mutate registry metadata."""
    schema = explicit if explicit is not None else TOOL_PARAMETER_SCHEMAS.get(name)
    return deepcopy(schema if schema is not None else generic_parameter_schema())


def validate_parameter_schema(schema: JsonSchema) -> None:
    """Validate the small invariant set required by provider function tools."""
    if schema.get("type") != "object":
        raise ValueError("tool parameter schema root must have type='object'")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("tool parameter schema must define object properties")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("tool parameter schema required must be a string list")
    unknown = set(required) - set(properties)
    if unknown:
        raise ValueError(f"tool parameter schema requires unknown fields: {sorted(unknown)}")
    if "additionalProperties" not in schema:
        raise ValueError("tool parameter schema must declare additionalProperties")


__all__ = [
    "TOOL_PARAMETER_SCHEMAS",
    "JsonSchema",
    "generic_parameter_schema",
    "parameter_schema_for",
    "validate_parameter_schema",
]

"""Describe planner observation inputs without retaining credentials or full prompts."""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from webagent.core.models import BrowserState
from webagent.planner.base import planner_dom_context


def input_metadata(
    state: BrowserState, screenshot_b64: str | None, omission_reason: str | None
) -> dict[str, Any]:
    dom = planner_dom_context(state)
    omissions = state.observation_metadata.get("context_omissions", {})
    return {
        "observation_id": state.observation_id,
        "requires_visual": state.requires_visual,
        "observation_status": state.observation_metadata.get("status", "unknown"),
        "viewport_chars": len(state.viewport_context or ""),
        "document_chars": len(state.document_context or ""),
        "context_omissions": omissions,
        "context_budget_usage": state.observation_metadata.get("context_budget_usage", {}),
        "element_omissions": state.observation_metadata.get("element_omissions", 0),
        "collection_truncated": any(
            frame.get("omitted") for frame in state.observation_metadata.get("frames", [])
        ),
        "collected_viewport_chars": sum(map(len, state.viewport_blocks)),
        "collected_document_chars": sum(map(len, state.document_blocks)),
        "dom_chars": len(dom),
        "dom_total_chars": len(state.dom_summary),
        "dom_truncated": any(omissions.values())
        if omissions
        else len(dom) < len(state.dom_summary),
        "dom_sha256": hashlib.sha256(dom.encode("utf-8")).hexdigest(),
        "screenshot_captured": state.screenshot is not None,
        "screenshot_included": screenshot_b64 is not None,
        "screenshot_sent": False,
        "request_dispatched": False,
        "screenshot_omission_reason": omission_reason,
        "screenshot_sha256": (
            hashlib.sha256(base64.b64decode(screenshot_b64)).hexdigest()
            if screenshot_b64 is not None
            else None
        ),
        "screenshot_encoding": "jpeg_quality_70" if screenshot_b64 is not None else None,
    }

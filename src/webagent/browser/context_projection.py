"""Separate screen-local evidence from document context, using whole-block budgets."""

from __future__ import annotations

import re
import textwrap
from typing import Any

from webagent.core.models import BrowserState


def planner_dom_context(browser_state: BrowserState) -> str:
    """One exact projection shared by planner input and evidence authorization."""
    if browser_state.viewport_context is not None:
        omissions = browser_state.observation_metadata.get("context_omissions", {})
        frames = browser_state.observation_metadata.get("frames", [])
        coverage = [
            {key: f.get(key) for key in ("frame_index", "omitted", "viewport_status")}
            for f in frames
        ]
        return (
            f"OBSERVATION ID: {browser_state.observation_id}\n"
            "VIEWPORT CONTENT (same region as viewport screenshot):\n"
            f"{browser_state.viewport_context or '(No viewport text/control projection available.)'}\n\n"
            "DOCUMENT SUPPLEMENT (may be outside screenshot; not visual evidence):\n"
            f"{browser_state.document_context or '(None)'}\n\n"
            f"OMITTED COMPLETE BLOCKS: {omissions}"
            f"; omitted controls: {browser_state.observation_metadata.get('element_omissions', 0)}"
            f"\nFRAME COVERAGE: {coverage}; unavailable: "
            f"{browser_state.observation_metadata.get('unavailable_frames', [])}"
        )
    return pack_blocks(browser_state.dom_summary.split("\n\n"), 6000)[0]


def pack_blocks(blocks: list[str], budget: int) -> tuple[str, int]:
    """Fit complete records; never cut a selector or half a text record."""
    selected: list[str] = []
    used = 0
    omitted = 0
    for block in blocks:
        cost = len(block) + (2 if selected else 0)
        if used + cost > budget:
            omitted += 1
            continue
        selected.append(block)
        used += cost
    return "\n\n".join(selected), omitted


def observation_evidence_text(state: BrowserState) -> str:
    """External page text, excluding editable values and control metadata echoes."""
    contexts = (
        [state.viewport_context, state.document_context or ""]
        if state.viewport_context is not None
        else [planner_dom_context(state)]
    )
    return "\n\n".join(
        block
        for context in contexts
        for block in context.split("\n\n")
        if not block.lstrip().startswith("[") and not re.match(r"Frame \d+ editable text:", block)
    )


def _control_block(element: dict[str, Any]) -> str:
    reference = f"{element['observation_id']}/{element['ref']}"
    semantic = element.get("attrs", {})
    label = semantic.get("aria-label") or semantic.get("placeholder") or element.get("text", "")
    frame = element.get("frame_index", 0)
    hints = "".join(
        f" {key}={semantic[key]!r}"
        for key in (
            "role",
            "value",
            "aria-expanded",
            "aria-checked",
            "aria-selected",
            "aria-pressed",
        )
        if key in semantic
    )
    if element.get("interaction_source") == "pointer_hint":
        hints += " affordance=pointer_hint"
    if "checked" in element:
        hints += f" checked={element['checked']}"
    return (
        f"[{reference}] {element['tag']} label={label!r}{hints} "
        f"visible={is_viewport_control(element)} enabled={element.get('enabled')}; frame={frame}"
    )


def _text_chunks(sentence: str, width: int) -> list[str]:
    chunks = []
    for part in re.split(r"(https?://\S+)", sentence, flags=re.IGNORECASE):
        if re.match(r"https?://", part, flags=re.IGNORECASE):
            chunks.append(part)
        else:
            chunks.extend(textwrap.wrap(part, width=width))
    return chunks


def _text_blocks(text: str, frame: int, width: int, editable: bool = False) -> list[str]:
    prefix = f"Frame {frame} {'editable text' if editable else 'text'}: "
    return [
        prefix + chunk
        for sentence in re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+|\n+", text)
        for chunk in _text_chunks(sentence, max(1, width - len(prefix)))
    ]


def _pack_balanced(
    texts: list[str], controls: list[str], budget: int, text_share: float
) -> tuple[str, dict[str, int]]:
    """Reserve text capacity, give controls the rest, then reclaim unused capacity."""
    text, _ = pack_blocks(texts, int(budget * text_share))
    control, control_omitted = pack_blocks(controls, max(0, budget - len(text) - bool(text) * 2))
    text, text_omitted = pack_blocks(texts, max(0, budget - len(control) - bool(control) * 2))
    return "\n\n".join(part for part in (text, control) if part), {
        "text_chars": len(text),
        "control_chars": len(control),
        "text_omitted": text_omitted,
        "control_omitted": control_omitted,
    }


def project_context(
    projection: dict[str, Any],
    elements: list[dict[str, Any]],
    *,
    viewport_budget: int,
    document_budget: int,
    text_share: float = 0.5,
    text_block_chars: int = 400,
) -> dict[str, Any]:
    controls = {
        "viewport": [_control_block(e) for e in elements if is_viewport_control(e)],
        "document": [_control_block(e) for e in elements if not is_viewport_control(e)],
    }
    texts: dict[str, list[str]] = {"viewport": [], "document": []}
    budgets = {"viewport": viewport_budget, "document": document_budget}
    for record in projection["texts"]:
        values = {
            "viewport": record["viewport_text"],
            "document": record["text"] if record["text"] != record["viewport_text"] else "",
        }
        for scope, value in values.items():
            # A zero-budget scope still reports the unselected text blocks.
            width = min(text_block_chars, int(budgets[scope] * text_share) or text_block_chars)
            texts[scope].extend(
                _text_blocks(value, record["frame_index"], width, record.get("editable", False))
            )
    packed = {
        scope: _pack_balanced(texts[scope], controls[scope], budget, text_share)
        for scope, budget in budgets.items()
    }
    return {
        "viewport_context": packed["viewport"][0],
        "document_context": packed["document"][0],
        "viewport_blocks": texts["viewport"] + controls["viewport"],
        "document_blocks": texts["document"] + controls["document"],
        "context_omissions": {
            scope: usage["text_omitted"] + usage["control_omitted"]
            for scope, (_, usage) in packed.items()
        },
        "context_budget_usage": {scope: usage for scope, (_, usage) in packed.items()},
    }


def is_viewport_control(element: dict[str, Any]) -> bool:
    return element.get("in_viewport") is True and element.get("receives_events") is True

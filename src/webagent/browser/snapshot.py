"""Enhanced DOM snapshot with CDP integration and intelligent filtering.

This module provides browser state capture optimized for LLM consumption:
- AX Tree integration for semantic understanding
- Interactive element detection with ad filtering
- Priority-based element ranking
- Optimized markdown output with top N elements only

Inspired by browser-use's approach to page content understanding.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag
from playwright.async_api import Page

from webagent.browser.cdp_service import CDPService
from webagent.browser.interactive_detector import (
    extract_interactive_elements,
)
from webagent.browser.priority import sort_elements_by_priority

logger = logging.getLogger("webagent")

# Patterns for filtering ads/noise
AD_REGEX = re.compile(
    r"\b(ad|ads|sponsor|banner|promo|tracking|cookie|subscribe|advertisement|affiliate)\b",
    re.IGNORECASE,
)


async def take_snapshot(
    page: Page,
    full_page: bool = False,
    wait_after_load: int = 200,
    task: str = "",
    max_elements: int = 50,
    use_cdp: bool = True,
    filter_ads: bool = True,
) -> dict[str, Any]:
    """Capture enhanced DOM + screenshot from an existing Playwright page.

    This enhanced snapshot:
    1. Uses CDP to get semantic understanding via AX Tree
    2. Detects interactive elements with intelligent filtering
    3. Prioritizes elements based on position, type, and task relevance
    4. Generates optimized markdown showing only top N elements

    Args:
        page: Playwright page object
        full_page: Capture full page screenshot
        wait_after_load: Milliseconds to wait after page load
        task: User's task for relevance matching
        max_elements: Maximum number of elements to include in output
        use_cdp: Whether to use CDP for enhanced detection
        filter_ads: Whether to remove ad-like elements and containers

    Returns:
        Snapshot dict with markdown, elements, screenshot, and metadata
    """
    if wait_after_load > 0:
        try:
            await page.wait_for_timeout(wait_after_load)
        except Exception:
            pass

    # Capture basic page info
    html = await page.content()
    screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
    title = await page.title()
    url = page.url

    # Extract interactive elements
    if use_cdp:
        elements = await _extract_elements_enhanced(page)
    else:
        elements = await _extract_elements_basic(page)

    # Filter and prioritize
    elements = _filter_and_dedupe(elements, filter_ads=filter_ads)
    elements = sort_elements_by_priority(elements, task=task, max_elements=max_elements)

    # Generate optimized markdown
    sanitized = _sanitize_html(html, filter_ads=filter_ads)
    markdown = _generate_llm_markdown(sanitized, elements, max_elements)

    # Get viewport info for priority calculation
    viewport = page.viewport_size or {"width": 1280, "height": 720}

    return {
        "meta": {
            "url": url,
            "title": title,
            "timestamp": datetime.now(UTC).isoformat(),
            "viewport": viewport,
            "element_count": len(elements),
        },
        "markdown": markdown,
        "elements": elements,
        "screenshot_bytes": screenshot_bytes,
        "html": html,
        "title": title,
        "url": url,
    }


async def _extract_elements_enhanced(page: Page) -> list[dict[str, Any]]:
    """Extract interactive elements using CDP when available."""
    try:
        # Use CDP for enhanced detection
        async with CDPService(page) as cdp:
            # Try to get AX tree for semantic understanding
            ax_tree = await cdp.get_ax_tree()

            # Fall back to JavaScript extraction if AX tree unavailable
            if ax_tree:
                elements = await _extract_from_ax_tree(ax_tree, page)
            else:
                elements = await extract_interactive_elements(page)

    except Exception as exc:
        logger.debug("CDP extraction failed, using basic: %s", exc)
        elements = await _extract_elements_basic(page)

    return elements


async def _extract_elements_basic(page: Page) -> list[dict[str, Any]]:
    """Extract interactive elements using JavaScript only."""
    try:
        elements = await extract_interactive_elements(page)
    except Exception as exc:
        logger.warning("Basic element extraction failed: %s", exc)
        elements = []
    return elements


async def _extract_from_ax_tree(ax_tree: dict[str, Any], page: Page) -> list[dict[str, Any]]:
    """Extract interactive elements from Chrome AX Tree.

    The AX Tree provides semantic information about interactive elements
    that JavaScript extraction might miss.
    """
    elements = []
    nodes = ax_tree.get("nodes", [])

    for node in nodes:
        role = node.get("role", {}).get("value", "").lower()

        # Only process interactive roles
        if role not in {
            "button",
            "link",
            "textbox",
            "searchbox",
            "combobox",
            "listbox",
            "menuitem",
            "radio",
            "checkbox",
            "slider",
            "tab",
        }:
            continue

        # Extract element properties
        backend_id = node.get("backendDOMNodeId")
        if not backend_id:
            continue

        # Get bounding box
        try:
            rect = await page.evaluate(
                """(id) => {
                    const node = document.querySelector(`[data-node-id="${id}"]`);
                    if (node) {
                        const r = node.getBoundingClientRect();
                        return {x: r.left, y: r.top, width: r.width, height: r.height};
                    }
                    return {x: 0, y: 0, width: 0, height: 0};
                }""",
                backend_id,
            )
        except Exception:
            rect = {"x": 0, "y": 0, "width": 0, "height": 0}

        elements.append(
            {
                "tag": role,
                "text": node.get("name", {}).get("value", ""),
                "attrs": {
                    "role": role,
                    "aria-label": node.get("name", {}).get("value", ""),
                },
                "bbox": rect,
                "is_visible": True,
            }
        )

    return elements


def _filter_and_dedupe(
    raw: list[dict[str, Any]], *, filter_ads: bool = True
) -> list[dict[str, Any]]:
    """Filter out ads and deduplicate elements."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for e in raw:
        attrs = e.get("attrs") or {}
        cls = attrs.get("class", "")
        if isinstance(cls, (list, tuple)):
            cls = " ".join(str(x) for x in cls if x is not None)

        txt = e.get("text") or ""

        # Filter ads
        if filter_ads:
            try:
                if AD_REGEX.search(str(cls)) or AD_REGEX.search(str(txt)):
                    continue
            except Exception:
                pass

        # Dedupe by signature
        sig = "|".join(
            [
                str(e.get("tag", "")),
                str(attrs.get("id", "")),
                str(attrs.get("name", "")),
                txt[:80],
                str(e.get("css_path", "")),
            ]
        )

        if sig in seen:
            continue

        seen.add(sig)
        out.append(e)

    return out


def _remove_ad_containers(soup: BeautifulSoup) -> None:
    """Decompose ad/tracker containers matched by id/class against AD_REGEX."""
    to_remove = []
    for node in list(soup.find_all(True)):
        if not isinstance(node, Tag):
            continue

        nid = str(node.get("id", "") or "")
        cls_raw: Any = node.get("class") or []
        cls = " ".join(cls_raw) if isinstance(cls_raw, list) else str(cls_raw)

        try:
            if AD_REGEX.search(nid) or AD_REGEX.search(cls):
                to_remove.append(node)
        except Exception:
            pass

    for node in to_remove:
        try:
            node.decompose()
        except Exception:
            pass


def _sanitize_html(html: str, *, filter_ads: bool = True) -> str:
    """Remove noise elements from HTML."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # Remove noise tags
    for tag in soup.find_all(["script", "style", "noscript", "template", "link"]):
        try:
            tag.decompose()
        except Exception:
            pass

    if filter_ads:
        _remove_ad_containers(soup)

    return str(soup)


def _list_lines(tag: Tag) -> list[str]:
    """Markdown lines for a top-level ``ul``/``ol``."""
    lines = []
    for li in tag.find_all("li", recursive=False):
        text = li.get_text(separator=" ", strip=True)
        if text:
            lines.append(f"- {text}\n")
    return lines


def _structural_lines(tag: Tag) -> list[str]:
    """Markdown lines for key children of a structural container."""
    lines = []
    for p in tag.find_all(["h1", "h2", "h3", "p"], recursive=False):
        t = p.get_text(separator=" ", strip=True)
        if t:
            lines.append(t + "\n")
    return lines


def _page_structure_lines(soup: BeautifulSoup) -> list[str]:
    """Markdown lines for the page title and top-level body structure."""
    parts: list[str] = []

    # Page title
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    if title:
        parts.append(f"# {title}\n")

    # Main content structure
    body = soup.body or soup
    for tag in body.find_all(recursive=False):
        name = (tag.name or "").lower()
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            parts.append(f"{'#' * int(name[1])} {tag.get_text(strip=True)}\n")
        elif name == "p":
            text = tag.get_text(separator=" ", strip=True)
            if text:
                parts.append(text + "\n")
        elif name in ("ul", "ol"):
            parts.extend(_list_lines(tag))
        elif name in ("section", "main", "div", "form"):
            parts.extend(_structural_lines(tag))

    return parts


def _interactive_controls_lines(elements: list[dict[str, Any]], max_elements: int) -> list[str]:
    """Markdown lines listing the top prioritized interactive elements."""
    parts = ["\n## Interactive Controls\n"]

    shown_count = 0
    for e in elements[:max_elements]:
        shown_count += 1
        label = _extract_element_label(e)
        priority = e.get("_priority", 0)
        css_path = e.get("css_path", "unknown")

        parts.append(
            f"- {label} `[e{shown_count}]` (priority: {priority:.0f})  \n  selector: `{css_path}`\n"
        )

    # Add ellipsis for remaining elements
    remaining = len(elements) - shown_count
    if remaining > 0:
        parts.append(f"\n*... and {remaining} more interactive element(s)*\n")

    return parts


def _generate_llm_markdown(
    html: str, elements: list[dict[str, Any]], max_elements: int = 50
) -> str:
    """Generate optimized markdown for LLM consumption.

    Shows:
    1. Page title and structure
    2. Top N interactive elements (prioritized)
    3. Ellipsis for remaining elements

    This format reduces token usage while maintaining actionable information.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    parts = _page_structure_lines(soup)
    parts.extend(_interactive_controls_lines(elements, max_elements))
    return "\n".join(parts)


def _extract_element_label(element: dict[str, Any]) -> str:
    """Extract meaningful label for element display."""
    attrs_value = element.get("attrs", {})
    attrs = attrs_value if isinstance(attrs_value, dict) else {}

    # Try ARIA attributes first
    for key in ["aria-label", "aria-placeholder", "placeholder"]:
        if attrs.get(key):
            return str(attrs[key])

    # Try text content
    text_value = element.get("text", "")
    text = text_value.strip() if isinstance(text_value, str) else str(text_value).strip()
    if text:
        return text[:60]

    # Try title attribute
    title = attrs.get("title", "")
    if title:
        return str(title)

    # Fallback to tag name
    tag = str(element.get("tag", ""))
    if tag == "input":
        input_type = attrs.get("type", "")
        return f"{input_type} input" if input_type else "input"
    return tag

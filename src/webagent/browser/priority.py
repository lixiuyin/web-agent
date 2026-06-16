"""Priority calculation for interactive elements.

This module provides intelligent element prioritization based on multiple
factors to help the LLM focus on the most relevant elements first.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("webagent")


def calculate_priority(
    element: dict[str, Any],
    task: str = "",
    viewport_height: int = 720,
    viewport_width: int = 1280,
) -> float:
    """Calculate priority score for an interactive element.

    Priority is calculated from multiple factors:
    1. Position score: Elements higher on page get higher scores
    2. Element type score: Inputs and buttons are prioritized
    3. Visibility score: Visible elements score higher
    4. Task relevance score: Matching task keywords boosts score
    5. Size score: Reasonable sized elements score higher

    Args:
        element: Element dict with tag, attrs, text, bbox
        task: User's task description for relevance matching
        viewport_height: Viewport height for position scoring
        viewport_width: Viewport width for position scoring

    Returns:
        Priority score from 0-100 (higher = more important)
    """
    score = 0.0

    # 1. Position score (viewport top-left = higher score)
    bbox = element.get("bbox", {})
    x_pos = bbox.get("x", 99999)
    y_pos = bbox.get("y", 99999)

    if y_pos < viewport_height:
        # In viewport: higher score for elements near top
        position_score = max(0, 25 - (y_pos / viewport_height) * 25)
        score += position_score
    else:
        # Below fold: lower base score
        score += 3

    # Horizontal position (left side is more prominent)
    if x_pos < viewport_width:
        horizontal_score = max(0, 10 - (x_pos / viewport_width) * 10)
        score += horizontal_score

    # 2. Element type score
    tag = element.get("tag", "").lower()
    type_scores = {
        "input": 35,  # Search/entry boxes are most important
        "textarea": 30,  # Text areas
        "select": 28,  # Dropdowns
        "button": 25,  # Buttons
        "a": 15,  # Links
    }
    score += type_scores.get(tag, 8)

    # 3. Size score (elements too small or too large get lower scores)
    width = bbox.get("width", 0)
    height = bbox.get("height", 0)
    area = width * height

    # Prefer reasonable sized elements (100px to 50000px area)
    if 100 < area < 50000:
        score += 5
    elif area > 0:
        score += 2

    # 4. Visibility score
    if element.get("is_visible", True):
        score += 10

    # 5. Text content quality score
    text = element.get("text", "").strip()
    if text:
        # Meaningful text (not single character or very long)
        if 2 < len(text) < 100:
            score += 8
        elif len(text) >= 2:
            score += 4

    # 6. Task relevance score
    if task:
        task_lower = task.lower()
        label = _extract_label(element).lower()
        task_words = set(re.findall(r"\w+", task_lower))

        # Count matching words
        matches = sum(1 for word in task_words if len(word) > 2 and word in label)
        if matches > 0:
            score += min(25, matches * 8)

    # 7. Attribute-based priority
    attrs = element.get("attrs", {})

    # Priority classes/IDs
    priority_indicators = ["search", "submit", "primary", "main", "important"]
    for key in ["id", "class"]:
        value = str(attrs.get(key, "")).lower()
        if any(indicator in value for indicator in priority_indicators):
            score += 10
            break

    # Negative indicators (navigation, footer, etc.)
    negative_indicators = ["footer", "header", "nav", "sidebar", "cookie"]
    for key in ["id", "class", "role"]:
        value = str(attrs.get(key, "")).lower()
        if any(indicator in value for indicator in negative_indicators):
            score -= 5
            break

    return max(0, min(100, score))


def _extract_label(element: dict[str, Any]) -> str:
    """Extract a meaningful label from element.

    Priority order:
    1. ARIA label or placeholder
    2. Text content
    3. Title attribute
    4. Tag name fallback
    """
    attrs = element.get("attrs", {})

    # Try ARIA attributes first
    for key in ["aria-label", "aria-placeholder", "placeholder"]:
        if attrs.get(key):
            return str(attrs[key])

    # Try text content
    text = element.get("text", "").strip()
    if text:
        return text[:100]

    # Try title attribute
    title = attrs.get("title", "")
    if title:
        return str(title)

    # Fallback to tag name
    tag = element.get("tag", "")
    if tag == "input":
        input_type = attrs.get("type", "")
        return f"{input_type} input" if input_type else "input"
    return tag


def sort_elements_by_priority(
    elements: list[dict[str, Any]],
    task: str = "",
    viewport_height: int = 720,
    viewport_width: int = 1280,
    max_elements: int = 50,
) -> list[dict[str, Any]]:
    """Sort elements by priority and limit to max_elements.

    Args:
        elements: List of element dicts
        task: User's task for relevance matching
        viewport_height: Viewport height for position scoring
        viewport_width: Viewport width for position scoring
        max_elements: Maximum number of elements to return

    Returns:
        Sorted and limited list of elements
    """
    # Calculate priority for each element without mutating the caller's dicts
    # (immutability rule — element dicts may be reused across snapshots).
    scored = [
        dict(
            element,
            _priority=calculate_priority(
                element, task=task, viewport_height=viewport_height, viewport_width=viewport_width
            ),
        )
        for element in elements
    ]

    # Sort by priority (descending) and limit to max_elements
    scored.sort(key=lambda e: e.get("_priority", 0), reverse=True)
    return scored[:max_elements]


def get_top_elements_summary(
    elements: list[dict[str, Any]],
    shown_count: int = 10,
) -> str:
    """Generate a summary string for elements not shown.

    Args:
        elements: List of elements that were not shown
        shown_count: Number of elements already shown

    Returns:
        Summary string like "and 15 more elements"
    """
    remaining = len(elements)
    if remaining == 0:
        return ""
    return f"... and {remaining} more element(s)"

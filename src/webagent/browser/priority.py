"""Priority calculation for interactive elements.

This module provides intelligent element prioritization based on multiple
factors to help the LLM focus on the most relevant elements first.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("webagent")


# Tag-based scores: search/entry boxes matter most, links least.
_TAG_SCORES = {
    "input": 35,
    "textarea": 30,
    "select": 28,
    "button": 25,
    "a": 15,
}
_DEFAULT_TAG_SCORE = 8

# id/class substrings that mark a primary interactive element.
_PRIORITY_INDICATORS = ("search", "submit", "primary", "main", "important")
# id/class/role substrings that mark chrome (navigation, footer, etc.).
_NEGATIVE_INDICATORS = ("footer", "header", "nav", "sidebar", "cookie")


def _position_score(element: dict[str, Any], viewport_height: int, viewport_width: int) -> float:
    """Vertical position dominates; left edge is slightly more prominent."""
    bbox = element.get("bbox", {})
    x_pos = bbox.get("x", 99999)
    y_pos = bbox.get("y", 99999)
    score = 0.0

    if y_pos < viewport_height:
        # In viewport: higher score for elements near top
        score += max(0, 25 - (y_pos / viewport_height) * 25)
    else:
        # Below fold: lower base score
        score += 3

    if x_pos < viewport_width:
        score += max(0, 10 - (x_pos / viewport_width) * 10)
    return score


def _size_score(element: dict[str, Any]) -> float:
    """Prefer reasonably sized elements (100px to 50000px area)."""
    bbox = element.get("bbox", {})
    area = bbox.get("width", 0) * bbox.get("height", 0)
    if 100 < area < 50000:
        return 5
    if area > 0:
        return 2
    return 0


def _text_score(element: dict[str, Any]) -> float:
    """Meaningful text (not single character or very long) scores higher."""
    text = element.get("text", "").strip()
    if not text:
        return 0
    if 2 < len(text) < 100:
        return 8
    if len(text) >= 2:
        return 4
    return 0


def _task_relevance_score(element: dict[str, Any], task: str) -> float:
    """Boost elements whose label contains task keywords."""
    if not task:
        return 0
    label = _extract_label(element).lower()
    task_words = set(re.findall(r"\w+", task.lower()))
    matches = sum(1 for word in task_words if len(word) > 2 and word in label)
    if matches > 0:
        return min(25, matches * 8)
    return 0


def _attribute_score(element: dict[str, Any]) -> float:
    """Score id/class/role indicator substrings (positive then negative)."""
    attrs_value = element.get("attrs", {})
    attrs = attrs_value if isinstance(attrs_value, dict) else {}
    score = 0.0

    for key in ("id", "class"):
        value = str(attrs.get(key, "")).lower()
        if any(indicator in value for indicator in _PRIORITY_INDICATORS):
            score += 10
            break

    for key in ("id", "class", "role"):
        value = str(attrs.get(key, "")).lower()
        if any(indicator in value for indicator in _NEGATIVE_INDICATORS):
            score -= 5
            break

    return score


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
    tag = element.get("tag", "").lower()

    score = _position_score(element, viewport_height, viewport_width)
    score += _TAG_SCORES.get(tag, _DEFAULT_TAG_SCORE)
    score += _size_score(element)
    if element.get("is_visible", True):
        score += 10
    score += _text_score(element)
    score += _task_relevance_score(element, task)
    score += _attribute_score(element)

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
    text_value = element.get("text", "")
    text = text_value.strip() if isinstance(text_value, str) else str(text_value).strip()
    if text:
        return text[:100]

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

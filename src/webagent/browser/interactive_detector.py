"""Interactive element detection with intelligent filtering.

Inspired by browser-use's candidateFilter logic, this module detects
interactive elements on a page while filtering out ads, trackers, and noise.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger("webagent")

# Patterns that indicate ad/tracker content
AD_PATTERNS = re.compile(
    r"\b(ad|ads|sponsor|banner|promo|tracking|cookie|subscribe|"
    r"advertisement|affiliate|partner|banner-ad)\b",
    re.IGNORECASE,
)

# Button-like class patterns (indicates interactive elements)
BUTTON_CLASS_PATTERNS = re.compile(
    r"\b(btn|button|link|toggle|submit|close|menu|nav|action|click|"
    r"select|choose|option|tab|dropdown|collapse|expand|modal)\b",
    re.IGNORECASE,
)

# Interactive tags that are always considered
INTERACTIVE_TAGS = {
    "button",
    "input",
    "textarea",
    "select",
    "option",
    "a",
    "summary",
    "details",
    "label",
}

# ARIA roles that indicate interactivity
INTERACTIVE_ROLES = {
    "button",
    "link",
    "textbox",
    "searchbox",
    "combobox",
    "listbox",
    "menuitem",
    "menuitemcheckbox",
    "menuitemradio",
    "radio",
    "checkbox",
    "switch",
    "slider",
    "tab",
    "tabpanel",
    "grid",
    "gridcell",
    "option",
    "scrollbar",
    "spinbutton",
    "treeitem",
}


class InteractiveElementDetector:
    """Detects interactive elements with intelligent filtering."""

    def __init__(
        self,
        filter_ads: bool = True,
        min_visibility: float = 0.1,
    ) -> None:
        """Initialize the detector.

        Args:
            filter_ads: Whether to filter out ad/tracker content
            min_visibility: Minimum opacity (0-1) for element to be visible
        """
        self.filter_ads = filter_ads
        self.min_visibility = min_visibility

    def is_interactive_tag(self, tag: str) -> bool:
        """Check if tag is inherently interactive."""
        return tag.lower() in INTERACTIVE_TAGS

    def is_interactive_role(self, role: str | None) -> bool:
        """Check if ARIA role indicates interactivity."""
        if not role:
            return False
        role_lower = role.lower()
        return role_lower in INTERACTIVE_ROLES

    def is_interactive_class(self, class_list: list[str] | str | None) -> bool:
        """Check if class names indicate interactivity."""
        if not class_list:
            return False
        if isinstance(class_list, str):
            class_list = class_list.split()
        return any(BUTTON_CLASS_PATTERNS.search(cls) for cls in class_list)

    def has_interactive_attribute(self, attributes: dict[str, Any]) -> bool:
        """Check if attributes indicate interactivity."""
        # Has onclick handler
        if "onclick" in attributes:
            return True
        # Is contenteditable
        if attributes.get("contenteditable") == "true":
            return True
        # Has role attribute with interactive role
        role = attributes.get("role")
        return bool(role and self.is_interactive_role(role))

    def is_ad_content(self, element: dict[str, Any]) -> bool:
        """Check if element is likely an ad or tracker."""
        if not self.filter_ads:
            return False

        # Check ID for ad patterns
        elem_id = element.get("attrs", {}).get("id", "")
        if AD_PATTERNS.search(elem_id):
            return True

        # Check class names
        classes = element.get("attrs", {}).get("class", "")
        if isinstance(classes, (list, tuple)):
            classes = " ".join(str(c) for c in classes if c)
        if AD_PATTERNS.search(classes):
            return True

        # Check text content
        text = element.get("text", "") or ""
        return bool(AD_PATTERNS.search(text))

    def is_visible(
        self,
        computed_style: dict[str, Any] | None,
        bbox: dict[str, Any] | None,
    ) -> bool:
        """Check if element is visible based on computed styles and bounding box.

        Args:
            computed_style: Computed CSS styles
            bbox: Bounding box with x, y, width, height

        Returns:
            True if element is visible
        """
        # Check computed styles first
        if computed_style:
            display = computed_style.get("display", "").lower()
            visibility = computed_style.get("visibility", "").lower()
            try:
                opacity = float(computed_style.get("opacity", "1"))
            except (ValueError, TypeError):
                opacity = 1.0

            if display == "none" or visibility == "hidden":
                return False
            if opacity < self.min_visibility:
                return False

        # Check bounding box
        if bbox:
            width = bbox.get("width", 0)
            height = bbox.get("height", 0)
            if width == 0 and height == 0:
                return False

        return True

    def should_include_element(self, element: dict[str, Any]) -> bool:
        """Determine if an element should be included in interactive elements.

        Args:
            element: Element data with tag, attrs, text, bbox, computed_style

        Returns:
            True if element should be included
        """
        # Filter ads
        if self.is_ad_content(element):
            return False

        # Check visibility
        if not element.get("is_visible", True):
            return False

        tag = element.get("tag", "").lower()

        # Check if inherently interactive
        if self.is_interactive_tag(tag):
            return True

        # Check ARIA role
        attrs = element.get("attrs", {})
        if self.is_interactive_role(attrs.get("role")):
            return True

        # Check class names
        classes = attrs.get("class", "")
        if self.is_interactive_class(classes):
            return True

        # Check for interactive attributes
        return bool(self.has_interactive_attribute(attrs))

    def extract_element_label(self, element: dict[str, Any]) -> str:
        """Extract a meaningful label from element.

        Priority order:
        1. ARIA label or accessible name
        2. Placeholder text
        3. Text content
        4. Title attribute
        5. Tag name fallback

        Args:
            element: Element data

        Returns:
            Extracted label text
        """
        attrs = element.get("attrs", {})

        # Try ARIA attributes first
        for key in ["aria-label", "aria-placeholder", "placeholder"]:
            if attrs.get(key):
                return str(attrs[key])

        # Try text content
        text = element.get("text", "").strip()
        if text:
            return text[:100]  # Limit length

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

    def calculate_relevance_score(
        self,
        element: dict[str, Any],
        task: str = "",
        viewport_height: int = 720,
    ) -> float:
        """Calculate relevance score for element prioritization.

        Args:
            element: Element data
            task: User's task for relevance matching
            viewport_height: Viewport height for position scoring

        Returns:
            Score from 0-100 (higher = more important)
        """
        score = 0.0

        # 1. Position score (viewport top = higher score)
        bbox = element.get("bbox", {})
        y_pos = bbox.get("y", 99999)
        if y_pos < viewport_height:
            # In viewport: higher score for elements near top
            score += max(0, 30 - (y_pos / viewport_height) * 30)
        else:
            # Below fold: lower base score
            score += 5

        # 2. Element type score
        tag = element.get("tag", "").lower()
        type_scores = {
            "input": 35,  # Search/entry boxes
            "button": 25,  # Buttons
            "a": 15,  # Links
            "select": 30,  # Dropdowns
            "textarea": 25,
        }
        score += type_scores.get(tag, 5)

        # 3. Visibility score (based on computed style)
        if element.get("is_visible", True):
            score += 10

        # 4. Task relevance score
        if task:
            label = self.extract_element_label(element).lower()
            task_lower = task.lower()
            task_words = set(task_lower.split())

            # Count matching words
            matches = sum(1 for word in task_words if word in label)
            if matches > 0:
                score += min(20, matches * 5)

        return min(100, score)


# JavaScript for extracting interactive elements from page
INTERACTIVE_ELEMENTS_JS = r"""
(() => {
    function isVisible(el) {
        try {
            const s = window.getComputedStyle(el);
            if (s.display === 'none' || s.visibility === 'hidden') {
                return false;
            }
            const opacity = parseFloat(s.opacity) || 1;
            if (opacity < 0.1) {
                return false;
            }
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) {
                return false;
            }
            return true;
        } catch(e) {
            return false;
        }
    }

    function isInteractive(el) {
        if (!(el instanceof Element)) return false;

        const tag = el.tagName.toLowerCase();

        // Always interactive tags
        if (['button', 'input', 'textarea', 'select', 'option', 'a'].includes(tag)) {
            if (tag === 'a' && !el.getAttribute('href') && !el.getAttribute('role')) {
                return false;
            }
            return isVisible(el);
        }

        // Check for onclick or contenteditable
        if (el.getAttribute('onclick') || el.getAttribute('contenteditable')) {
            return isVisible(el);
        }

        // Check role
        const role = el.getAttribute('role');
        const interactiveRoles = [
            'button', 'link', 'textbox', 'searchbox', 'combobox', 'listbox',
            'menuitem', 'radio', 'checkbox', 'switch', 'tab', 'grid'
        ];
        if (role && interactiveRoles.includes(role.toLowerCase())) {
            return isVisible(el);
        }

        // Check for button-like classes
        const className = el.className || '';
        const buttonPattern = /\b(btn|button|link|toggle|submit|click)\b/i;
        if (buttonPattern.test(className)) {
            return isVisible(el);
        }

        return false;
    }

    function getCSSPath(el) {
        if (!(el instanceof Element)) return '';
        const parts = [];
        while (el && el.nodeType === Node.ELEMENT_NODE) {
            let part = el.tagName.toLowerCase();
            if (el.id) {
                part += '#' + el.id;
                parts.unshift(part);
                break;
            }
            if (el.className && typeof el.className === 'string') {
                const classes = el.className.split(/\s+/).filter(Boolean).slice(0, 2);
                if (classes.length) {
                    part += '.' + classes.join('.');
                }
            }
            const parent = el.parentElement;
            if (parent) {
                const siblings = Array.from(parent.children).filter(e => e.tagName === el.tagName);
                if (siblings.length > 1) {
                    part += `:nth-of-type(${siblings.indexOf(el) + 1})`;
                }
            }
            parts.unshift(part);
            el = parent;
        }
        return parts.join(' > ');
    }

    function extractInteractiveElements() {
        const elements = [];
        const allElements = document.querySelectorAll('*');

        for (const el of allElements) {
            if (isInteractive(el)) {
                const rect = el.getBoundingClientRect();
                const attrs = {};

                // Extract important attributes
                for (const attr of ['id', 'name', 'type', 'placeholder', 'role',
                                      'href', 'title', 'aria-label', 'value', 'class']) {
                    const val = el.getAttribute(attr);
                    if (val) attrs[attr] = val;
                }

                // Get text content
                let text = '';
                if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
                    text = el.value || el.getAttribute('placeholder') || '';
                } else {
                    text = el.innerText || el.textContent || '';
                }

                elements.push({
                    tag: el.tagName.toLowerCase(),
                    text: text.trim().slice(0, 200),
                    attrs: attrs,
                    css_path: getCSSPath(el),
                    bbox: {
                        x: Math.round(rect.left + window.scrollX),
                        y: Math.round(rect.top + window.scrollY),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    },
                    is_visible: true
                });
            }
        }

        return elements;
    }

    return extractInteractiveElements();
})();
"""


async def extract_interactive_elements(page: Page) -> list[dict[str, Any]]:
    """Extract interactive elements from the page.

    Args:
        page: Playwright page object

    Returns:
        List of interactive element dictionaries
    """
    try:
        elements_raw = await page.evaluate(INTERACTIVE_ELEMENTS_JS)
        return elements_raw if isinstance(elements_raw, list) else []
    except Exception as e:
        logger.warning(f"Failed to extract interactive elements: {e}")
        return []

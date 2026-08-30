"""Interactive element detection with intelligent filtering.

Inspired by browser-use's candidateFilter logic, this module detects
interactive elements on a page while filtering out ads, trackers, and noise.
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger("webagent")


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
    except Exception as exc:
        logger.warning("Failed to extract interactive elements: %s", exc)
        return []

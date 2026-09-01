"""Captcha/challenge detection for web automation.

Provides lightweight captcha detection without external dependencies by
analyzing DOM patterns, page content, and known challenge indicators.
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger("webagent")


class CaptchaDetector:
    """Detects captcha/challenge pages by analyzing page content.

    This detector identifies common captcha types without requiring
    external solving services. When a captcha is detected, the agent
    can pause and notify the user for manual resolution.
    """

    # CSS selectors for known captcha systems
    CAPTCHA_PATTERNS: dict[str, list[str]] = {
        "recaptcha": [
            'iframe[src*="recaptcha"]',
            "div.g-recaptcha",
            ".recaptcha-checkbox",
            "#recaptcha-anchor",
            "[data-sitekey]",  # reCAPTCHA v3 marker
        ],
        "hcaptcha": [
            'iframe[src*="hcaptcha"]',
            ".h-captcha",
            "#h-captcha",
            "[data-hcaptcha]",
        ],
        "cloudflare": [
            "div.cf-browser-verification",
            ".cf-challenge",
            "#cf-challenge-form",
            'iframe[src*="challenges.cloudflare"]',
        ],
        "image_captcha": [
            'input[name="captcha"]',
            'input[name="captcha_code"]',
            "#captcha",
            ".captcha",
            'img[alt*="captcha" i]',
            'img[src*="captcha" i]',
        ],
        "fun_captcha": [
            'iframe[src*="funcaptcha"]',
            ".funcaptcha",
            "#FunCaptcha-Token",
        ],
        "arkose": [
            'iframe[src*="arkose"]',
            ".arkose-captcha",
            "#arkose",
        ],
    }

    # Keywords that may appear in page title or URL when captcha is present
    CAPTCHA_KEYWORDS = [
        "captcha",
        "challenge",
        "verification",
        "verify you are human",
        "are you human",
        "security check",
        "human verification",
        "prove you're human",
        "bot check",
        "i'm not a robot",
    ]

    async def detect_captcha(self, page: Page) -> dict[str, Any]:
        """Scan page for captcha indicators.

        Args:
            page: The Playwright page to analyze.

        Returns:
            Dictionary with detection results:
                - detected (bool): Whether captcha was detected
                - type (str): Type of captcha (recaptcha, hcaptcha, etc.)
                - confidence (float): Detection confidence (0.0-1.0)
                - reason (str): Human-readable explanation
                - selectors (list[str]): Matching CSS selectors
        """
        url = page.url.lower()
        title = await page.title() if page else ""
        title_lower = title.lower()

        # Check URL and title for captcha keywords
        keyword_matches = [
            keyword for keyword in self.CAPTCHA_KEYWORDS if keyword in url or keyword in title_lower
        ]

        # Google's automated-query challenge uses a stable ``/sorry/`` route
        # whose title does not necessarily mention a captcha or verification.
        if "google." in url and "/sorry/" in url:
            return {
                "detected": True,
                "type": "google_unusual_traffic",
                "confidence": 0.95,
                "reason": "Detected Google's unusual-traffic challenge URL",
                "selectors": [],
            }

        # DuckDuckGo returns its bot challenge as an HTTP 202 page whose title
        # resembles an ordinary result page and whose DOM has no standard
        # CAPTCHA widget. Its explicit body message is the reliable signal.
        if "duckduckgo.com" in url:
            try:
                body = (await page.inner_text("body")).casefold()
            except Exception:
                body = ""
            if "bots use duckduckgo" in body:
                return {
                    "detected": True,
                    "type": "duckduckgo_bot_challenge",
                    "confidence": 0.95,
                    "reason": "Detected DuckDuckGo bot-challenge message",
                    "selectors": [],
                }

        # Check DOM for known captcha patterns
        dom_matches: dict[str, list[str]] = {}
        for captcha_type, selectors in self.CAPTCHA_PATTERNS.items():
            matching_selectors = []
            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element is not None and await element.is_visible():
                        matching_selectors.append(selector)
                except Exception:
                    # Selector might be invalid, skip it
                    pass

            if matching_selectors:
                dom_matches[captcha_type] = matching_selectors

        # Determine detection result
        if dom_matches:
            # Found DOM patterns - highest confidence
            captcha_type = next(iter(dom_matches))
            return {
                "detected": True,
                "type": captcha_type,
                "confidence": 0.9,
                "reason": f"Detected {captcha_type} via DOM patterns",
                "selectors": dom_matches[captcha_type],
            }

        if keyword_matches:
            # Found keywords but no DOM patterns - medium confidence
            return {
                "detected": True,
                "type": "unknown",
                "confidence": 0.5,
                "reason": f"Detected captcha via keywords: {', '.join(keyword_matches)}",
                "selectors": [],
            }

        # No captcha detected
        return {
            "detected": False,
            "type": None,
            "confidence": 0.0,
            "reason": "No captcha indicators found",
            "selectors": [],
        }


async def check_captcha(page: Page) -> dict[str, Any]:
    """Convenience function to check a page for captcha.

    Args:
        page: The Playwright page to analyze.

    Returns:
        Dictionary with detection results.
    """
    detector = CaptchaDetector()
    return await detector.detect_captcha(page)

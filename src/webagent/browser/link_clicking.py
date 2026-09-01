"""Fallback strategies for clicking a link identified by its visible text.

Each strategy takes a Playwright page and the target text, tries one matching
approach, and returns a success dict (same shape as the controller's action
results) or ``None`` when it cannot match anything. The controller walks the
strategies in order until one succeeds.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from webagent.browser.search_parsers import extract_keyword_words

# Terms that trigger the generic PDF/download link fallback.
_DOWNLOAD_TERMS = ("pdf", "view", "download")


async def click_by_exact_text(page: Page, text: str) -> dict[str, Any] | None:
    """Strategy 1: exact text selector (fastest, most precise)."""
    selector = f'text="{text}"'
    try:
        await page.click(selector, timeout=5000, force=False)
        return {"success": True, "selector": selector, "method": "exact"}
    except Exception:
        return None


async def click_by_fuzzy_text(page: Page, text: str) -> dict[str, Any] | None:
    """Strategy 2: Playwright substring text matcher."""
    try:
        element = page.get_by_text(text, exact=False).first
        await element.click(timeout=5000)
        return {
            "success": True,
            "selector": f"get_by_text({text}, exact=False)",
            "method": "fuzzy",
        }
    except Exception:
        return None


async def click_by_keyword_match(page: Page, text: str) -> dict[str, Any] | None:
    """Strategy 3: click the first link whose text contains >= 2 query words."""
    words_to_search = extract_keyword_words(text)
    try:
        links = await page.query_selector_all("a")
        for link in links:
            try:
                link_text = await link.inner_text() or ""
                match_count = sum(
                    1 for word in words_to_search if word.lower() in link_text.lower()
                )
                if match_count >= 2:  # At least 2 words should match
                    await link.click(timeout=5000)
                    return {
                        "success": True,
                        "selector": f"link_by_text: {text}",
                        "found_text": link_text.strip()[:100],
                        "method": "keyword_match",
                    }
            except Exception:
                continue
    except Exception:
        pass
    return None


async def click_by_identifier(page: Page, text: str) -> dict[str, Any] | None:
    """Strategy 4: match arXiv IDs or DOIs embedded in the text against hrefs."""
    arxiv_match = re.search(r"\d{4}\.\d+", text)
    if not arxiv_match:
        return None
    arxiv_id = arxiv_match.group(0)

    try:
        links = await page.query_selector_all("a")
        for link in links:
            try:
                href = await link.get_attribute("href") or ""
                if arxiv_id in href:
                    await link.click(timeout=5000)
                    return {
                        "success": True,
                        "selector": f"link_by_arxiv_id: {arxiv_id}",
                        "found_href": href[:100],
                        "method": "url_match",
                    }
            except Exception:
                continue
    except Exception:
        pass
    return None


async def click_pdf_link(page: Page, text: str) -> dict[str, Any] | None:
    """Strategy 5: for PDF-ish queries, click the first link with 'pdf' in its URL."""
    if not any(term in text.lower() for term in _DOWNLOAD_TERMS):
        return None
    try:
        links = await page.query_selector_all("a")
        for link in links:
            try:
                href = await link.get_attribute("href") or ""
                if "pdf" in href.lower():
                    await link.click(timeout=5000)
                    return {
                        "success": True,
                        "selector": "link_by_pdf_url",
                        "found_href": href[:100],
                        "method": "pdf_url_fallback",
                    }
            except Exception:
                continue
    except Exception:
        pass
    return None


async def click_link_by_text_strategies(page: Page, text: str, fuzzy: bool) -> dict[str, Any]:
    """Run all link-clicking strategies in order and return the first success.

    This is the behavior previously inlined in
    ``BrowserController.click_link_by_text``; kept here so each strategy can be
    unit-tested against a fake page.
    """
    strategies = [click_by_exact_text]
    if fuzzy:
        strategies.extend(
            [click_by_fuzzy_text, click_by_keyword_match, click_by_identifier, click_pdf_link]
        )

    for strategy in strategies:
        result = await strategy(page, text)
        if result is not None and result.get("success"):
            return result

    if not fuzzy:
        return {"success": False, "error": f"No link found with text: {text}"}
    return {
        "success": False,
        "error": f"No link found matching: {text}",
        "tried_methods": ["exact", "fuzzy", "keyword_match", "url_match"],
    }

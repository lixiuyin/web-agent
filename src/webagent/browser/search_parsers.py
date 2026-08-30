"""Per-engine search result extraction for major search engines.

Each parser takes a Playwright page currently showing search results and
returns a normalized list of ``{"title", "link", "snippet"}`` dicts. Keeping
the engine-specific selectors here (out of the controller) makes them
independently testable against fake pages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from playwright.async_api import ElementHandle, Page

# Google snippet containers seen across layout generations.
_GOOGLE_SNIPPET_SELECTORS = "[style*='-webkit-line-clamp'], .VwiC3b, .IsZvec"
# Common English words ignored when keyword-matching links against link text.
_KEYWORD_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "with",
    }
)


def detect_search_engine(url: str) -> str | None:
    """Return the engine id for a search results URL, or None if unrecognized."""
    lower = url.lower()
    if "google.com" in lower or "google." in lower:
        return "google"
    if "bing.com" in lower:
        return "bing"
    if "duckduckgo.com" in lower:
        return "duckduckgo"
    return None


async def parse_google_results(page: Page, max_results: int) -> list[dict[str, Any]]:
    """Extract organic results from a Google results page.

    Tries the classic ``div.g`` containers first, then falls back to
    ``div[data-hveid]`` which also matches navigation tabs; internal Google
    links are filtered out either way.
    """
    results: list[dict[str, Any]] = []
    elements = await page.query_selector_all("div.g")
    if not elements:
        elements = await page.query_selector_all("div[data-hveid]")

    # Over-fetch to account for filtering below.
    for element in elements[: max_results * 2]:
        try:
            link_el = await element.query_selector("a")
            if not link_el:
                continue

            href = await link_el.get_attribute("href")
            title = await link_el.text_content()

            # Skip Google-internal navigation links (tabs, redirects)
            if not href or not href.startswith("http"):
                continue
            if "google.com" in href:
                continue

            snippet_el = await element.query_selector(_GOOGLE_SNIPPET_SELECTORS)
            snippet = await snippet_el.text_content() if snippet_el else ""

            title_clean = (title or "").strip()
            if title_clean:
                results.append(
                    {
                        "title": title_clean,
                        "link": href,
                        "snippet": (snippet or "").strip(),
                    }
                )
            if len(results) >= max_results:
                break
        except Exception:
            continue

    return results


async def parse_bing_results(page: Page, max_results: int) -> list[dict[str, Any]]:
    """Extract organic results from a Bing results page (``li.b_algo`` rows)."""
    results: list[dict[str, Any]] = []
    elements = await page.query_selector_all("li.b_algo")

    for element in elements[:max_results]:
        try:
            link_el = await element.query_selector("h2 a")
            if not link_el:
                continue

            href = await link_el.get_attribute("href")
            title = await link_el.text_content()

            snippet_el = await element.query_selector("p")
            snippet = await snippet_el.text_content() if snippet_el else ""

            if href and title:
                results.append(
                    {
                        "title": (title or "").strip(),
                        "link": href,
                        "snippet": (snippet or "").strip(),
                    }
                )
        except Exception:
            continue

    if results:
        return results

    # Bing occasionally renders a visually normal results area without the
    # traditional ``li.b_algo > h2 > a`` hierarchy. Stay inside the results
    # region (never the global nav) and recover visible titled links.
    links = await page.query_selector_all("#b_results h2 a, #b_results a[href], main h2 a")
    seen: set[str] = set()
    for link_el in links:
        try:
            href = await link_el.get_attribute("href")
            title = (await link_el.text_content() or "").strip()
            if not href or not href.startswith("http") or not title or href in seen:
                continue
            seen.add(href)
            results.append({"title": title, "link": href, "snippet": ""})
            if len(results) >= max_results:
                break
        except Exception:
            continue

    return results


async def _parse_ddg_element(element: ElementHandle) -> dict[str, Any] | None:
    """Parse one DuckDuckGo result container into a normalized entry."""
    link_el: ElementHandle | None = element
    if await element.evaluate("el => el.tagName") != "A":
        link_el = await element.query_selector("a[href]")
    if not link_el:
        return None

    href = await link_el.get_attribute("href")
    if not href or href.startswith("/") or "duckduckgo.com" in href:
        # Skip internal links
        return None

    title = await link_el.text_content() or await link_el.get_attribute("title") or ""
    title = title.strip()

    snippet = await _ddg_snippet(element, link_el)

    if title and href and href.startswith("http"):
        return {"title": title, "link": href, "snippet": snippet}
    return None


async def parse_duckduckgo_results(page: Page, max_results: int) -> list[dict[str, Any]]:
    """Extract organic results from a DuckDuckGo results page.

    Tries modern ``article.result`` containers, then legacy
    ``div.web-result``, then all links inside the main content area.
    """
    results: list[dict[str, Any]] = []
    elements = await page.query_selector_all("article.result")
    if not elements:
        elements = await page.query_selector_all("div.web-result")
    if not elements:
        main_content = await page.query_selector("main#content__main, #links, .results")
        if main_content:
            elements = await main_content.query_selector_all("a")

    for element in elements[:max_results]:
        try:
            entry = await _parse_ddg_element(element)
        except Exception:
            continue
        if entry is not None:
            results.append(entry)

    return results


async def _ddg_snippet(element: Any, link_el: Any) -> str:
    """Find the snippet text near a DuckDuckGo result link."""
    try:
        parent = await link_el.evaluate("el => el.parentElement")
        if parent:
            snippet_el = await element.query_selector(".result__snippet, .snippet, p")
            if snippet_el:
                return (await snippet_el.text_content() or "").strip()
    except Exception:
        pass
    return ""


SEARCH_PARSERS: dict[str, Callable[[Page, int], Awaitable[list[dict[str, Any]]]]] = {
    "google": parse_google_results,
    "bing": parse_bing_results,
    "duckduckgo": parse_duckduckgo_results,
}


def extract_keyword_words(text: str) -> list[str]:
    """Extract meaningful words (>3 chars, non-stop-words) from link text."""
    return [w for w in text.split() if len(w) > 3 and w.lower() not in _KEYWORD_STOP_WORDS]

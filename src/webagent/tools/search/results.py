"""Browser SERP readiness checks and normalized result extraction."""

from __future__ import annotations

from typing import Any

from webagent.browser.url_identity import search_engine_for_url, web_url
from webagent.tools.search.support import (
    _SEARCH_CHALLENGE_URL_MARKERS,
    _SEARCH_ERROR_MARKERS,
    _unwrap_search_redirect,
)


def _organic_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Engine tabs repeat the query but are not independent search evidence."""
    organic = []
    for item in results:
        url = _unwrap_search_redirect(item.get("url", ""))
        parsed = web_url(url)
        if parsed is None:
            continue
        if search_engine_for_url(url) and parsed.path.startswith(
            ("/search", "/image", "/video", "/copilotsearch", "/ck/")
        ):
            continue
        organic.append({**item, "url": url})
    return organic


async def results_present(browser: Any, config: dict[str, str]) -> bool:
    """Return True only if the current page actually shows search results.

    Guards against engines returning an error / zero-results / bot-block page
    (which lacks the results selector) being reported as a successful search.
    """
    try:
        page = browser.page
        body = (await page.inner_text("body")).lower()
        parsed = web_url(page.url)
        current_url = parsed.path.casefold() if parsed is not None else ""
        if any(marker in current_url for marker in _SEARCH_CHALLENGE_URL_MARKERS):
            return False
        if any(marker in body for marker in _SEARCH_ERROR_MARKERS):
            return False
        if await page.locator(config["wait_selector"]).count() > 0:
            return True
        # Generic fallback: a real results page links out to many sites.
        return bool(await page.locator("a[href^='http']").count() > 5)
    except Exception:
        return False


async def extract_results(browser: Any, limit: int = 10) -> list[dict[str, str]]:
    """Extract top SERP result items (title, url, date) for recency comparison.

    Engine-agnostic and best-effort: reads the result containers common to
    Google/Bing/DuckDuckGo and pulls each result's heading link plus any
    date-like text in its block. Never raises — a failed extraction returns
    an empty list, which the caller treats as an unusable search page.
    """
    js = r"""
    (limit) => {
      const out = [];
      const seen = new Set();
      const containers = [
        'li.b_algo', 'div.b_algo',           // Bing
        'div.g', 'div.MjjYud',               // Google
        'article[data-testid="result"]',     // DuckDuckGo
        'div.result',                        // DuckDuckGo legacy
        'div#web div.algo-sr',               // Yahoo
        'a.sw-Card__titleInner',              // Yahoo Japan
        'a[data-e-a="heading"]',             // Seznam
      ];
      const datePatterns = [
        /\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b/i,
        /\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b/,
        /\b\d+\s+(?:day|days|hour|hours|week|weeks|month|months|year|years)\s+ago\b/i,
      ];
      for (const sel of containers) {
        for (const block of document.querySelectorAll(sel)) {
          const a = block.matches('a[href^="http"]')
            ? block
            : block.querySelector('a[data-testid="result-title-a"][href^="http"]')
              || block.querySelector('h2 a[href^="http"]')
              || block.querySelector('a[href^="http"]');
          if (!a) continue;
          let title = (a.innerText || a.getAttribute('title') || '').trim();
          if (a.matches('a.sw-Card__titleInner')) {
            title = title.split('\n').map(line => line.trim()).find(Boolean) || '';
          }
          const url = a.href;
          if (!title || seen.has(url)) continue;
          seen.add(url);
          let date = '';
          const text = block.innerText || '';
          for (const re of datePatterns) {
            const m = text.match(re);
            if (m) { date = m[0]; break; }
          }
          out.push({ title: title.slice(0, 180), url, date });
          if (out.length >= limit) return out.slice(0, limit);
        }
      }
      return out.slice(0, limit);
    }
    """
    try:
        results: list[dict[str, str]] = await browser.page.evaluate(js, limit)
        if results:
            return _organic_results(results)
    except Exception:
        pass

    # Reuse the controller's independently tested per-engine parsers as a
    # second extraction path. They cover alternate DOM layouts and return
    # ``link`` rather than ``url``.
    get_results = getattr(browser, "get_search_results", None)
    if not callable(get_results):
        return []
    try:
        response = await get_results(max_results=limit)
    except Exception:
        return []
    normalized: list[dict[str, str]] = []
    for item in response.get("results", []) if response.get("success") else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("link")
        title = item.get("title")
        if isinstance(url, str) and isinstance(title, str) and url and title.strip():
            normalized.append(
                {
                    "title": title.strip()[:180],
                    "url": _unwrap_search_redirect(url),
                    "date": str(item.get("date") or ""),
                    "snippet": str(item.get("snippet") or "")[:500],
                }
            )
    return _organic_results(normalized)[:limit]

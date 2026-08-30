"""Search engine tools for web automation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from webagent.core.models import ToolResult
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool

# Search engine configurations
_SEARCH_ENGINES = {
    "google": {
        "url": "https://www.google.com",
        "input_selector": 'textarea[name="q"]',  # Google updated to textarea in 2024
        "wait_selector": 'div[id="search"]',
    },
    "bing": {
        "url": "https://www.bing.com",
        "input_selector": 'input[name="q"]',
        "wait_selector": 'div[id="b_content"]',
    },
    "yahoo": {
        "url": "https://search.yahoo.com",
        "input_selector": 'input[name="p"]',
        "wait_selector": 'div[id="web"]',
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com",
        "input_selector": 'textarea[name="q"], input[name="q"]',
        "wait_selector": 'div[id="links"]',
    },
}

# Lowercased text that marks a search-engine error / zero-results / bot-block page.
# Detecting these stops the agent from looping on a dead results page.
_SEARCH_ERROR_MARKERS = (
    "unexpected error",
    "no results found",
    "did not match any documents",
    "detected unusual traffic",
    "our systems have detected",
    "to continue, please type the characters",
    "before you continue",
)

# Ordered engines tried automatically after the primary engine fails. A single
# engine is often bot-blocked, so cascading through all three maximizes the
# chance of getting real results before the agent has to work around it.
_FALLBACK_ORDER = ("bing", "yahoo", "duckduckgo", "google")


def _classify_search_failure(reason: str) -> str:
    lowered = reason.casefold()
    if "captcha" in lowered or "verification" in lowered or "blocked" in lowered:
        return "challenge_or_block"
    if "selector" in lowered or "structured results" in lowered:
        return "selector_drift"
    if "no results" in lowered or "empty" in lowered:
        return "empty_results"
    if "navigate" in lowered:
        return "navigation_failure"
    if "submit" in lowered or "type query" in lowered:
        return "interaction_failure"
    return "unknown"


def _failure_data(query: str, engines: list[str], reason: str) -> dict[str, Any]:
    return {
        "query": query,
        "attempted_engines": engines,
        "failure_category": _classify_search_failure(reason),
        "search_attempts": [{"engine": engine, "outcome": "failed"} for engine in engines],
    }


def _unwrap_search_redirect(url: str) -> str:
    """Expose Yahoo's destination URL instead of treating its click tracker as evidence."""
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.endswith("search.yahoo.com"):
        marker = "/RU="
        if marker in parsed.path:
            encoded = parsed.path.split(marker, 1)[1].split("/", 1)[0]
            destination = unquote(encoded)
            if urlsplit(destination).scheme in {"http", "https"}:
                return destination
    return url


def _fallback_chain(primary: str, *, allow_google: bool) -> list[str]:
    """Remaining fallback engines to try, in order, after ``primary`` failed."""
    return [
        engine
        for engine in _FALLBACK_ORDER
        if engine != primary and (engine != "google" or allow_google)
    ]


@tool(
    "search",
    "Search the web without opening Google by default (automated Google sessions often "
    "hit human verification). params: query (string), engine=bing|yahoo|duckduckgo|google "
    "(default: bing; Google requires AGENT_ALLOW_GOOGLE_SEARCH=true), "
    "recency=week|month|year|latest. For latest technical reports, use github_search too "
    "only when it is listed as an available tool; arXiv alone can lag behind.",
)
class SearchTool(BrowserToolBase):
    """Perform a web search using the specified search engine.

    This tool abstracts the multi-step process of:
    1. Navigating to the search engine
    2. Typing the query into the search box
    3. Submitting the search (pressing Enter)
    4. Waiting for results to load

    Smart date detection:
    - If recency not specified, auto-detects from query keywords
    - "latest", "recent", "newest" -> week
    - "2024", "2025", "2026" -> year

    Bing is the default. DuckDuckGo is the automatic fallback. Google is opt-in
    because repeated automated sessions commonly trigger human verification.
    """

    # Query keywords that suggest recency
    RECENCY_KEYWORDS = {
        "latest": ["latest", "newest", "most recent"],
        "week": ["recent", "last week", "this week"],
        "month": ["this month", "past month"],
        "year": ["this year", "past year", "2024", "2025", "2026"],
    }

    def __init__(self, browser: Any = None, config: Any = None, **kw: Any) -> None:
        super().__init__(browser=browser, **kw)
        self._allow_google = bool(getattr(config, "allow_google_search", False))

    def validate_params(self, params: dict[str, Any]) -> None:
        """Validate search parameters."""
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' parameter is required and must be non-empty")

        engine = params.get("engine", "google")
        if engine not in _SEARCH_ENGINES:
            valid = ", ".join(_SEARCH_ENGINES.keys())
            raise ValueError(f"'engine' must be one of: {valid}")

        recency = params.get("recency")
        if recency and recency not in ["week", "month", "year", "latest"]:
            raise ValueError("'recency' must be one of: week, month, year, latest")

    def _detect_recency(self, query: str) -> str | None:
        """Auto-detect recency from query keywords."""
        query_lower = query.lower()

        for recency, keywords in self.RECENCY_KEYWORDS.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return recency

        # Also check for explicit year mentions (e.g., "2026 technical report")
        import re

        years = re.findall(r"(20\d{2})", query)
        if years:
            # Use the most recent year mentioned to calculate date range
            year_int = max(int(y) for y in years)
            current_year = datetime.now(UTC).year
            # For historical years, use year-based filtering
            # For recent/current years, use month-based filtering
            return "year" if year_int < current_year else "month"

        return None

    def _add_date_filter(
        self, query: str, engine: str, recency: str | None, custom_date: str | None
    ) -> str:
        """Keep engine-specific date syntax out of the user's query text.

        Date filters belong in URL parameters. Injecting ``dt:y`` into a Bing
        query made the literal token appear in results and harmed relevance; the
        previous implementation also applied a second filter to the result URL.
        ``custom_date`` is retained in the signature for API compatibility.
        """
        del engine, recency, custom_date
        return query

    def _get_sorted_url(self, engine: str, base_url: str, recency: str) -> str:
        """Get URL with date sort parameter."""
        # Map recency to URL params
        recency_map = {
            "week": ("w", "week"),
            "month": ("m", "month"),
            "year": ("y", "year"),
            # "latest" means rank/compare all candidates, not "published in the
            # last week". A hard one-week filter can exclude the actual latest
            # report when a project has not published recently.
            "latest": (None, "latest"),
        }

        param, _ = recency_map.get(recency, (None, None))
        if not param:
            return base_url

        if engine == "google":
            # Google: tbs=qdr: w=week, m=month, y=year
            if "?" in base_url:
                return f"{base_url}&tbs=qdr:{param}"
            return f"{base_url}?tbs=qdr:{param}"
        if engine == "bing":
            # Bing: filters
            return f"{base_url}&filt=custom&sc=0-{param}-0"
        if engine == "duckduckgo":
            # DuckDuckGo: use df= URL parameter (d=day, w=week, m=month, y=year)
            ddg_param = {"w": "w", "m": "m", "y": "y"}.get(param, "w")
            sep = "&" if "?" in base_url else "?"
            return f"{base_url}{sep}df={ddg_param}"

        return base_url

    async def _navigate(self, config: dict[str, str], engine: str) -> ToolResult | None:
        """Step 1: navigate to the engine. Returns an error result, or None if OK."""
        nav_result = await self.browser.goto(config["url"])
        if not nav_result.get("success"):
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Failed to navigate to {engine}: {nav_result.get('error', 'Unknown error')}",
            )
        return None

    async def _type_query(self, config: dict[str, str], query: str) -> ToolResult | None:
        """Step 2: type the query into the search box. Error result, or None if OK."""
        type_result = await self.browser.type_text(
            selector=config["input_selector"],
            text=query,
            delay=50,
            clear_first=True,
        )
        if not type_result.get("success"):
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Failed to type query: {type_result.get('error', 'Unknown error')}",
            )
        return None

    async def _submit_and_wait(self, config: dict[str, str]) -> ToolResult | None:
        """Steps 3-4: submit the search and wait for results. Error result, or None."""
        press_result = await self.browser.press_key("Enter")
        if not press_result.get("success"):
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Failed to submit search: {press_result.get('error', 'Unknown error')}",
            )

        wait_result = await self.browser.wait_for_selector(
            selector=config["wait_selector"],
            state="visible",
            timeout=10000,
        )
        # Don't fail hard if wait fails - search may have still worked
        del wait_result
        return None

    async def _apply_date_sort(self, engine: str, config: dict[str, str], recency: str) -> None:
        """Step 5: re-sort by date via URL when a recency filter is active."""
        # Get current URL (search results page) instead of base config URL
        current_url = self.browser.page.url
        sorted_url = self._get_sorted_url(engine, current_url, recency)
        if sorted_url != current_url:
            await self.browser.goto(sorted_url)
            await self.browser.wait_for_selector(
                selector=config["wait_selector"],
                state="visible",
                timeout=10000,
            )

    async def _recover_step_error(
        self,
        error: ToolResult,
        *,
        query: str,
        recency: str | None,
        custom_date: str | None,
        engine: str,
        reason: str | None = None,
    ) -> ToolResult:
        failure_reason = reason or error.error or f"Search interaction failed on {engine}"
        fallbacks = _fallback_chain(engine, allow_google=self._allow_google)
        if fallbacks:
            return await self._try_fallback_engine(
                query,
                recency,
                custom_date,
                engine,
                fallbacks,
                failure_reason,
            )
        return error.model_copy(update={"data": _failure_data(query, [engine], failure_reason)})

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        """Execute the search operation."""
        query = params["query"].strip()
        original_query = query  # unfiltered — fallback engines use different date syntax
        requested_engine = params.get("engine", "bing")
        engine = requested_engine
        if engine == "google" and not self._allow_google:
            engine = "bing"
        recency = params.get("recency")
        custom_date = params.get("custom_date")

        # Auto-detect recency if not specified
        if not recency:
            recency = self._detect_recency(query)

        # Apply date filter if recency detected or specified
        if recency:
            query = self._add_date_filter(query, engine, recency, custom_date)

        config = _SEARCH_ENGINES[engine]

        try:
            nav_error = await self._navigate(config, engine)
            if nav_error:
                return await self._recover_step_error(
                    nav_error,
                    query=original_query,
                    recency=recency,
                    custom_date=custom_date,
                    engine=engine,
                )

            type_error = await self._type_query(config, query)
            if type_error:
                return await self._recover_step_error(
                    type_error,
                    query=original_query,
                    recency=recency,
                    custom_date=custom_date,
                    engine=engine,
                    reason=f"Input selector not found on {engine} (possibly CAPTCHA)",
                )

            submit_error = await self._submit_and_wait(config)
            if submit_error:
                return await self._recover_step_error(
                    submit_error,
                    query=original_query,
                    recency=recency,
                    custom_date=custom_date,
                    engine=engine,
                )

            # Apply date sort via URL if recency specified
            if recency:
                await self._apply_date_sort(engine, config, recency)

            return await self._collect_results(
                original_query,
                recency,
                custom_date,
                engine,
                query,
                config,
                requested_engine=requested_engine,
            )

        except Exception as e:
            reason = f"Search failed: {e}"
            return ToolResult(
                success=False,
                tool_name="search",
                error=reason,
                data=_failure_data(original_query, [engine], reason),
            )

    async def _collect_results(
        self,
        original_query: str,
        recency: str | None,
        custom_date: str | None,
        engine: str,
        query: str,
        config: dict[str, str],
        *,
        requested_engine: str,
    ) -> ToolResult:
        """Verify results loaded on the current engine and build the success result.

        Falls back to the engine cascade (then direct paper candidates) when the
        page is an error/empty/bot-block page.
        """
        if not await self._results_present(config):
            fallbacks = _fallback_chain(engine, allow_google=self._allow_google)
            if fallbacks:
                return await self._try_fallback_engine(
                    original_query,
                    recency,
                    custom_date,
                    engine,
                    fallbacks,
                    f"{engine} returned no results (error or blocked page)",
                )
            return ToolResult(
                success=False,
                tool_name="search",
                error=(
                    f"{engine} returned no results (error/blocked page). "
                    "Use only a URL already observed in prior evidence, or report the search failure."
                ),
                data=_failure_data(original_query, [engine], f"{engine} returned no results"),
            )

        results = await self._extract_results()
        if not results:
            fallbacks = _fallback_chain(engine, allow_google=self._allow_google)
            if fallbacks:
                return await self._try_fallback_engine(
                    original_query,
                    recency,
                    custom_date,
                    engine,
                    fallbacks,
                    f"{engine} page loaded but no structured results could be extracted",
                )
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"{engine} page loaded but no structured results could be extracted",
                data=_failure_data(
                    original_query, [engine], "no structured results could be extracted"
                ),
            )

        data: dict[str, Any] = {
            "query": query,
            "engine": engine,
            "url": self.browser.page.url,
            "title": await self.browser.page.title(),
            "results": results,
            "search_attempts": [{"engine": engine, "outcome": "success"}],
        }
        if requested_engine != engine:
            data["requested_engine"] = requested_engine
            data["engine_notice"] = (
                "Google automation is disabled; used Bing to avoid human verification."
            )
        return ToolResult(
            success=True,
            tool_name="search",
            data=data,
        )

    async def _results_present(self, config: dict[str, str]) -> bool:
        """Return True only if the current page actually shows search results.

        Guards against engines returning an error / zero-results / bot-block page
        (which lacks the results selector) being reported as a successful search.
        """
        try:
            page = self.browser.page
            body = (await page.inner_text("body")).lower()
            if any(marker in body for marker in _SEARCH_ERROR_MARKERS):
                return False
            if await page.locator(config["wait_selector"]).count() > 0:
                return True
            # Generic fallback: a real results page links out to many sites.
            return bool(await page.locator("a[href^='http']").count() > 5)
        except Exception:
            return False

    async def _extract_results(self, limit: int = 10) -> list[dict[str, str]]:
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
          ];
          const datePatterns = [
            /\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b/i,
            /\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b/,
            /\b\d+\s+(?:day|days|hour|hours|week|weeks|month|months|year|years)\s+ago\b/i,
          ];
          for (const sel of containers) {
            for (const block of document.querySelectorAll(sel)) {
              const a = block.querySelector('a[href^="http"]');
              if (!a) continue;
              const title = (a.innerText || a.getAttribute('title') || '').trim();
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
            results: list[dict[str, str]] = await self.browser.page.evaluate(js, limit)
            if results:
                for item in results:
                    if isinstance(item.get("url"), str):
                        item["url"] = _unwrap_search_redirect(item["url"])
                return results
        except Exception:
            pass

        # Reuse the controller's independently tested per-engine parsers as a
        # second extraction path. They cover alternate DOM layouts and return
        # ``link`` rather than ``url``.
        get_results = getattr(self.browser, "get_search_results", None)
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
        return normalized[:limit]

    async def _try_fallback_engine(
        self,
        query: str,
        recency: str | None,
        custom_date: str | None,
        failed_engine: str,
        fallback_engines: list[str],
        reason: str,
    ) -> ToolResult:
        """Cascade through ``fallback_engines`` until one returns real results.

        Args:
            query: The raw search query (WITHOUT any engine-specific date filter)
            recency: Recency setting
            custom_date: Custom date setting
            failed_engine: The engine that originally failed
            fallback_engines: Ordered engines to try as fallbacks
            reason: Why the original engine failed

        Returns:
            The first successful fallback result; otherwise direct paper
            candidates; otherwise a terminal failure telling the agent to
            navigate directly.
        """
        import logging

        logger = logging.getLogger("webagent")

        prev_engine, prev_reason = failed_engine, reason
        attempts: list[dict[str, str]] = [
            {
                "engine": failed_engine,
                "outcome": "failed",
                "failure_category": _classify_search_failure(reason),
                "error": reason,
            }
        ]
        for fallback_engine in fallback_engines:
            logger.warning(
                "Search engine '%s' failed: %s. Trying fallback engine '%s'.",
                prev_engine,
                prev_reason,
                fallback_engine,
            )
            result = await self._attempt_engine(
                query, recency, custom_date, fallback_engine, failed_engine, prev_reason
            )
            if result.success:
                result.data["search_attempts"] = [
                    *attempts,
                    {"engine": fallback_engine, "outcome": "success"},
                ]
                return result
            attempts.append(
                {
                    "engine": fallback_engine,
                    "outcome": "failed",
                    "failure_category": _classify_search_failure(result.error or ""),
                    "error": result.error or "unknown search failure",
                }
            )
            prev_engine = fallback_engine
            prev_reason = result.error or f"{fallback_engine} returned no usable results"

        # Every engine in the cascade failed — tell the agent to stop searching
        # and navigate directly to the target site.
        tried = ", ".join([failed_engine, *fallback_engines])
        return ToolResult(
            success=False,
            tool_name="search",
            error=(
                f"All search engines ({tried}) returned no results (error/blocked "
                "pages). Use only a URL already observed in prior evidence, or report the failure. "
                f"Last failure: {prev_reason}"
            ),
            data={
                "query": query,
                "attempted_engines": [failed_engine, *fallback_engines],
                "failure_category": _classify_search_failure(prev_reason),
                "search_attempts": attempts,
            },
        )

    async def _attempt_engine(
        self,
        query: str,
        recency: str | None,
        custom_date: str | None,
        engine: str,
        failed_engine: str,
        reason: str,
    ) -> ToolResult:
        """Run a single search on ``engine``; return success or a failure result.

        ``query`` is the raw query; the engine's own date syntax is applied here.
        Any navigation/typing error or a blocked/empty results page yields a
        failure ToolResult so the caller can advance to the next fallback engine.
        """
        # Re-apply the date filter using this engine's own syntax.
        if recency:
            query = self._add_date_filter(query, engine, recency, custom_date)

        config = _SEARCH_ENGINES[engine]
        try:
            nav_result = await self.browser.goto(config["url"])
            if not nav_result.get("success"):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to navigate to {engine}: {nav_result.get('error', 'Unknown error')}",
                )

            type_result = await self.browser.type_text(
                selector=config["input_selector"],
                text=query,
                delay=50,
                clear_first=True,
            )
            if not type_result.get("success"):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to type query on {engine}: {type_result.get('error', 'Unknown error')}",
                )

            press_result = await self.browser.press_key("Enter")
            if not press_result.get("success"):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to submit search on {engine}: {press_result.get('error', 'Unknown error')}",
                )

            await self.browser.wait_for_selector(
                selector=config["wait_selector"],
                state="visible",
                timeout=10000,
            )

            if recency:
                current_url = self.browser.page.url
                sorted_url = self._get_sorted_url(engine, current_url, recency)
                if sorted_url != current_url:
                    await self.browser.goto(sorted_url)
                    await self.browser.wait_for_selector(
                        selector=config["wait_selector"],
                        state="visible",
                        timeout=10000,
                    )

            if not await self._results_present(config):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"{engine} returned no results (error or blocked page)",
                )

            results = await self._extract_results()
            if not results:
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"{engine} page loaded but no structured results could be extracted",
                )

            return ToolResult(
                success=True,
                tool_name="search",
                data={
                    "query": query,
                    "engine": engine,
                    "url": self.browser.page.url,
                    "title": await self.browser.page.title(),
                    "results": results,
                    "fallback_from": failed_engine,
                    "fallback_reason": reason,
                },
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Fallback search on {engine} also failed: {e}",
            )

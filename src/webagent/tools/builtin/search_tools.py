"""Search engine tools for web automation."""

from __future__ import annotations

from typing import Any

from webagent.core.models import ToolResult
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
    "duckduckgo": {
        "url": "https://duckduckgo.com",
        "input_selector": 'input[name="q"]',
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
_FALLBACK_ORDER = ("google", "bing", "duckduckgo")


def _fallback_chain(primary: str) -> list[str]:
    """Remaining fallback engines to try, in order, after ``primary`` failed."""
    return [engine for engine in _FALLBACK_ORDER if engine != primary]


@tool(
    "search",
    "Search the web. params: query (string), engine=google|bing|duckduckgo (default: google), recency=week|month|year|latest. "
    "For Qwen technical-report/PDF queries, blocked search engines may return direct arXiv candidates.",
)
class SearchTool:
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

    Note: DuckDuckGo is recommended for automated agents as it's more bot-friendly
    and less likely to trigger CAPTCHAs than Google/Bing.
    """

    # Query keywords that suggest recency
    RECENCY_KEYWORDS = {
        "week": ["latest", "recent", "newest", "last week", "this week"],
        "month": ["this month", "past month"],
        "year": ["this year", "past year", "2024", "2025", "2026"],
    }

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
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
        import datetime
        import re

        years = re.findall(r"(20\d{2})", query)
        if years:
            # Use the most recent year mentioned to calculate date range
            year_int = max(int(y) for y in years)
            current_year = datetime.date.today().year
            # For historical years, use year-based filtering
            # For recent/current years, use month-based filtering
            return "year" if year_int < current_year else "month"

        return None

    def _add_date_filter(
        self, query: str, engine: str, recency: str | None, custom_date: str | None
    ) -> str:
        """Add date filter to query based on recency parameter."""
        import datetime

        if not recency:
            return query

        # Map recency to days
        recency_map = {
            "week": 7,
            "month": 30,
            "year": 365,
            "latest": 7,  # latest = week
        }

        days = recency_map.get(recency)
        if not days:
            return query

        today = datetime.date.today()
        start_date = today - datetime.timedelta(days=days)

        if engine == "google":
            return f"{query} after:{start_date.strftime('%Y/%m/%d')}"
        elif engine == "bing":
            if days <= 7:
                return f"{query} dt:w"
            elif days <= 30:
                return f"{query} dt:m"
            else:
                return f"{query} dt:y"
        # DuckDuckGo does not support after: query syntax; date filtering is
        # handled via df= URL parameter in _get_sorted_url instead.

        return query

    def _get_sorted_url(self, engine: str, base_url: str, recency: str) -> str:
        """Get URL with date sort parameter."""
        # Map recency to URL params
        recency_map = {
            "week": ("w", "week"),
            "month": ("m", "month"),
            "year": ("y", "year"),
            "latest": ("w", "week"),
        }

        param, _ = recency_map.get(recency, (None, None))
        if not param:
            return base_url

        if engine == "google":
            # Google: tbs=qdr: w=week, m=month, y=year
            if "?" in base_url:
                return f"{base_url}&tbs=qdr:{param}"
            else:
                return f"{base_url}?tbs=qdr:{param}"
        elif engine == "bing":
            # Bing: filters
            return f"{base_url}&filt=custom&sc=0-{param}-0"
        elif engine == "duckduckgo":
            # DuckDuckGo: use df= URL parameter (d=day, w=week, m=month, y=year)
            ddg_param = {"w": "w", "m": "m", "y": "y"}.get(param, "w")
            sep = "&" if "?" in base_url else "?"
            return f"{base_url}{sep}df={ddg_param}"

        return base_url

    async def execute(self, params: dict) -> ToolResult:
        """Execute the search operation."""
        query = params["query"].strip()
        original_query = query  # unfiltered — fallback engines use different date syntax
        engine = params.get("engine", "google")  # Default to Google for best result quality
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
            # Step 1: Navigate to search engine
            nav_result = await self.browser.goto(config["url"])
            if not nav_result.get("success"):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to navigate to {engine}: {nav_result.get('error', 'Unknown error')}",
                )

            # Step 2: Type query into search box
            type_result = await self.browser.type_text(
                selector=config["input_selector"],
                text=query,
                delay=50,
                clear_first=True,
            )
            if not type_result.get("success"):
                # Input selector not found - might be CAPTCHA or page changed.
                # Cascade through the remaining engines before giving up.
                fallbacks = _fallback_chain(engine)
                if fallbacks:
                    return await self._try_fallback_engine(
                        original_query,
                        recency,
                        custom_date,
                        engine,
                        fallbacks,
                        f"Input selector not found on {engine} (possibly CAPTCHA)",
                    )
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to type query: {type_result.get('error', 'Unknown error')}",
                )

            # Step 3: Press Enter to submit search
            press_result = await self.browser.press_key("Enter")
            if not press_result.get("success"):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to submit search: {press_result.get('error', 'Unknown error')}",
                )

            # Step 4: Wait for results to load
            wait_result = await self.browser.wait_for_selector(
                selector=config["wait_selector"],
                state="visible",
                timeout=10000,
            )
            if not wait_result.get("success"):
                # Don't fail hard if wait fails - search may have still worked
                pass

            # Step 5: Apply date sort via URL if recency specified
            if recency:
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

            # Verify results actually loaded. Engines show an error/empty/bot-block
            # page (no results selector) on which the agent would otherwise loop.
            if not await self._results_present(config):
                fallbacks = _fallback_chain(engine)
                if fallbacks:
                    return await self._try_fallback_engine(
                        original_query,
                        recency,
                        custom_date,
                        engine,
                        fallbacks,
                        f"{engine} returned no results (error or blocked page)",
                    )
                direct = _direct_paper_candidates(original_query)
                if direct:
                    return direct
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=(
                        f"{engine} returned no results (error/blocked page). "
                        "Try navigating directly to a known site (e.g. arxiv.org) instead of searching."
                    ),
                )

            return ToolResult(
                success=True,
                tool_name="search",
                data={
                    "query": query,
                    "engine": engine,
                    "url": self.browser.page.url,
                    "title": await self.browser.page.title(),
                },
            )

        except Exception as e:
            direct = _direct_paper_candidates(original_query)
            if direct:
                return direct
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Search failed: {e}",
            )

    async def _results_present(self, config: dict) -> bool:
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
            return await page.locator("a[href^='http']").count() > 5
        except Exception:
            return False

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
                return result
            prev_engine = fallback_engine
            prev_reason = result.error or f"{fallback_engine} returned no usable results"

        # Every engine in the cascade failed — try structured candidates, else
        # tell the agent to stop searching and navigate directly.
        direct = _direct_paper_candidates(query)
        if direct:
            return direct
        tried = ", ".join([failed_engine, *fallback_engines])
        return ToolResult(
            success=False,
            tool_name="search",
            error=(
                f"All search engines ({tried}) returned no results (error/blocked "
                "pages). Try navigating directly to the target site."
            ),
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

            return ToolResult(
                success=True,
                tool_name="search",
                data={
                    "query": query,
                    "engine": engine,
                    "url": self.browser.page.url,
                    "title": await self.browser.page.title(),
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


def _direct_paper_candidates(query: str) -> ToolResult | None:
    """Return direct structured paper candidates when browser search is blocked."""
    try:
        from webagent.tools.builtin.arxiv_tools import known_arxiv_results

        results = known_arxiv_results(query, max_results=5)
    except Exception:
        results = []
    if not results:
        return None

    top = results[0]
    return ToolResult(
        success=True,
        tool_name="search",
        data={
            "query": query,
            "engine": "direct_arxiv_fallback",
            "url": top["abs_url"],
            "title": top["title"],
            "results": results,
            "warning": (
                "Browser search engines returned no usable results; returned direct "
                "arXiv candidates. Use the top result's pdf_url with download_pdf."
            ),
        },
    )

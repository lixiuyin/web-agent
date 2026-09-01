"""Search engine tools for web automation."""

from __future__ import annotations

import asyncio
import base64
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import httpx

from webagent.core.models import ToolResult
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool

# Search engine configurations
_SEARCH_ENGINES = {
    "google": {
        "url": "https://www.google.com",
        "query_url": "https://www.google.com/search",
        "query_param": "q",
        "input_selector": 'textarea[name="q"]',  # Google updated to textarea in 2024
        "wait_selector": 'div[id="search"]',
    },
    "bing": {
        "url": "https://www.bing.com",
        "query_url": "https://www.bing.com/search",
        "query_param": "q",
        "input_selector": 'input[name="q"]',
        "wait_selector": 'div[id="b_content"]',
    },
    "yahoo": {
        "url": "https://search.yahoo.com",
        "input_selector": 'input[name="p"]',
        "wait_selector": 'div[id="web"]',
    },
    "yahoo_japan": {
        "url": "https://search.yahoo.co.jp",
        "query_url": "https://search.yahoo.co.jp/search",
        "query_param": "p",
        "input_selector": 'input[name="p"]',
        "wait_selector": "a.sw-Card__titleInner",
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com",
        "input_selector": 'textarea[name="q"], input[name="q"]',
        "wait_selector": 'article[data-testid="result"], div[id="links"]',
    },
    "seznam": {
        "url": "https://search.seznam.cz/",
        "query_url": "https://search.seznam.cz/",
        "query_param": "q",
        "input_selector": 'input[name="q"]',
        "wait_selector": 'a[data-e-a="heading"]',
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
    "unusual traffic from your computer network",
    "sorry, but your computer or network may be sending automated queries",
    "unfortunately, bots use duckduckgo too",
    "not a robot",
)
_SEARCH_CHALLENGE_URL_MARKERS = ("/sorry/", "recaptcha")
_GOOGLE_CUSTOM_SEARCH_API_URL = "https://customsearch.googleapis.com/customsearch/v1"

# Ordered engines tried automatically after the primary engine fails. A single
# engine is often bot-blocked, so cascading through all three maximizes the
# chance of getting real results before the agent has to work around it.
_FALLBACK_ORDER = ("bing", "yahoo_japan", "seznam", "yahoo", "duckduckgo", "google")

# Strict headless evaluation cannot hand a challenge to a person. Keep its
# engine pool to providers repeatedly verified in the same bundled-Chromium
# environment; broader interactive runs retain the full catalog.
_STRICT_HEADLESS_ENGINES = frozenset({"bing", "yahoo_japan", "seznam"})

_SITE_QUERY_DOMAIN_RE = re.compile(
    r"\bsite:\s*(?P<host>[a-z0-9-]+(?:\.[a-z0-9-]+)+)",
    re.IGNORECASE,
)
_QUOTED_QUERY_RE = re.compile(r'["“](.+?)["”]')
_PHRASE_STOP_WORDS = frozenset(
    {"a", "an", "and", "for", "in", "is", "of", "official", "page", "the", "to"}
)
_TOPICAL_QUERY_STOP_WORDS = frozenset(
    {
        "about",
        "documentation",
        "docs",
        "encyclopedia",
        "find",
        "framework",
        "guide",
        "home",
        "homepage",
        "installation",
        "introduction",
        "latest",
        "neutral",
        "newest",
        "official",
        "overview",
        "page",
        "recent",
        "report",
        "search",
        "site",
        "technical",
        "topic",
        "tutorial",
        "website",
    }
)


def _result_quality_issue(query: str, results: list[dict[str, str]]) -> str | None:
    """Reject a populated SERP when it clearly ignores a host/title constraint.

    Some regional search pages return dictionary results for one common word
    while silently discarding ``site:`` and quoted-title constraints.  Treating
    that page as success prevents the fallback cascade from reaching a useful
    engine.  This guard is deliberately narrow: unconstrained topical searches
    are not second-guessed.
    """
    # Only an explicit ``site:`` operator is a domain constraint. Version names
    # (``qwen3.8``) and file names (``tech_report.pdf``) are ordinary query
    # terms; treating every dotted token as a host rejects useful result pages.
    hosts = {
        match.group("host").casefold().rstrip(".")
        for match in _SITE_QUERY_DOMAIN_RE.finditer(query)
    }
    if hosts:
        observed_hosts: set[str] = set()
        for result in results:
            value = str(result.get("url") or result.get("link") or "")
            try:
                if hostname := urlsplit(value).hostname:
                    observed_hosts.add(hostname.casefold().rstrip("."))
            except ValueError:
                continue
        if not any(
            observed == expected or observed.endswith(f".{expected}")
            for observed in observed_hosts
            for expected in hosts
        ):
            return "results ignored the requested domain constraint"

    for phrase in _QUOTED_QUERY_RE.findall(query):
        terms = {
            term.casefold()
            for term in re.findall(r"[A-Za-z0-9]+", phrase)
            if len(term) >= 3 and term.casefold() not in _PHRASE_STOP_WORDS
        }
        if len(terms) < 2:
            continue
        if not any(
            terms.issubset(
                set(
                    re.findall(
                        r"[a-z0-9]+",
                        " ".join(str(value) for value in result.values()).casefold(),
                    )
                )
            )
            for result in results
        ):
            return "results ignored the quoted title constraint"

    # Reject only gross topical mismatches. Search engines occasionally return a
    # fully populated but stale/corrupted SERP (for example, a FastAPI query whose
    # ten results are all about poetry or sports). Requiring one distinctive query
    # token anywhere in title/URL/snippet is conservative enough to preserve
    # synonym-heavy results while preventing such pages from counting as success.
    topical_terms = {
        term.casefold()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", query)
        if term.casefold() not in _TOPICAL_QUERY_STOP_WORDS
        and not term.casefold().startswith(("http", "www"))
    }
    if topical_terms and not any(
        any(
            term in " ".join(str(value) for value in result.values()).casefold()
            for term in topical_terms
        )
        for result in results
    ):
        return "results are unrelated to the distinctive query terms"
    return None


def _classify_search_failure(reason: str) -> str:
    lowered = reason.casefold()
    if re.search(r"\bhttp\s+429\b", lowered):
        return "rate_limited"
    if re.search(r"\bhttp\s+5\d\d\b", lowered):
        return "upstream_http_5xx"
    if any(
        marker in lowered
        for marker in ("bot challenge", "captcha", "challenge", "verification", "blocked")
    ):
        return "challenge_or_block"
    if "irrelevant results" in lowered or "ignored the" in lowered:
        return "quality_failure"
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
    """Expose a search result destination instead of its engine click tracker."""
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.endswith("search.yahoo.com"):
        marker = "/RU="
        if marker in parsed.path:
            encoded = parsed.path.split(marker, 1)[1].split("/", 1)[0]
            destination = unquote(encoded)
            if urlsplit(destination).scheme in {"http", "https"}:
                return destination
    if parsed.hostname and parsed.hostname.endswith("bing.com"):
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if encoded.startswith("a1"):
            payload = encoded[2:]
            try:
                padding = "=" * (-len(payload) % 4)
                destination = base64.urlsafe_b64decode(payload + padding).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                destination = ""
            if urlsplit(destination).scheme in {"http", "https"}:
                return destination
    return url


def _bing_compat_query(query: str) -> str:
    """Rewrite Bing's unreliable ``site:`` syntax while retaining its postcondition.

    Some Bing regions render no organic result cards for a valid ``site:host``
    query, while the equivalent ``terms host`` query works. The result-quality
    guard still checks the original query, so this compatibility form cannot
    turn off the requested domain or quoted-title constraint.
    """
    domains: list[str] = []

    def replace_site(match: re.Match[str]) -> str:
        domains.append(match.group("target"))
        return " "

    terms = re.sub(
        r"(?i)(?:^|\s)site:(?P<target>[a-z0-9.-]+(?:/[^\s]+)?)",
        replace_site,
        query,
    )
    if not domains:
        return query
    return " ".join([*terms.split(), *domains])


def _fallback_chain(primary: str, *, google_available: bool) -> list[str]:
    """Remaining fallback engines to try, in order, after ``primary`` failed."""
    return [
        engine
        for engine in _FALLBACK_ORDER
        if engine != primary and (engine != "google" or google_available)
    ]


@tool(
    "search",
    "Search the web with the configured default engine (Bing initially; automated Google sessions often "
    "hit human verification). params: query (string), "
    "engine=bing|seznam|yahoo_japan|yahoo|duckduckgo|google "
    "(Google requires browser opt-in or existing Custom Search API credentials), "
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

    Bing is the default because it is a widely used engine that returns stable
    browser-visible result links in the headless benchmark environment. Yahoo
    Japan and Seznam remain automatic reliability fallbacks.
    Google is opt-in because repeated automated sessions commonly trigger human verification.
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
        self._default_engine = str(getattr(config, "search_default_engine", "bing") or "bing")
        self._bing_market = getattr(config, "search_bing_market", None)
        self._google_api_key = str(getattr(config, "google_search_api_key", "") or "").strip()
        self._google_engine_id = str(getattr(config, "google_search_engine_id", "") or "").strip()
        self._google_api_timeout = float(getattr(config, "google_search_api_timeout_seconds", 15.0))
        self._google_api_available = bool(self._google_api_key and self._google_engine_id)
        self._google_available = self._allow_google or self._google_api_available
        self._strict_headless = bool(
            (
                getattr(config, "strict_eval_mode", False)
                or getattr(config, "search_engine_only", False)
            )
            and getattr(config, "browser_headless", False)
        )
        self._captcha_handling = str(getattr(config, "captcha_handling", "fail") or "fail")
        self._engine_cooldown_seconds = float(
            getattr(config, "search_engine_cooldown_seconds", 300.0)
        )
        self._engine_unavailable_until: dict[str, float] = {}

    def _fallback_engines(self, primary: str) -> list[str]:
        """Return fallbacks whose session cooldown has expired."""
        now = time.monotonic()
        return [
            engine
            for engine in _fallback_chain(primary, google_available=self._google_available)
            if not self._strict_headless or engine in _STRICT_HEADLESS_ENGINES
            if self._engine_unavailable_until.get(engine, 0.0) <= now
        ]

    def _record_engine_failure(self, engine: str, reason: str) -> None:
        """Temporarily quarantine failures likely to persist within this session."""
        category = _classify_search_failure(reason)
        if self._engine_cooldown_seconds <= 0 or category not in {
            "challenge_or_block",
            "navigation_failure",
            "quality_failure",
            "rate_limited",
            "upstream_http_5xx",
        }:
            return
        self._engine_unavailable_until[engine] = time.monotonic() + self._engine_cooldown_seconds

    async def _blocked_page_reason(self, engine: str) -> str:
        """Return a specific challenge reason when the current page exposes one."""
        try:
            page = self.browser.page
            current_url = page.url.casefold()
            body = (await page.inner_text("body")).casefold()
        except Exception:
            return f"{engine} returned no results (error or blocked page)"
        if engine == "duckduckgo" and "bots use duckduckgo" in body:
            return "DuckDuckGo bot challenge detected"
        if engine == "google" and (
            any(marker in current_url for marker in _SEARCH_CHALLENGE_URL_MARKERS)
            or "unusual traffic" in body
        ):
            return "Google human-verification challenge detected"
        return f"{engine} returned no results (error or blocked page)"

    def _engine_config(self, engine: str) -> dict[str, str]:
        config = dict(_SEARCH_ENGINES[engine])
        if engine == "bing" and self._bing_market:
            language, country = self._bing_market.split("-", 1)
            config["query_params"] = urlencode(
                {
                    "mkt": self._bing_market,
                    "cc": country.lower(),
                    "setlang": f"{language}-{country}",
                }
            )
        return config

    async def _navigate_direct_query(self, config: dict[str, str], query: str) -> dict[str, Any]:
        """Open a direct-result URL from a neutral page, retrying one aborted handoff."""
        query_url = config["query_url"]
        direct_url = f"{query_url}?{urlencode({config['query_param']: query})}"
        if extra_params := config.get("query_params"):
            direct_url = f"{direct_url}&{extra_params}"
        # Moving between two actively loading SERPs can abort the second
        # navigation before its document owns the page.  A neutral commit keeps
        # the fallback independent of the failed engine's pending redirects.
        await self.browser.goto("about:blank", wait_until="commit")
        result: dict[str, Any] = {}
        for attempt in range(2):
            result = await self.browser.goto(direct_url, wait_until="domcontentloaded")
            if result.get("success"):
                return result
            if attempt == 0:
                await asyncio.sleep(1)
        return result

    async def _search_google_api(self, query: str, recency: str | None) -> ToolResult:
        """Use Google's supported JSON API without exposing credentials in evidence."""
        params: dict[str, str | int] = {
            "key": self._google_api_key,
            "cx": self._google_engine_id,
            "q": query,
            "num": 10,
        }
        date_restrict = {
            "week": "w1",
            "month": "m1",
            "year": "y1",
        }.get(recency or "")
        if date_restrict:
            params["dateRestrict"] = date_restrict

        try:
            async with httpx.AsyncClient(timeout=self._google_api_timeout) as client:
                response = await client.get(_GOOGLE_CUSTOM_SEARCH_API_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Google Custom Search JSON API returned HTTP {exc.response.status_code}",
            )
        except Exception as exc:
            # Exception strings may embed the fully parameterized request URL,
            # including the API key. Report only the exception type.
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Google Custom Search JSON API request failed: {type(exc).__name__}",
            )

        items = payload.get("items", []) if isinstance(payload, dict) else []
        results: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("link")
            if isinstance(title, str) and isinstance(link, str) and title.strip() and link:
                results.append(
                    {
                        "title": title.strip()[:180],
                        "url": link,
                        "date": "",
                        "snippet": str(item.get("snippet") or "")[:500],
                    }
                )
        if not results:
            return ToolResult(
                success=False,
                tool_name="search",
                error="Google Custom Search JSON API returned no results",
            )
        if quality_issue := _result_quality_issue(query, results):
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Google returned irrelevant results: {quality_issue}",
            )
        return ToolResult(
            success=True,
            tool_name="search",
            data={
                "query": query,
                "engine": "google",
                "transport": "custom_search_json_api",
                "url": _GOOGLE_CUSTOM_SEARCH_API_URL,
                "title": "Google Custom Search JSON API",
                "results": results,
                "search_attempts": [{"engine": "google", "outcome": "success"}],
            },
        )

    async def _google_challenge_present(self) -> bool:
        """Recognize Google's human-verification page without attempting to solve it."""
        try:
            page = self.browser.page
            current_url = page.url.lower()
            if any(marker in current_url for marker in _SEARCH_CHALLENGE_URL_MARKERS):
                return True
            body = (await page.inner_text("body")).lower()
            return any(
                marker in body
                for marker in (
                    "detected unusual traffic",
                    "unusual traffic from your computer network",
                    "our systems have detected",
                    "not a robot",
                )
            )
        except Exception:
            return False

    def _can_wait_for_google_challenge(self) -> bool:
        """Allow the outer agent loop to hand a headed challenge to the user."""
        return not bool(getattr(self.browser, "headless", True)) and self._captcha_handling in {
            "report",
            "wait_for_human",
        }

    def validate_params(self, params: dict[str, Any]) -> None:
        """Validate search parameters."""
        if not isinstance(params.get("query"), str) or not params["query"].strip():
            raise ValueError("'query' parameter is required and must be non-empty")

        engine = params.get("engine", self._default_engine)
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
        if engine == "yahoo_japan":
            yahoo_param = {"w": "w", "m": "m1", "y": "y"}.get(param, "w")
            sep = "&" if "?" in base_url else "?"
            return f"{base_url}{sep}vd={yahoo_param}"

        return base_url

    async def _navigate(self, config: dict[str, str], engine: str) -> ToolResult | None:
        """Step 1: navigate to the engine. Returns an error result, or None if OK."""
        nav_result = await self.browser.goto(config["url"])
        status = nav_result.get("status")
        if not nav_result.get("success") or (
            isinstance(status, int) and not isinstance(status, bool) and status >= 400
        ):
            detail = (
                f"HTTP {status}"
                if isinstance(status, int)
                else nav_result.get("error", "Unknown error")
            )
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Failed to navigate to {engine}: {detail}",
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

    async def _apply_bing_market(self, query: str, config: dict[str, str]) -> ToolResult | None:
        """Pin the market on the actual results URL, not only Bing's landing page."""
        if not self._bing_market:
            return None
        language, country = self._bing_market.split("-", 1)
        market_url = "https://www.bing.com/search?" + urlencode(
            {
                "q": query,
                "mkt": self._bing_market,
                "setlang": f"{language}-{country}",
                "cc": country.lower(),
            }
        )
        nav_result = await self.browser.goto(market_url)
        if not nav_result.get("success"):
            return ToolResult(
                success=False,
                tool_name="search",
                error=(
                    "Failed to apply configured Bing market "
                    f"{self._bing_market}: {nav_result.get('error', 'Unknown error')}"
                ),
            )
        await self.browser.wait_for_selector(
            selector=config["wait_selector"],
            state="visible",
            timeout=10000,
        )
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
        fallbacks = self._fallback_engines(engine)
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
        requested_engine = params.get("engine", self._default_engine)
        engine = requested_engine
        if self._strict_headless and engine not in _STRICT_HEADLESS_ENGINES:
            engine = (
                self._default_engine if self._default_engine in _STRICT_HEADLESS_ENGINES else "bing"
            )
        recency = params.get("recency")
        custom_date = params.get("custom_date")

        if not recency:
            recency = self._detect_recency(query)

        if engine == "google" and self._google_api_available:
            api_result = await self._search_google_api(query, recency)
            if api_result.success:
                return api_result
            fallbacks = self._fallback_engines("google")
            return await self._try_fallback_engine(
                query,
                recency,
                custom_date,
                "google",
                fallbacks,
                api_result.error or "Google Custom Search JSON API failed",
                max_attempts=(
                    1
                    if _classify_search_failure(api_result.error or "") == "quality_failure"
                    else None
                ),
            )

        if engine == "google" and not self._allow_google:
            engine = "bing"

        if engine == "bing":
            query = _bing_compat_query(query)

        # Auto-detect recency if not specified
        if recency:
            query = self._add_date_filter(query, engine, recency, custom_date)

        config = self._engine_config(engine)

        try:
            if config.get("query_url"):
                direct_result = await self._navigate_direct_query(config, query)
                if not direct_result.get("success"):
                    direct_error = ToolResult(
                        success=False,
                        tool_name="search",
                        error=(
                            f"Failed to navigate to {engine}: "
                            f"{direct_result.get('error', 'Unknown error')}"
                        ),
                    )
                    return await self._recover_step_error(
                        direct_error,
                        query=original_query,
                        recency=recency,
                        custom_date=custom_date,
                        engine=engine,
                    )
                await self.browser.wait_for_selector(
                    selector=config["wait_selector"], state="visible", timeout=10000
                )
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

            if engine == "bing":
                market_error = await self._apply_bing_market(query, config)
                if market_error:
                    return await self._recover_step_error(
                        market_error,
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
            if (
                engine == "google"
                and self._can_wait_for_google_challenge()
                and await self._google_challenge_present()
            ):
                reason = (
                    "Google human-verification challenge detected; kept the headed page open "
                    "for the configured manual handoff"
                )
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=reason,
                    data=_failure_data(original_query, [engine], reason),
                )
            reason = await self._blocked_page_reason(engine)
            fallbacks = self._fallback_engines(engine)
            if fallbacks:
                return await self._try_fallback_engine(
                    original_query,
                    recency,
                    custom_date,
                    engine,
                    fallbacks,
                    reason,
                )
            return ToolResult(
                success=False,
                tool_name="search",
                error=(
                    f"{engine} returned no results (error/blocked page). "
                    "Use only a URL already observed in prior evidence, or report the search failure."
                ),
                data=_failure_data(original_query, [engine], reason),
            )

        results = await self._extract_results()
        if not results:
            fallbacks = self._fallback_engines(engine)
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

        if quality_issue := _result_quality_issue(original_query, results):
            fallbacks = self._fallback_engines(engine)
            if fallbacks:
                return await self._try_fallback_engine(
                    original_query,
                    recency,
                    custom_date,
                    engine,
                    fallbacks,
                    f"{engine} returned irrelevant results: {quality_issue}",
                    max_attempts=1,
                )
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"{engine} returned irrelevant results: {quality_issue}",
                data=_failure_data(original_query, [engine], quality_issue),
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
            if self._strict_headless:
                data["engine_notice"] = (
                    f"Strict headless evaluation used {engine} instead of {requested_engine}; "
                    "the requested engine is not in the audited headless engine pool."
                )
            else:
                data["engine_notice"] = (
                    "Google automation is disabled; used Bing to avoid human verification."
                )
        if engine == "bing" and self._bing_market:
            data["search_market"] = self._bing_market
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
            current_url = page.url.lower()
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
        *,
        max_attempts: int | None = None,
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
        self._record_engine_failure(failed_engine, reason)
        selected_fallbacks = (
            fallback_engines if max_attempts is None else fallback_engines[:max_attempts]
        )
        for fallback_engine in selected_fallbacks:
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
            self._record_engine_failure(fallback_engine, result.error or "unknown search failure")
            prev_engine = fallback_engine
            prev_reason = result.error or f"{fallback_engine} returned no usable results"

        # Every engine in the cascade failed — tell the agent to stop searching
        # and navigate directly to the target site.
        attempted_engines = [failed_engine, *selected_fallbacks]
        tried = ", ".join(attempted_engines)
        failure_category = _classify_search_failure(prev_reason)
        failure_summary = (
            "returned results that did not satisfy the explicit query constraint"
            if failure_category == "quality_failure"
            else "returned no results (error/blocked pages)"
        )
        return ToolResult(
            success=False,
            tool_name="search",
            error=(
                f"All attempted search engines ({tried}) {failure_summary}. "
                "Use only a URL already observed in prior evidence, or report the failure. "
                f"Last failure: {prev_reason}"
            ),
            data={
                "query": query,
                "attempted_engines": attempted_engines,
                "failure_category": failure_category,
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
        original_query = query
        if engine == "bing":
            query = _bing_compat_query(query)
        if recency:
            query = self._add_date_filter(query, engine, recency, custom_date)

        if engine == "google" and self._google_api_available:
            api_result = await self._search_google_api(query, recency)
            if api_result.success:
                api_result.data["fallback_from"] = failed_engine
                api_result.data["fallback_reason"] = reason
            return api_result

        config = self._engine_config(engine)
        try:
            query_url = config.get("query_url")
            nav_result = (
                await self._navigate_direct_query(config, query)
                if query_url
                else await self.browser.goto(config["url"])
            )
            if not nav_result.get("success"):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"Failed to navigate to {engine}: {nav_result.get('error', 'Unknown error')}",
                )

            if not query_url:
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
                        error=(
                            f"Failed to type query on {engine}: "
                            f"{type_result.get('error', 'Unknown error')}"
                        ),
                    )

                press_result = await self.browser.press_key("Enter")
                if not press_result.get("success"):
                    return ToolResult(
                        success=False,
                        tool_name="search",
                        error=(
                            f"Failed to submit search on {engine}: "
                            f"{press_result.get('error', 'Unknown error')}"
                        ),
                    )

            await self.browser.wait_for_selector(
                selector=config["wait_selector"],
                state="visible",
                timeout=10000,
            )

            if engine == "bing" and not query_url:
                market_error = await self._apply_bing_market(query, config)
                if market_error:
                    return market_error

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

            if quality_issue := _result_quality_issue(original_query, results):
                return ToolResult(
                    success=False,
                    tool_name="search",
                    error=f"{engine} returned irrelevant results: {quality_issue}",
                )

            data: dict[str, Any] = {
                "query": query,
                "engine": engine,
                "url": self.browser.page.url,
                "title": await self.browser.page.title(),
                "results": results,
                "fallback_from": failed_engine,
                "fallback_reason": reason,
            }
            if engine == "bing" and self._bing_market:
                data["search_market"] = self._bing_market
            return ToolResult(
                success=True,
                tool_name="search",
                data=data,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                tool_name="search",
                error=f"Fallback search on {engine} also failed: {e}",
            )

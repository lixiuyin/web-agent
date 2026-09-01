"""Tests for search tools."""

from types import SimpleNamespace

import httpx
import pytest

from webagent.core.models import ToolCall, ToolResult
from webagent.tools.builtin.search_tools import (
    SearchTool,
    _bing_compat_query,
    _classify_search_failure,
    _result_quality_issue,
    _unwrap_search_redirect,
)
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry


class MockLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    async def count(self) -> int:
        return self._count


class MockPage:
    """Mock Playwright page. Defaults simulate a normal results page."""

    def __init__(self, body="Web results: result one result two result three", link_count=20):
        self.url = "https://www.google.com/search?q=test"
        self._body = body
        self._link_count = link_count

    async def title(self):
        return "Search Results"

    async def inner_text(self, selector):
        return self._body

    def locator(self, selector):
        if "a[href" in selector:
            return MockLocator(self._link_count)
        return MockLocator(1 if "result" in self._body.lower() else 0)

    async def evaluate(self, _script, _limit):
        return [{"title": "Example result", "url": "https://example.test/result", "date": ""}]


class MockBrowser:
    """Mock browser controller for testing."""

    def __init__(self, page=None):
        self.page = page or MockPage()
        self.goto_called = False
        self.type_called = False
        self.press_called = False
        self.wait_called = False

    async def goto(self, url, **kwargs):
        self.goto_called = True
        return {"success": True, "url": url, "title": "Search"}

    async def type_text(self, selector, text, **kwargs):
        self.type_called = True
        return {"success": True, "selector": selector, "text": text}

    async def press_key(self, key):
        self.press_called = True
        return {"success": True, "key": key}

    async def wait_for_selector(self, selector, **kwargs):
        self.wait_called = True
        return {"success": True, "selector": selector}


@pytest.fixture
def mock_browser():
    return MockBrowser()


@pytest.fixture
def tool_executor(mock_browser):
    """Create a ToolExecutor with mocked browser."""
    import webagent.tools.builtin.search_tools  # noqa: F401

    registry = ToolRegistry()
    registry.auto_discover(browser=mock_browser)
    return ToolExecutor(registry)


@pytest.mark.asyncio
async def test_search_tool_success(tool_executor, mock_browser):
    """Test successful search execution."""
    result = await tool_executor.execute(
        ToolCall(tool_name="search", parameters={"query": "test query"}, reasoning="Search test")
    )

    assert result.success is True
    assert mock_browser.goto_called
    assert not mock_browser.type_called
    assert not mock_browser.press_called
    assert mock_browser.wait_called
    assert result.data["query"] == "test query"
    assert result.data["engine"] == "bing"


@pytest.mark.asyncio
async def test_search_tool_bing(tool_executor, mock_browser):
    """Test search with Bing engine."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="search",
            parameters={"query": "test", "engine": "bing"},
            reasoning="Search Bing",
        )
    )

    assert result.success is True
    assert result.data["engine"] == "bing"
    assert mock_browser.type_called is False
    assert mock_browser.press_called is False


@pytest.mark.asyncio
async def test_search_tool_yahoo_japan_uses_direct_results_url() -> None:
    browser = MockBrowser()
    tool = SearchTool(browser=browser)

    result = await tool.execute({"query": "python documentation", "engine": "yahoo_japan"})

    assert result.success is True
    assert result.data["engine"] == "yahoo_japan"
    assert browser.type_called is False
    assert browser.press_called is False


@pytest.mark.asyncio
async def test_bing_market_is_applied_and_reported() -> None:
    class MarketBrowser(MockBrowser):
        def __init__(self) -> None:
            super().__init__()
            self.goto_urls: list[str] = []

        async def goto(self, url, **kwargs):
            self.goto_urls.append(url)
            self.page.url = url
            return await super().goto(url)

    browser = MarketBrowser()
    tool = SearchTool(
        browser=browser,
        config=SimpleNamespace(allow_google_search=False, search_bing_market="en-US"),
    )

    result = await tool.execute({"query": "official documentation", "engine": "bing"})

    assert result.success is True
    search_url = next(url for url in browser.goto_urls if "/search?" in url)
    assert "cc=us" in search_url
    assert "setlang=en-US" in search_url
    assert "mkt=en-US" in search_url
    assert browser.goto_urls.count("about:blank") == 1
    assert result.data["search_market"] == "en-US"


@pytest.mark.asyncio
async def test_search_tool_validation_error(tool_executor):
    """Test search tool parameter validation."""
    result = await tool_executor.execute(
        ToolCall(tool_name="search", parameters={}, reasoning="Missing query")
    )

    assert result.success is False
    assert "Validation" in (result.error or "")
    assert "query" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_search_reports_failure_on_error_page():
    """Regression (outputs/ failure): an engine error page must NOT report success.

    Both Google and the DuckDuckGo fallback show 'Unexpected error' → the tool
    must return success=False so the agent stops looping on a dead page.
    """
    import webagent.tools.builtin.search_tools  # noqa: F401

    error_page = MockPage(body="Unexpected error. Please try again.", link_count=0)
    browser = MockBrowser(page=error_page)
    registry = ToolRegistry()
    registry.auto_discover(browser=browser)
    ex = ToolExecutor(registry)

    result = await ex.execute(
        ToolCall(tool_name="search", parameters={"query": "unrelated report"})
    )
    assert result.success is False
    assert "no results" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_search_does_not_return_canned_qwen_candidates_when_blocked():
    """Qwen queries are not special-cased: blocked engines fail honestly rather
    than returning a hardcoded candidate."""
    import webagent.tools.builtin.search_tools  # noqa: F401

    error_page = MockPage(body="Unexpected error. Please try again.", link_count=0)
    browser = MockBrowser(page=error_page)
    registry = ToolRegistry()
    registry.auto_discover(browser=browser)
    ex = ToolExecutor(registry)

    result = await ex.execute(
        ToolCall(tool_name="search", parameters={"query": "Qwen technical report PDF"})
    )

    assert result.success is False
    assert "no results" in (result.error or "").lower()


class _CascadePage:
    """Results page whose content depends on which engine is currently loaded."""

    def __init__(self, blocked: set[str]) -> None:
        self._blocked = blocked
        self.url = "https://www.google.com/search?q=test"
        self.current = "google"

    async def title(self) -> str:
        return "Search Results"

    async def inner_text(self, selector) -> str:
        if self.current in self._blocked:
            return "Unexpected error. Please try again."
        return "Web results: result one result two result three"

    def locator(self, selector):
        blocked = self.current in self._blocked
        if "a[href" in selector:
            return MockLocator(0 if blocked else 20)
        return MockLocator(0 if blocked else 1)

    async def evaluate(self, _script, _limit):
        if self.current in self._blocked:
            return []
        return [{"title": "Example result", "url": "https://example.test/result", "date": ""}]


class _CascadeBrowser:
    """Browser that routes each engine to a blocked or working results page."""

    def __init__(self, page: _CascadePage) -> None:
        self.page = page
        self.engines_tried: list[str] = []

    @staticmethod
    def _engine_of(url: str) -> str:
        if url == "about:blank":
            return "blank"
        if "bing" in url:
            return "bing"
        if "search.seznam" in url:
            return "seznam"
        if "duckduckgo" in url:
            return "duckduckgo"
        if "search.yahoo.co.jp" in url:
            return "yahoo_japan"
        if "yahoo" in url:
            return "yahoo"
        return "google"

    async def goto(self, url, **kwargs):
        engine = self._engine_of(url)
        self.page.current = engine
        self.page.url = url
        if engine != "blank" and (not self.engines_tried or self.engines_tried[-1] != engine):
            self.engines_tried.append(engine)
        return {"success": True, "url": url, "title": "Search"}

    async def type_text(self, selector, text, **kwargs):
        return {"success": True}

    async def press_key(self, key):
        return {"success": True}

    async def wait_for_selector(self, selector, **kwargs):
        return {"success": True}


@pytest.mark.asyncio
async def test_search_defaults_to_bing_without_google():
    """Default automation must not open Google's human-verification page."""
    import webagent.tools.builtin.search_tools  # noqa: F401

    browser = _CascadeBrowser(_CascadePage(blocked=set()))
    registry = ToolRegistry()
    registry.auto_discover(browser=browser)
    ex = ToolExecutor(registry)

    result = await ex.execute(
        ToolCall(tool_name="search", parameters={"query": "neutral encyclopedia topic"})
    )

    assert result.success is True
    assert result.data["engine"] == "bing"
    assert "fallback_from" not in result.data
    assert browser.engines_tried == ["bing"]


@pytest.mark.asyncio
async def test_google_request_is_rerouted_when_disabled():
    browser = _CascadeBrowser(_CascadePage(blocked=set()))
    tool = SearchTool(browser=browser)

    result = await tool.execute({"query": "technical report", "engine": "google"})

    assert result.success is True
    assert result.data["engine"] == "bing"
    assert result.data["requested_engine"] == "google"
    assert "human verification" in result.data["engine_notice"]
    assert browser.engines_tried == ["bing"]


@pytest.mark.asyncio
async def test_google_custom_search_api_is_used_without_browser_opt_in(monkeypatch):
    requests: list[tuple[str, dict[str, str | int]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), dict(request.url.params)))
        return httpx.Response(
            200,
            request=request,
            json={
                "items": [
                    {
                        "title": "General Python FAQ",
                        "link": "https://docs.python.org/3/faq/general.html",
                        "snippet": "Official Python documentation",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "webagent.tools.builtin.search_tools.httpx.AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )
    browser = _CascadeBrowser(_CascadePage(blocked=set()))
    tool = SearchTool(
        browser=browser,
        config=SimpleNamespace(
            allow_google_search=False,
            search_default_engine="google",
            google_search_api_key="private-google-key",
            google_search_engine_id="engine-id",
            google_search_api_timeout_seconds=3,
        ),
    )

    result = await tool.execute({"query": 'site:docs.python.org "General Python FAQ"'})

    assert result.success is True
    assert result.data["engine"] == "google"
    assert result.data["transport"] == "custom_search_json_api"
    assert result.data["results"][0]["url"].endswith("/3/faq/general.html")
    assert browser.engines_tried == []
    assert requests[0][1]["key"] == "private-google-key"
    assert "private-google-key" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_google_browser_search_uses_direct_results_url() -> None:
    browser = MockBrowser()
    tool = SearchTool(
        browser=browser,
        config=SimpleNamespace(allow_google_search=True),
    )

    result = await tool.execute({"query": "python documentation", "engine": "google"})

    assert result.success is True
    assert browser.goto_called is True
    assert browser.type_called is False
    assert browser.press_called is False


@pytest.mark.asyncio
async def test_headed_google_challenge_is_preserved_for_manual_handoff() -> None:
    class ChallengePage(MockPage):
        def __init__(self) -> None:
            super().__init__(
                body="Our systems have detected unusual traffic from your computer network",
                link_count=0,
            )
            self.url = "about:blank"

        def locator(self, selector):
            return MockLocator(0)

        async def evaluate(self, _script, _limit):
            return []

    class ChallengeBrowser(MockBrowser):
        headless = False

        async def goto(self, url, **kwargs):
            if "google.com/search" in url:
                self.page.url = "https://www.google.com/sorry/index?continue=redacted"
            else:
                self.page.url = url
            return {"success": True, "url": self.page.url}

    browser = ChallengeBrowser(page=ChallengePage())
    tool = SearchTool(
        browser=browser,
        config=SimpleNamespace(
            allow_google_search=True,
            captcha_handling="wait_for_human",
        ),
    )

    result = await tool.execute({"query": "python documentation", "engine": "google"})

    assert result.success is False
    assert result.data["failure_category"] == "challenge_or_block"
    assert result.data["attempted_engines"] == ["google"]
    assert browser.page.url.startswith("https://www.google.com/sorry/")


@pytest.mark.asyncio
async def test_search_tool_invalid_engine(tool_executor):
    """Test search tool with invalid engine."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="search",
            parameters={"query": "test", "engine": "invalid"},
            reasoning="Invalid engine",
        )
    )

    assert result.success is False
    assert "engine" in (result.error or "").lower()


class TestValidation:
    def test_invalid_recency(self):
        with pytest.raises(ValueError):
            SearchTool().validate_params({"query": "x", "recency": "decade"})


def test_yahoo_redirect_is_unwrapped_to_planner_visible_destination():
    wrapped = (
        "https://r.search.yahoo.com/_ylt=x/RV=2/RE=1/RO=10/"
        "RU=https%3A%2F%2Fgithub.com%2FQwenLM%2FQwen3/RK=2/RS=x"
    )

    assert _unwrap_search_redirect(wrapped) == "https://github.com/QwenLM/Qwen3"


def test_bing_redirect_is_unwrapped_to_planner_visible_destination():
    wrapped = (
        "https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9kb2NzLnB5dGhvbi5vcmcvMy9mYXEvZ2VuZXJhbC5odG1s"
    )

    assert _unwrap_search_redirect(wrapped) == "https://docs.python.org/3/faq/general.html"


def test_bing_site_query_uses_compatible_domain_postcondition_form() -> None:
    assert (
        _bing_compat_query('site:docs.python.org "General Python FAQ"')
        == '"General Python FAQ" docs.python.org'
    )


def test_bing_query_without_site_operator_is_unchanged() -> None:
    query = "General Python FAQ docs.python.org"

    assert _bing_compat_query(query) == query


def test_result_quality_rejects_ignored_domain_constraint() -> None:
    issue = _result_quality_issue(
        "site:docs.python.org General Python FAQ",
        [{"title": "General definition", "url": "https://dictionary.example/general"}],
    )

    assert issue == "results ignored the requested domain constraint"


def test_result_quality_rejects_ignored_quoted_title() -> None:
    issue = _result_quality_issue(
        'official page "General Python FAQ"',
        [{"title": "General definition", "url": "https://dictionary.example/general"}],
    )

    assert issue == "results ignored the quoted title constraint"


def test_result_quality_accepts_matching_domain_and_quoted_title() -> None:
    issue = _result_quality_issue(
        'site:docs.python.org "General Python FAQ"',
        [
            {
                "title": "General Python FAQ — Python documentation",
                "url": "https://docs.python.org/3/faq/general.html",
            }
        ],
    )

    assert issue is None


def test_result_quality_does_not_second_guess_unconstrained_topic() -> None:
    assert (
        _result_quality_issue(
            "neutral encyclopedia topic",
            [{"title": "Example result", "url": "https://example.test/result"}],
        )
        is None
    )


def test_result_quality_does_not_treat_version_or_filename_as_domain() -> None:
    results = [
        {
            "title": "Qwen3.8-Flash-Next technical report",
            "url": "https://github.com/QwenLM/Qwen3.8-Flash-Next/blob/main/tech_report.pdf",
        }
    ]

    assert _result_quality_issue("qwen3.8 technical report", results) is None
    assert _result_quality_issue("Qwen3.8 tech_report.pdf", results) is None


@pytest.mark.asyncio
async def test_duckduckgo_bot_challenge_marker_rejects_link_heavy_page() -> None:
    page = MockPage(
        body="Unfortunately, bots use DuckDuckGo too. Please complete this challenge.",
        link_count=20,
    )
    tool = SearchTool(browser=MockBrowser(page=page))

    assert await tool._results_present(tool._engine_config("duckduckgo")) is False


def test_persistent_search_failures_open_a_session_cooldown() -> None:
    tool = SearchTool(
        config=SimpleNamespace(
            allow_google_search=False,
            search_engine_cooldown_seconds=60,
        )
    )

    tool._record_engine_failure("duckduckgo", "DuckDuckGo bot challenge detected")
    tool._record_engine_failure(
        "bing", "bing returned irrelevant results: results ignored the requested domain constraint"
    )

    fallbacks = tool._fallback_engines("yahoo")
    assert "duckduckgo" not in fallbacks
    assert "bing" not in fallbacks
    assert "seznam" in fallbacks
    assert "yahoo_japan" in fallbacks


@pytest.mark.asyncio
async def test_quality_failure_tries_only_one_alternate_engine() -> None:
    tool = SearchTool(browser=MockBrowser())
    attempted: list[str] = []

    async def fail_attempt(
        _query: str,
        _recency: str | None,
        _custom_date: str | None,
        engine: str,
        _failed_engine: str,
        _reason: str,
    ) -> ToolResult:
        attempted.append(engine)
        return ToolResult(
            success=False,
            tool_name="search",
            error=f"{engine} returned irrelevant results: ignored constraint",
        )

    tool._attempt_engine = fail_attempt  # type: ignore[method-assign]
    result = await tool._try_fallback_engine(
        "site:example.test report",
        None,
        None,
        "bing",
        ["yahoo", "duckduckgo", "seznam"],
        "bing returned irrelevant results: ignored constraint",
        max_attempts=1,
    )

    assert result.success is False
    assert attempted == ["yahoo"]
    assert result.data["attempted_engines"] == ["bing", "yahoo"]
    assert result.data["failure_category"] == "quality_failure"


def test_selector_drift_does_not_open_engine_cooldown() -> None:
    tool = SearchTool(
        config=SimpleNamespace(
            allow_google_search=False,
            search_engine_cooldown_seconds=60,
        )
    )

    tool._record_engine_failure("duckduckgo", "no structured results could be extracted")

    assert "duckduckgo" in tool._fallback_engines("bing")


def test_strict_headless_fallbacks_exclude_unaudited_engines() -> None:
    tool = SearchTool(
        config=SimpleNamespace(
            allow_google_search=False,
            search_default_engine="bing",
            search_engine_cooldown_seconds=60,
            strict_eval_mode=True,
            search_engine_only=True,
            browser_headless=True,
        )
    )

    assert tool._fallback_engines("bing") == ["yahoo_japan", "seznam"]


@pytest.mark.asyncio
async def test_strict_headless_reroutes_explicit_unaudited_engine() -> None:
    browser = _CascadeBrowser(_CascadePage(blocked=set()))
    tool = SearchTool(
        browser=browser,
        config=SimpleNamespace(
            allow_google_search=False,
            search_default_engine="bing",
            strict_eval_mode=True,
            search_engine_only=True,
            browser_headless=True,
        ),
    )

    result = await tool.execute({"query": "technical report", "engine": "duckduckgo"})

    assert result.success is True
    assert result.data["engine"] == "bing"
    assert result.data["requested_engine"] == "duckduckgo"
    assert "audited headless engine pool" in result.data["engine_notice"]
    assert browser.engines_tried == ["bing"]


class TestDetectRecency:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("latest news roundup", "latest"),
            ("past month changes", "month"),
            ("this year review", "year"),
            ("plain topic", None),
        ],
    )
    def test_keyword_detection(self, query, expected):
        assert SearchTool()._detect_recency(query) == expected

    def test_year_in_past_uses_year(self):
        assert SearchTool()._detect_recency("survey from 2019") == "year"

    def test_future_year_uses_month(self):
        from datetime import UTC, datetime

        future = datetime.now(UTC).year + 1
        assert SearchTool()._detect_recency(f"roadmap for {future}") == "month"


class TestAddDateFilter:
    def test_no_recency_returns_unchanged(self):
        assert SearchTool()._add_date_filter("q", "google", None, None) == "q"

    @pytest.mark.parametrize(
        "engine", ["google", "bing", "seznam", "yahoo_japan", "yahoo", "duckduckgo"]
    )
    def test_engine_syntax_never_pollutes_query(self, engine):
        assert SearchTool()._add_date_filter("q", engine, "year", None) == "q"

    def test_duckduckgo_unchanged(self):
        assert SearchTool()._add_date_filter("q", "duckduckgo", "week", None) == "q"


class TestGetSortedUrl:
    def test_unknown_recency_returns_base(self):
        assert SearchTool()._get_sorted_url("google", "http://x", "bogus") == "http://x"

    def test_google_without_query(self):
        assert SearchTool()._get_sorted_url("google", "http://x", "week") == "http://x?tbs=qdr:w"

    def test_google_with_query(self):
        out = SearchTool()._get_sorted_url("google", "http://x?a=1", "month")
        assert out == "http://x?a=1&tbs=qdr:m"

    def test_bing(self):
        out = SearchTool()._get_sorted_url("bing", "http://x?a=1", "year")
        assert "filt=custom" in out and "sc=0-y-0" in out

    def test_duckduckgo(self):
        assert SearchTool()._get_sorted_url("duckduckgo", "http://x", "week").endswith("?df=w")

    def test_yahoo_japan(self):
        assert (
            SearchTool()._get_sorted_url("yahoo_japan", "http://x?p=q", "month").endswith("&vd=m1")
        )

    def test_latest_does_not_mean_last_week(self):
        assert SearchTool()._get_sorted_url("bing", "http://x?a=1", "latest") == "http://x?a=1"


class _FailPage:
    url = "https://www.google.com/search?q=test"

    async def title(self):
        return "t"

    async def inner_text(self, selector):
        return "results result"

    def locator(self, selector):
        return MockLocator(20)

    async def evaluate(self, _script, _limit):
        return [{"title": "Example result", "url": "https://example.test/result", "date": ""}]


@pytest.mark.asyncio
async def test_loaded_page_without_extractable_results_is_failure():
    class EmptyExtractionPage(MockPage):
        async def evaluate(self, _script, _limit):
            return []

    browser = MockBrowser(page=EmptyExtractionPage())
    result = await SearchTool(browser=browser).execute(
        {"query": "neutral report", "engine": "bing"}
    )

    assert result.success is False
    assert "no results" in result.error
    assert result.data["failure_category"] == "selector_drift"
    assert result.data["attempted_engines"] == [
        "bing",
        "yahoo_japan",
        "seznam",
        "yahoo",
        "duckduckgo",
    ]
    assert result.data["search_attempts"][0]["engine"] == "bing"


@pytest.mark.asyncio
async def test_controller_parser_recovers_alternate_results_layout():
    class EmptyJsPage(MockPage):
        async def evaluate(self, _script, _limit):
            return []

    class AlternateLayoutBrowser(MockBrowser):
        async def get_search_results(self, max_results=10):
            return {
                "success": True,
                "results": [
                    {
                        "title": "Recovered result",
                        "link": "https://example.test/recovered",
                        "snippet": "Alternate layout",
                    }
                ],
            }

    browser = AlternateLayoutBrowser(page=EmptyJsPage())
    result = await SearchTool(browser=browser).execute(
        {"query": "neutral report", "engine": "bing"}
    )

    assert result.success is True
    assert result.data["results"][0]["url"] == "https://example.test/recovered"
    assert result.data["search_attempts"] == [{"engine": "bing", "outcome": "success"}]


@pytest.mark.asyncio
async def test_navigation_failure_returns_error():
    class NavFailBrowser(MockBrowser):
        async def goto(self, url, **kwargs):
            return {"success": False, "error": "dns"}

    tool = SearchTool(browser=NavFailBrowser())
    result = await tool.execute({"query": "hello", "engine": "duckduckgo"})
    assert not result.success
    assert "Failed to navigate" in result.error
    assert result.data["failure_category"] == "navigation_failure"
    assert result.data["attempted_engines"] == [
        "duckduckgo",
        "bing",
        "yahoo_japan",
        "seznam",
        "yahoo",
    ]


@pytest.mark.asyncio
async def test_search_navigation_rejects_upstream_http_500() -> None:
    class Http500Browser(MockBrowser):
        async def goto(self, url, **kwargs):
            return {"success": True, "url": url, "title": "", "status": 500}

    tool = SearchTool(browser=Http500Browser())

    error = await tool._navigate(tool._engine_config("yahoo"), "yahoo")

    assert error is not None
    assert error.error == "Failed to navigate to yahoo: HTTP 500"
    assert _classify_search_failure(error.error) == "upstream_http_5xx"


@pytest.mark.asyncio
async def test_recency_triggers_date_sort_goto():
    class RecencyBrowser(MockBrowser):
        def __init__(self):
            super().__init__(page=_FailPage())
            self.goto_urls: list[str] = []

        async def goto(self, url, **kwargs):
            self.goto_urls.append(url)
            self.page.url = url
            return {"success": True, "url": url}

    browser = RecencyBrowser()
    tool = SearchTool(
        browser=browser,
        config=SimpleNamespace(allow_google_search=True),
    )
    result = await tool.execute({"query": "topic", "engine": "google", "recency": "week"})
    assert result.success
    assert any("tbs=qdr:w" in u for u in browser.goto_urls)

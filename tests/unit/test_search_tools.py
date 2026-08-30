"""Tests for search tools."""

from types import SimpleNamespace

import pytest

from webagent.core.models import ToolCall
from webagent.tools.builtin.search_tools import SearchTool, _unwrap_search_redirect
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

    async def goto(self, url):
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
    assert mock_browser.type_called
    assert mock_browser.press_called
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
        if "bing" in url:
            return "bing"
        if "duckduckgo" in url:
            return "duckduckgo"
        if "yahoo" in url:
            return "yahoo"
        return "google"

    async def goto(self, url):
        engine = self._engine_of(url)
        self.page.current = engine
        self.page.url = url
        if not self.engines_tried or self.engines_tried[-1] != engine:
            self.engines_tried.append(engine)
        return {"success": True, "url": url, "title": "Search"}

    async def type_text(self, selector, text, **kwargs):
        return {"success": True}

    async def press_key(self, key):
        return {"success": True}

    async def wait_for_selector(self, selector, **kwargs):
        return {"success": True}


@pytest.mark.asyncio
async def test_search_defaults_to_bing_then_yahoo_without_google():
    """Default automation must not open Google's human-verification page."""
    import webagent.tools.builtin.search_tools  # noqa: F401

    browser = _CascadeBrowser(_CascadePage(blocked={"bing"}))
    registry = ToolRegistry()
    registry.auto_discover(browser=browser)
    ex = ToolExecutor(registry)

    result = await ex.execute(
        ToolCall(tool_name="search", parameters={"query": "neutral encyclopedia topic"})
    )

    assert result.success is True
    assert result.data["engine"] == "yahoo"
    assert result.data["fallback_from"] == "bing"
    assert browser.engines_tried == ["bing", "yahoo"]


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

    @pytest.mark.parametrize("engine", ["google", "bing", "yahoo", "duckduckgo"])
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
    assert result.data["attempted_engines"] == ["bing", "yahoo", "duckduckgo"]
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
        async def goto(self, url):
            return {"success": False, "error": "dns"}

    tool = SearchTool(browser=NavFailBrowser())
    result = await tool.execute({"query": "hello", "engine": "duckduckgo"})
    assert not result.success
    assert "Failed to navigate" in result.error
    assert result.data["failure_category"] == "navigation_failure"
    assert result.data["attempted_engines"] == ["duckduckgo", "bing", "yahoo"]


@pytest.mark.asyncio
async def test_recency_triggers_date_sort_goto():
    class RecencyBrowser(MockBrowser):
        def __init__(self):
            super().__init__(page=_FailPage())
            self.goto_urls: list[str] = []

        async def goto(self, url):
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

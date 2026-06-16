"""Tests for search tools."""

import pytest

from webagent.core.models import ToolCall
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
    assert result.data["engine"] == "google"


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
async def test_search_returns_direct_arxiv_candidates_for_qwen_when_engines_blocked():
    import webagent.tools.builtin.search_tools  # noqa: F401

    error_page = MockPage(body="Unexpected error. Please try again.", link_count=0)
    browser = MockBrowser(page=error_page)
    registry = ToolRegistry()
    registry.auto_discover(browser=browser)
    ex = ToolExecutor(registry)

    result = await ex.execute(
        ToolCall(tool_name="search", parameters={"query": "Qwen technical report PDF"})
    )

    assert result.success is True
    assert result.data["engine"] == "direct_arxiv_fallback"
    assert result.data["results"][0]["pdf_url"] == "https://arxiv.org/pdf/2604.15804"


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
async def test_search_cascades_through_bing_before_duckduckgo():
    """Regression: Bing used to be skipped — the fallback jumped straight to
    DuckDuckGo. With Google and Bing blocked, the cascade must try all three in
    order and succeed on DuckDuckGo.
    """
    import webagent.tools.builtin.search_tools  # noqa: F401

    browser = _CascadeBrowser(_CascadePage(blocked={"google", "bing"}))
    registry = ToolRegistry()
    registry.auto_discover(browser=browser)
    ex = ToolExecutor(registry)

    result = await ex.execute(
        ToolCall(tool_name="search", parameters={"query": "neutral encyclopedia topic"})
    )

    assert result.success is True
    assert result.data["engine"] == "duckduckgo"
    assert result.data["fallback_from"] == "google"
    # All three engines were attempted, in the documented order.
    assert browser.engines_tried == ["google", "bing", "duckduckgo"]


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

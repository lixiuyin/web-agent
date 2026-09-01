"""Tests for interaction tools."""

from unittest.mock import AsyncMock

import pytest

from webagent.core.models import ToolCall
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry


class MockBrowser:
    """Mock browser controller for testing interaction tools."""

    def __init__(self):
        self.page = MockPage()

    async def hover(self, selector):
        return {"success": True, "selector": selector}

    async def select_option(self, selector, **kwargs):
        return {"success": True, "selector": selector, "option": kwargs}

    async def wait_for_selector(self, selector, state="visible", **kwargs):
        return {"success": True, "selector": selector, "state": state}

    async def get_attribute(self, selector, attribute):
        return {
            "success": True,
            "selector": selector,
            "attribute": attribute,
            "value": "test-value",
        }

    async def get_all_links(self, **kwargs):
        # Return different results based on filter parameters
        links = [
            {"href": "https://example.com", "text": "Example"},
            {"href": "#anchor", "text": "Anchor"},
            {"href": "javascript:void(0)", "text": "JS Link"},
            {"href": "/relative", "text": "Relative"},
        ]
        filtered_links = links.copy()

        if kwargs.get("skip_anchors"):
            filtered_links = [link for link in filtered_links if not link["href"].startswith("#")]
        if kwargs.get("skip_javascript"):
            filtered_links = [
                link for link in filtered_links if not link["href"].startswith("javascript:")
            ]
        if kwargs.get("filter_external_only"):
            filtered_links = [link for link in filtered_links if link["href"].startswith("http")]

        max_res = kwargs.get("max_results", len(filtered_links))
        return {
            "success": True,
            "links": filtered_links[:max_res],
            "count": len(filtered_links[:max_res]),
            "total_count": len(filtered_links),
        }

    async def get_search_results(self, max_results=10):
        all_results = [
            {
                "title": "Qwen Technical Report",
                "link": "https://arxiv.org/pdf/2505.09388",
                "snippet": "Latest report",
            },
            {
                "title": "Qwen GitHub",
                "link": "https://github.com/QwenLM/Qwen",
                "snippet": "Source code",
            },
        ]
        results = all_results[:max_results]
        return {
            "success": True,
            "results": results,
            "count": len(results),
        }

    async def refresh(self):
        return {"success": True, "url": "https://example.com"}

    async def scroll_to_element(self, selector):
        return {"success": True, "selector": selector}


class MockPage:
    """Mock Playwright page."""

    @property
    def url(self):
        return "https://example.com"

    async def title(self):
        return "Example Page"


@pytest.fixture
def mock_browser():
    return MockBrowser()


@pytest.fixture
def tool_executor(mock_browser):
    """Create a ToolExecutor with mocked browser."""
    import webagent.tools.builtin.interaction_tools  # noqa: F401

    registry = ToolRegistry()
    registry.auto_discover(browser=mock_browser)
    return ToolExecutor(registry)


# Hover Tool Tests
@pytest.mark.asyncio
async def test_hover_tool_success(tool_executor):
    """Test successful hover execution."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="hover",
            parameters={"selector": {"type": "css", "value": "#button"}},
            reasoning="Hover button",
        )
    )

    assert result.success is True
    assert result.data["selector"] == {"type": "css", "value": "#button"}


@pytest.mark.asyncio
async def test_hover_tool_validation_error(tool_executor):
    """Test hover tool parameter validation."""
    result = await tool_executor.execute(
        ToolCall(tool_name="hover", parameters={}, reasoning="Missing selector")
    )

    assert result.success is False
    assert "Validation" in (result.error or "")


# Select Dropdown Tool Tests
@pytest.mark.asyncio
async def test_select_dropdown_by_value(tool_executor):
    """Test selecting dropdown option by value."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="select_dropdown",
            parameters={"selector": {"type": "css", "value": "select"}, "value": "option1"},
            reasoning="Select option",
        )
    )

    assert result.success is True
    assert result.data["option"]["value"] == "option1"


@pytest.mark.asyncio
async def test_select_dropdown_by_label(tool_executor):
    """Test selecting dropdown option by label."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="select_dropdown",
            parameters={"selector": {"type": "css", "value": "select"}, "label": "Option 1"},
            reasoning="Select option",
        )
    )

    assert result.success is True
    assert result.data["option"]["label"] == "Option 1"


@pytest.mark.asyncio
async def test_select_dropdown_validation_error(tool_executor):
    """Test select dropdown with no selection method."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="select_dropdown",
            parameters={"selector": {"type": "css", "value": "select"}},
            reasoning="No selection method",
        )
    )

    assert result.success is False
    assert "not valid under any of the given schemas" in (result.error or "").lower()


# Wait For Element Tool Tests
@pytest.mark.asyncio
async def test_wait_for_element_visible(tool_executor):
    """Test waiting for element to be visible."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="wait_for_element",
            parameters={"selector": {"type": "css", "value": "#loading"}, "state": "visible"},
            reasoning="Wait for loading",
        )
    )

    assert result.success is True
    assert result.data["state"] == "visible"


@pytest.mark.asyncio
async def test_wait_for_element_invalid_state(tool_executor):
    """Test wait_for_element with invalid state."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="wait_for_element",
            parameters={"selector": {"type": "css", "value": "#x"}, "state": "invalid"},
            reasoning="Invalid state",
        )
    )

    assert result.success is False
    assert "state" in (result.error or "").lower()


# Get Attribute Tool Tests
@pytest.mark.asyncio
async def test_get_attribute_success(tool_executor):
    """Test getting element attribute."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_attribute",
            parameters={"selector": {"type": "css", "value": "a.link"}, "attribute": "href"},
            reasoning="Get href",
        )
    )

    assert result.success is True
    assert result.data["value"] == "test-value"
    assert result.data["attribute"] == "href"


@pytest.mark.asyncio
async def test_get_attribute_validation_error(tool_executor):
    """Test get_attribute with missing attribute parameter."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_attribute",
            parameters={"selector": {"type": "css", "value": "a"}},
            reasoning="Missing attribute",
        )
    )

    assert result.success is False
    assert "attribute" in (result.error or "").lower()


# Get All Links Tool Tests
@pytest.mark.asyncio
async def test_get_all_links_success(tool_executor):
    """Test extracting all links."""
    result = await tool_executor.execute(
        ToolCall(tool_name="get_all_links", parameters={}, reasoning="Get links")
    )

    assert result.success is True
    # Without filters, returns all 4 mock links
    assert result.data["total_count"] == 4
    assert result.data["returned"] == 4


# Get URL Tool Tests
@pytest.mark.asyncio
async def test_get_url_success(tool_executor):
    """Test getting current URL."""
    result = await tool_executor.execute(
        ToolCall(tool_name="get_url", parameters={}, reasoning="Get URL")
    )

    assert result.success is True
    assert result.data["url"] == "https://example.com"


# Get Title Tool Tests
@pytest.mark.asyncio
async def test_get_title_success(tool_executor):
    """Test getting page title."""
    result = await tool_executor.execute(
        ToolCall(tool_name="get_title", parameters={}, reasoning="Get title")
    )

    assert result.success is True
    assert result.data["title"] == "Example Page"


# Refresh Tool Tests
@pytest.mark.asyncio
async def test_refresh_success(tool_executor):
    """Test refreshing page."""
    result = await tool_executor.execute(
        ToolCall(tool_name="refresh", parameters={}, reasoning="Refresh page")
    )

    assert result.success is True
    assert result.data["url"] == "https://example.com"


# Scroll To Element Tool Tests
@pytest.mark.asyncio
async def test_scroll_to_element_success(tool_executor):
    """Test scrolling element into view."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="scroll_to_element",
            parameters={"selector": {"type": "css", "value": "#footer"}},
            reasoning="Scroll to footer",
        )
    )

    assert result.success is True
    assert result.data["selector"] == {"type": "css", "value": "#footer"}


# Get All Links with Filter Tests
@pytest.mark.asyncio
async def test_get_all_links_with_filters(tool_executor):
    """Test getting links with filters applied."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_all_links",
            parameters={
                "skip_anchors": True,
                "skip_javascript": True,
                "filter_external_only": True,
            },
            reasoning="Get filtered links",
        )
    )

    assert result.success is True
    # With all filters on, should only get the https://example.com link
    assert len(result.data["links"]) == 1
    assert result.data["links"][0]["href"] == "https://example.com"


@pytest.mark.asyncio
async def test_get_all_links_max_results(tool_executor):
    """Test max_results parameter."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_all_links",
            parameters={"max_results": 2},
            reasoning="Get limited links",
        )
    )

    assert result.success is True
    assert result.data["returned"] <= 2


@pytest.mark.asyncio
async def test_get_all_links_invalid_max_results(tool_executor):
    """Test invalid max_results parameter."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_all_links",
            parameters={"max_results": 2000},
            reasoning="Invalid max",
        )
    )

    assert result.success is False
    assert "max_results" in (result.error or "").lower()


# Get Search Results Tool Tests
@pytest.mark.asyncio
async def test_get_search_results_success(tool_executor):
    """Test extracting search results with top 5 shown by default."""
    result = await tool_executor.execute(
        ToolCall(tool_name="get_search_results", parameters={}, reasoning="Get search results")
    )

    assert result.success is True
    # When 2 results exist, both are shown (less than default 5)
    assert result.data["count"] == 2  # Both results shown
    assert result.data["total_available"] == 2
    assert "more_results" not in result.data
    assert len(result.data["results"]) == 2
    assert result.data["results"][0]["title"] == "Qwen Technical Report"
    assert result.data["results"][0]["url"] == "https://arxiv.org/pdf/2505.09388"
    assert result.data["results"][0]["link"] == "https://arxiv.org/pdf/2505.09388"


@pytest.mark.asyncio
async def test_get_search_results_unwraps_bing_destination(tool_executor, mock_browser):
    wrapped = (
        "https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9kb2NzLnB5dGhvbi5vcmcvMy9mYXEvZ2VuZXJhbC5odG1s"
    )
    mock_browser.get_search_results = AsyncMock(
        return_value={
            "success": True,
            "engine": "bing",
            "query": "python faq",
            "results": [{"title": "Python FAQ", "link": wrapped, "snippet": "FAQ"}],
            "count": 1,
        }
    )

    result = await tool_executor.execute(
        ToolCall(tool_name="get_search_results", parameters={}, reasoning="Get results")
    )

    assert result.data["results"][0]["url"] == ("https://docs.python.org/3/faq/general.html")


@pytest.mark.asyncio
async def test_get_search_results_show_all(tool_executor):
    """Test show_all parameter to show all results."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_search_results",
            parameters={"show_all": True},
            reasoning="Get all results",
        )
    )

    assert result.success is True
    # show_all=true should show all available results
    assert result.data["count"] == 2
    assert result.data["total_available"] == 2
    assert "more_results" not in result.data
    assert len(result.data["results"]) == 2


@pytest.mark.asyncio
async def test_get_search_results_invalid_max_results(tool_executor):
    """Test invalid max_results parameter."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_search_results",
            parameters={"max_results": 0},
            reasoning="Invalid max",
        )
    )

    assert result.success is False
    assert "max_results" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_get_search_results_invalid_show_all(tool_executor):
    """Test invalid show_all parameter."""
    result = await tool_executor.execute(
        ToolCall(
            tool_name="get_search_results",
            parameters={"show_all": "yes"},
            reasoning="Invalid show_all",
        )
    )

    assert result.success is False
    assert "show_all" in (result.error or "").lower()

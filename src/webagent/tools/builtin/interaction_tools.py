"""Additional browser interaction tools beyond basic navigation."""

from __future__ import annotations

from typing import Any

from webagent.core.models import ToolResult
from webagent.tools.builtin.browser_tools import _resolve_selector, _validate_selector
from webagent.tools.registry import tool


@tool("hover", "Hover over element. params: selector={type:'text'|'css', value:(string)}")
class HoverTool:
    """Hover over an element to trigger dropdowns, tooltips, or other hover effects.

    Examples:
    - Hover by text: {"selector": {"type": "text", "value": "Menu"}}
    - Hover by CSS: {"selector": {"type": "css", "value": ".dropdown-trigger"}}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        resp = await self.browser.hover(selector)
        if resp.get("success"):
            return ToolResult(
                success=True, tool_name="hover", data={"selector": params["selector"]}
            )
        return ToolResult(success=False, tool_name="hover", error=resp.get("error", "Hover failed"))


@tool(
    "select_dropdown",
    "Select from dropdown. params: selector={type:'text'|'css', value:(string)}, value?, label?, index?",
)
class SelectDropdownTool:
    """Select an option from a <select> dropdown element.

    Examples:
    - By value: {"selector": {"type": "css", "value": "select"}, "value": "option1"}
    - By label: {"selector": {"type": "text", "value": "Choose Country"}, "label": "United States"}
    - By index: {"selector": {"type": "css", "value": "select"}, "index": 0}

    Specify one of:
    - value: The option value attribute
    - label: The visible text of the option
    - index: The 0-based index of the option
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))

        # At least one selection method must be provided
        if not any(k in params for k in ("value", "label", "index")):
            raise ValueError("Must specify one of: value, label, or index")

        if "index" in params:
            idx = params["index"]
            if not isinstance(idx, int) or idx < 0:
                raise ValueError("'index' must be a non-negative integer")

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        resp = await self.browser.select_option(
            selector,
            value=params.get("value"),
            label=params.get("label"),
            index=params.get("index"),
        )
        if resp.get("success"):
            return ToolResult(
                success=True,
                tool_name="select_dropdown",
                data={"selector": params["selector"], "option": resp.get("option")},
            )
        return ToolResult(
            success=False, tool_name="select_dropdown", error=resp.get("error", "Selection failed")
        )


@tool(
    "wait_for_element",
    "Wait for element state. params: selector={type:'text'|'css', value:(string)}, state=visible|hidden|attached|detached, timeout_ms=30000",
)
class WaitForElementTool:
    """Wait for an element to reach a specific state.

    Examples:
    - Wait for visible: {"selector": {"type": "text", "value": "Loading..."}, "state": "visible"}
    - Wait for hidden: {"selector": {"type": "css", "value": ".spinner"}, "state": "hidden"}
    - Custom timeout: {"selector": {"type": "text", "value": "Result"}, "state": "visible", "timeout_ms": 10000}

    States:
    - visible: Element is visible in the viewport
    - hidden: Element is hidden or not visible
    - attached: Element is present in the DOM
    - detached: Element is removed from the DOM
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))

        state = params.get("state", "visible")
        if state not in ("visible", "hidden", "attached", "detached"):
            raise ValueError("'state' must be one of: visible, hidden, attached, detached")

        timeout = params.get("timeout_ms", 30000)
        if not isinstance(timeout, int) or timeout < 0 or timeout > 120000:
            raise ValueError("'timeout_ms' must be between 0 and 120000")

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        state = params.get("state", "visible")
        timeout = params.get("timeout_ms", 30000)

        resp = await self.browser.wait_for_selector(selector, state=state, timeout=timeout)
        if resp.get("success"):
            return ToolResult(
                success=True,
                tool_name="wait_for_element",
                data={"selector": params["selector"], "state": state},
            )
        return ToolResult(
            success=False,
            tool_name="wait_for_element",
            error=resp.get("error", f"Element did not reach '{state}' state"),
        )


@tool(
    "get_attribute",
    "Get element attribute. params: selector={type:'text'|'css', value:(string)}, attribute (string)",
)
class GetAttributeTool:
    """Get the value of an element's attribute (e.g., href, src, data-*, id, class, etc.).

    Examples:
    - Get href: {"selector": {"type": "text", "value": "Link"}, "attribute": "href"}
    - Get src: {"selector": {"type": "css", "value": "img.thumbnail"}, "attribute": "src"}
    - Get data attribute: {"selector": {"type": "css", "value": "[data-id]"}, "attribute": "data-id"}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))
        if not isinstance(params.get("attribute"), str) or not params["attribute"].strip():
            raise ValueError("'attribute' must be a non-empty string")

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        attribute = params["attribute"].strip()

        resp = await self.browser.get_attribute(selector, attribute)
        if resp.get("success"):
            return ToolResult(
                success=True,
                tool_name="get_attribute",
                data={
                    "selector": params["selector"],
                    "attribute": attribute,
                    "value": resp.get("value"),
                },
            )
        return ToolResult(
            success=False,
            tool_name="get_attribute",
            error=resp.get("error", "Failed to get attribute"),
        )


@tool(
    "get_all_links",
    "Extract all links from page. params: skip_anchors=false, skip_javascript=false, filter_external_only=false, max_results=100",
)
class GetAllLinksTool:
    """Extract all links (hrefs and text) from the current page with optional filtering.

    Filter options:
    - skip_anchors: Skip anchor links (#)
    - skip_javascript: Skip javascript: links
    - filter_external_only: Only return http/https links
    - max_results: Maximum number of links to return (default 100)
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        # Validate boolean parameters
        for param in ("skip_anchors", "skip_javascript", "filter_external_only"):
            if param in params and not isinstance(params[param], bool):
                raise ValueError(f"'{param}' must be a boolean")

        # Validate max_results
        if "max_results" in params:
            max_res = params["max_results"]
            if not isinstance(max_res, int) or max_res < 0 or max_res > 1000:
                raise ValueError("'max_results' must be between 0 and 1000")

    async def execute(self, params: dict) -> ToolResult:
        resp = await self.browser.get_all_links(
            skip_anchors=params.get("skip_anchors", False),
            skip_javascript=params.get("skip_javascript", False),
            filter_external_only=params.get("filter_external_only", False),
            max_results=params.get("max_results", 100),
        )
        if resp.get("success"):
            links = resp.get("links", [])
            return ToolResult(
                success=True,
                tool_name="get_all_links",
                data={"links": links, "total_count": resp.get("count", 0), "returned": len(links)},
            )
        return ToolResult(
            success=False,
            tool_name="get_all_links",
            error=resp.get("error", "Failed to extract links"),
        )


@tool("get_url", "Get current page URL. params: none")
class GetUrlTool:
    """Get the current page URL."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        pass

    async def execute(self, params: dict) -> ToolResult:
        try:
            url = self.browser.page.url
            return ToolResult(success=True, tool_name="get_url", data={"url": url})
        except Exception as e:
            return ToolResult(success=False, tool_name="get_url", error=str(e))


@tool("get_title", "Get current page title. params: none")
class GetTitleTool:
    """Get the current page title."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        pass

    async def execute(self, params: dict) -> ToolResult:
        try:
            title = await self.browser.page.title()
            return ToolResult(success=True, tool_name="get_title", data={"title": title})
        except Exception as e:
            return ToolResult(success=False, tool_name="get_title", error=str(e))


@tool("refresh", "Refresh current page. params: none")
class RefreshTool:
    """Refresh/reload the current page."""

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        pass

    async def execute(self, params: dict) -> ToolResult:
        resp = await self.browser.refresh()
        if resp.get("success"):
            return ToolResult(
                success=True,
                tool_name="refresh",
                data={"url": resp.get("url"), "title": await self.browser.page.title()},
            )
        return ToolResult(
            success=False, tool_name="refresh", error=resp.get("error", "Refresh failed")
        )


@tool(
    "scroll_to_element",
    "Scroll element into view. params: selector={type:'text'|'css', value:(string)}",
)
class ScrollToElementTool:
    """Scroll a specific element into view.

    Examples:
    - Scroll by text: {"selector": {"type": "text", "value": "Footer"}}
    - Scroll by CSS: {"selector": {"type": "css", "value": "#comments-section"}}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        resp = await self.browser.scroll_to_element(selector)
        if resp.get("success"):
            return ToolResult(
                success=True, tool_name="scroll_to_element", data={"selector": params["selector"]}
            )
        return ToolResult(
            success=False, tool_name="scroll_to_element", error=resp.get("error", "Scroll failed")
        )


@tool(
    "get_search_results", "Extract search results from page. params: max_results=10, show_all=false"
)
class GetSearchResultsTool:
    """Extract structured search results from search engines (Google, Bing, DuckDuckGo).

    Automatically detects the search engine and extracts results with:
    - title: Result title
    - link: Result URL
    - snippet: Result description/snippet

    By default shows top 5 results for comparison. Use show_all=true to see all results.

    For non-search engine pages, falls back to filtered link extraction.
    """

    # Keywords that indicate the user needs to compare multiple results
    COMPARISON_KEYWORDS = [
        "most recent",
        "latest",
        "newest",
        "compare",
        "best",
        "top",
        "which",
        "what is",
        "find the",
        "all",
        "list",
        "options",
    ]

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        max_res = params.get("max_results", 10)
        if not isinstance(max_res, int) or max_res < 1 or max_res > 100:
            raise ValueError("'max_results' must be between 1 and 100")

        show_all = params.get("show_all", False)
        if not isinstance(show_all, bool):
            raise ValueError("'show_all' must be a boolean")

    async def execute(self, params: dict) -> ToolResult:
        max_results = params.get("max_results", 10)
        show_all = params.get("show_all", False)
        resp = await self.browser.get_search_results(max_results=max_results)
        if resp.get("success"):
            results = resp.get("results", [])
            total_count = resp.get("count", 0)

            # Format results for LLM consumption
            # By default, show top 5 results with full details, summarize the rest
            # If show_all=True, show all results
            default_shown = 5
            if show_all:
                # Show all results with full details
                formatted_results = [
                    {
                        "title": r.get("title", ""),
                        "link": r.get("link", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in results
                ]
                data = {
                    "results": formatted_results,
                    "count": len(results),
                    "total_available": total_count,
                }
            elif len(results) > default_shown:
                # Show top N results in detail, summarize the rest
                top_results = results[:default_shown]
                formatted_results = [
                    {
                        "title": r.get("title", ""),
                        "link": r.get("link", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in top_results
                ]
                remaining_count = len(results) - default_shown
                data = {
                    "results": formatted_results,
                    "count": default_shown,
                    "total_available": total_count,
                    "more_results": f"... and {remaining_count} more result(s)",
                }
            else:
                # Show all results (less than default_shown)
                formatted_results = [
                    {
                        "title": r.get("title", ""),
                        "link": r.get("link", ""),
                        "snippet": r.get("snippet", ""),
                    }
                    for r in results
                ]
                data = {
                    "results": formatted_results,
                    "count": len(results),
                    "total_available": total_count,
                }

            return ToolResult(
                success=True,
                tool_name="get_search_results",
                data=data,
            )
        return ToolResult(
            success=False,
            tool_name="get_search_results",
            error=resp.get("error", "Failed to extract search results"),
        )

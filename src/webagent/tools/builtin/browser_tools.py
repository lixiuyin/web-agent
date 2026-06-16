"""Browser navigation and interaction tools."""

from __future__ import annotations

import asyncio
from typing import Any

from webagent.core.models import ToolResult
from webagent.tools.registry import tool

# URL schemes the navigation tool must refuse (the URL is partly page/LLM-driven).
_DISALLOWED_GOTO_SCHEMES = (
    "file:",
    "data:",
    "javascript:",
    "blob:",
    "about:",
    "view-source:",
    "chrome:",
)


def _resolve_selector(selector: dict[str, str]) -> str:
    """Convert structured selector to Playwright selector string.

    Valid selector types:
    - text: Match by visible text content (case-insensitive, substring match)
    - css: Match by CSS selector

    Examples:
    - {"type": "text", "value": "Submit"} -> text="Submit"
    - {"type": "css", "value": "#submit-btn"} -> #submit-btn
    """
    sel_type = selector["type"]
    sel_value = selector["value"]
    if sel_type == "css":
        return sel_value
    if sel_type == "text":
        # Use exact text matching with quotes to handle special characters
        escaped_value = sel_value.replace('"', '\\"')
        return f'text="{escaped_value}"'
    raise ValueError(f"Unknown selector type: {sel_type}. Valid types are: 'text', 'css'")


def _validate_selector(selector: Any) -> None:
    if not isinstance(selector, dict):
        raise ValueError("selector must be {type, value}")
    if selector.get("type") not in ("text", "css"):
        raise ValueError("selector.type must be 'text' or 'css'")
    if not isinstance(selector.get("value"), str):
        raise ValueError("selector.value must be a string")


@tool("goto", "Navigate to URL. params: url (string), wait_until=load|domcontentloaded|networkidle")
class GotoTool:
    """Navigate to a specific URL.

    Example: {"url": "https://example.com", "wait_until": "load"}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("url"), str) or not params["url"].strip():
            raise ValueError("'url' required")
        # Block dangerous schemes (the URL is partly page/LLM-controlled). file://
        # would read local files into a screenshot; data:/blob:/javascript: are
        # injection vectors. A denylist avoids false-positives on bare hosts and
        # host:port that an allowlist via urlparse would misclassify.
        if params["url"].strip().lower().startswith(_DISALLOWED_GOTO_SCHEMES):
            raise ValueError(f"Disallowed URL scheme: {params['url'].strip()!r}")

    async def execute(self, params: dict) -> ToolResult:
        url = str(params["url"]).strip()
        wait = str(params.get("wait_until", "load"))
        resp = await self.browser.goto(url, wait_until=wait or "load")
        if resp.get("success"):
            return ToolResult(
                success=True,
                tool_name="goto",
                data={"url": resp.get("url"), "title": resp.get("title")},
            )
        return ToolResult(
            success=False, tool_name="goto", error=resp.get("error", "Navigation failed")
        )


@tool("click", "Click element. params: selector={type:'text'|'css', value:(string)}, force=false")
class ClickTool:
    """Click on an element using text or CSS selector.

    Examples:
    - Click by text: {"selector": {"type": "text", "value": "Submit Button"}}
    - Click by CSS: {"selector": {"type": "css", "value": "#submit-btn"}}
    - Click with force: {"selector": {"type": "text", "value": "Link"}, "force": true}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        force = bool(params.get("force", False))
        resp = await self.browser.click(selector, force=force)
        if resp.get("success"):
            return ToolResult(
                success=True, tool_name="click", data={"selector": params["selector"]}
            )
        return ToolResult(success=False, tool_name="click", error=resp.get("error", "Click failed"))


@tool("click_link", "Click link by text (fuzzy matching). params: text (string), fuzzy=true")
class ClickLinkTool:
    """Click a link using flexible text matching.

    This tool is designed for clicking links where the exact text might not match
    what's shown in search results. It tries multiple strategies:
    1. Exact text match
    2. Fuzzy text match (substring)
    3. Keyword matching (finds links containing multiple words from the search text)
    4. URL pattern matching (arXiv IDs, DOIs, etc.)

    Examples:
    - Click search result: {"text": "Qwen Technical Report arXiv.org"}
    - Click by title: {"text": "Download PDF"}
    - Click by arXiv ID: {"text": "[2505.09388] Qwen3 Technical Report"}

    Use this instead of 'click' when dealing with search results or links with
    inconsistent text formatting.
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("text"), str) or not params["text"].strip():
            raise ValueError("'text' parameter is required and must be non-empty")

        fuzzy = params.get("fuzzy", True)
        if not isinstance(fuzzy, bool):
            raise ValueError("'fuzzy' must be a boolean")

    async def execute(self, params: dict) -> ToolResult:
        text = str(params["text"]).strip()
        fuzzy = bool(params.get("fuzzy", True))

        resp = await self.browser.click_link_by_text(text, fuzzy=fuzzy)
        if resp.get("success"):
            data = {"text": text, "method": resp.get("method", "unknown")}
            if "found_text" in resp:
                data["found_text"] = resp["found_text"]
            if "found_href" in resp:
                data["found_href"] = resp["found_href"]
            return ToolResult(success=True, tool_name="click_link", data=data)
        return ToolResult(
            success=False, tool_name="click_link", error=resp.get("error", "Click link failed")
        )


@tool(
    "type",
    "Type text into element. params: selector={type:'text'|'css', value:(string)}, text (string), delay_ms=50",
)
class TypeTool:
    """Type text into an input field or textarea.

    Examples:
    - Type by text selector: {"selector": {"type": "text", "value": "Search"}, "text": "query"}
    - Type by CSS selector: {"selector": {"type": "css", "value": "#search-box"}, "text": "hello"}
    - With custom delay: {"selector": {"type": "text", "value": "Input"}, "text": "text", "delay_ms": 100}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        _validate_selector(params.get("selector"))
        if not isinstance(params.get("text"), str):
            raise ValueError("'text' required")

    async def execute(self, params: dict) -> ToolResult:
        selector = _resolve_selector(params["selector"])
        text = str(params["text"])
        delay = int(params.get("delay_ms", 50))
        clear = bool(params.get("clear_first", True))
        try:
            await self.browser.click(selector, force=False)
            await asyncio.sleep(0.2)
        except Exception:
            pass
        resp = await self.browser.type_text(selector, text, delay=delay, clear_first=clear)
        if resp.get("success"):
            return ToolResult(
                success=True, tool_name="type", data={"selector": params["selector"], "text": text}
            )
        return ToolResult(success=False, tool_name="type", error=resp.get("error", "Type failed"))


@tool(
    "press",
    "Press keyboard key. params: key (string), selector={type:'text'|'css', value:(string)}?",
)
class PressTool:
    """Press a keyboard key, optionally on a specific element.

    Common keys: Enter, Tab, Escape, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Home, End, PageUp, PageDown

    Examples:
    - Press Enter globally: {"key": "Enter"}
    - Press Tab on an element: {"key": "Tab", "selector": {"type": "text", "value": "Input"}}
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        if not isinstance(params.get("key"), str) or not params["key"].strip():
            raise ValueError("'key' required")

    async def execute(self, params: dict) -> ToolResult:
        key = str(params["key"]).strip()
        selector = None
        if params.get("selector"):
            _validate_selector(params["selector"])
            selector = _resolve_selector(params["selector"])
        resp = await self.browser.press_key(key, selector=selector)
        if resp.get("success"):
            return ToolResult(success=True, tool_name="press", data={"key": key})
        return ToolResult(success=False, tool_name="press", error=resp.get("error", "Press failed"))


@tool("scroll", "Scroll page. params: direction='up'|'down', amount_px=500")
class ScrollTool:
    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        if params.get("direction") and params["direction"] not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")

    async def execute(self, params: dict) -> ToolResult:
        direction = str(params.get("direction", "down"))
        amount = int(params.get("amount_px", 500))
        resp = await self.browser.scroll(direction=direction, amount=amount)
        if resp.get("success"):
            return ToolResult(success=True, tool_name="scroll", data=resp)
        return ToolResult(success=False, tool_name="scroll", error=resp.get("error"))


@tool("wait", "Sleep for milliseconds. params: ms (int, 0-60000)")
class WaitTool:
    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        ms = params.get("ms", 1000)
        if not isinstance(ms, int) or ms < 0 or ms > 60000:
            raise ValueError("ms must be 0-60000")

    async def execute(self, params: dict) -> ToolResult:
        ms = int(params.get("ms", 1000))
        await self.browser.wait(ms)
        return ToolResult(success=True, tool_name="wait", data={"waited_ms": ms})


@tool("forward", "Go forward in browser history. params: steps=1")
class ForwardTool:
    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        pass

    async def execute(self, params: dict) -> ToolResult:
        steps = params.get("steps", 1)
        for _ in range(int(steps)):
            await self.browser.page.go_forward()
        return ToolResult(success=True, tool_name="forward", data={"steps": steps})


@tool("back", "Go back in browser history. params: steps=1")
class BackTool:
    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

    def validate_params(self, params: dict) -> None:
        pass

    async def execute(self, params: dict) -> ToolResult:
        steps = params.get("steps", 1)
        for _ in range(int(steps)):
            await self.browser.page.go_back()
        return ToolResult(success=True, tool_name="back", data={"steps": steps})

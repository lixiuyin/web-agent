"""Branch tests for the browser navigation/interaction tools.

A small fake browser records calls and returns canned responses so the tool
wrappers (validation, selector resolution, success/failure shaping) are exercised
without a real Playwright session.
"""

from __future__ import annotations

from typing import Any

import pytest

from webagent.tools.builtin.browser_tools import (
    BackTool,
    ClickLinkTool,
    ClickTool,
    ForwardTool,
    GotoTool,
    PressTool,
    ScrollTool,
    TypeTool,
    WaitTool,
    _resolve_selector,
    _validate_selector,
)
from webagent.tools.builtin.interaction_tools import GetAttributeTool


class FakePage:
    def __init__(self) -> None:
        self.forward = 0
        self.back = 0

    async def go_forward(self) -> None:
        self.forward += 1

    async def go_back(self) -> None:
        self.back += 1


class FakeBrowser:
    def __init__(self, **responses: Any) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.page = FakePage()

    def _record(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, args, kwargs))
        return self.responses.get(name, {"success": True})

    async def goto(self, url: str, wait_until: str = "load") -> dict[str, Any]:
        return self._record("goto", url, wait_until=wait_until)

    async def click(self, selector: str, force: bool = False) -> dict[str, Any]:
        return self._record("click", selector, force=force)

    async def click_link_by_text(self, text: str, fuzzy: bool = True) -> dict[str, Any]:
        return self._record("click_link_by_text", text, fuzzy=fuzzy)

    async def type_text(
        self, selector: str, text: str, delay: int = 50, clear_first: bool = True
    ) -> dict[str, Any]:
        return self._record("type_text", selector, text, delay=delay, clear_first=clear_first)

    async def press_key(self, key: str, selector: str | None = None) -> dict[str, Any]:
        return self._record("press_key", key, selector=selector)

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict[str, Any]:
        return self._record("scroll", direction=direction, amount=amount)

    async def wait(self, ms: int) -> dict[str, Any]:
        return self._record("wait", ms)

    async def get_attribute(self, selector: str, attribute: str) -> dict[str, Any]:
        return self._record("get_attribute", selector, attribute)


class TestSelectorHelpers:
    def test_resolve_css(self) -> None:
        assert _resolve_selector({"type": "css", "value": "#id"}) == "#id"

    def test_resolve_text_escapes_quotes(self) -> None:
        assert _resolve_selector({"type": "text", "value": 'Say "hi"'}) == 'text="Say \\"hi\\""'

    def test_resolve_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            _resolve_selector({"type": "xpath", "value": "//a"})

    def test_validate_selector_errors(self) -> None:
        with pytest.raises(ValueError):
            _validate_selector("not a dict")
        with pytest.raises(ValueError):
            _validate_selector({"type": "bad", "value": "x"})
        with pytest.raises(ValueError):
            _validate_selector({"type": "css", "value": 123})


class TestGoto:
    async def test_validation(self) -> None:
        tool = GotoTool(browser=FakeBrowser())
        with pytest.raises(ValueError):
            tool.validate_params({"url": ""})
        with pytest.raises(ValueError):
            tool.validate_params({"url": "file:///etc/passwd"})
        tool.validate_params({"url": "https://example.com"})

    async def test_success(self) -> None:
        browser = FakeBrowser(goto={"success": True, "url": "https://x", "title": "X"})
        result = await GotoTool(browser=browser).execute({"url": "https://x"})
        assert result.success and result.data["title"] == "X"

    async def test_failure(self) -> None:
        browser = FakeBrowser(goto={"success": False, "error": "timeout"})
        result = await GotoTool(browser=browser).execute({"url": "https://x"})
        assert not result.success and result.error == "timeout"


class TestClick:
    async def test_success(self) -> None:
        browser = FakeBrowser(click={"success": True})
        result = await ClickTool(browser=browser).execute(
            {"selector": {"type": "css", "value": "#b"}, "force": True}
        )
        assert result.success
        assert browser.calls[0] == ("click", ("#b",), {"force": True})

    async def test_failure(self) -> None:
        browser = FakeBrowser(click={"success": False})
        result = await ClickTool(browser=browser).execute(
            {"selector": {"type": "text", "value": "Go"}}
        )
        assert not result.success and result.error == "Click failed"


class TestClickLink:
    async def test_validation(self) -> None:
        tool = ClickLinkTool(browser=FakeBrowser())
        with pytest.raises(ValueError):
            tool.validate_params({"text": ""})
        with pytest.raises(ValueError):
            tool.validate_params({"text": "x", "fuzzy": "yes"})

    async def test_success_with_metadata(self) -> None:
        browser = FakeBrowser(
            click_link_by_text={
                "success": True,
                "method": "fuzzy",
                "found_text": "PDF",
                "found_href": "http://x/a.pdf",
            }
        )
        result = await ClickLinkTool(browser=browser).execute({"text": "Download PDF"})
        assert result.success
        assert result.data["found_text"] == "PDF"
        assert result.data["found_href"] == "http://x/a.pdf"

    async def test_failure(self) -> None:
        browser = FakeBrowser(click_link_by_text={"success": False})
        result = await ClickLinkTool(browser=browser).execute({"text": "x"})
        assert not result.success


class TestType:
    async def test_validation(self) -> None:
        tool = TypeTool(browser=FakeBrowser())
        with pytest.raises(ValueError):
            tool.validate_params({"selector": {"type": "css", "value": "#i"}})

    async def test_success_clicks_first(self) -> None:
        browser = FakeBrowser(type_text={"success": True})
        result = await TypeTool(browser=browser).execute(
            {"selector": {"type": "css", "value": "#i"}, "text": "hi"}
        )
        assert result.success
        assert any(c[0] == "click" for c in browser.calls)
        assert any(c[0] == "type_text" for c in browser.calls)

    async def test_click_precheck_swallows_errors(self) -> None:
        class RaisingBrowser(FakeBrowser):
            async def click(self, selector: str, force: bool = False) -> dict[str, Any]:
                raise RuntimeError("no element")

        browser = RaisingBrowser(type_text={"success": True})
        result = await TypeTool(browser=browser).execute(
            {"selector": {"type": "css", "value": "#i"}, "text": "hi"}
        )
        assert result.success

    async def test_failure(self) -> None:
        browser = FakeBrowser(type_text={"success": False})
        result = await TypeTool(browser=browser).execute(
            {"selector": {"type": "css", "value": "#i"}, "text": "hi"}
        )
        assert not result.success


class TestPress:
    async def test_validation(self) -> None:
        with pytest.raises(ValueError):
            PressTool(browser=FakeBrowser()).validate_params({"key": ""})

    async def test_global_key(self) -> None:
        browser = FakeBrowser(press_key={"success": True})
        result = await PressTool(browser=browser).execute({"key": "Enter"})
        assert result.success
        assert browser.calls[0] == ("press_key", ("Enter",), {"selector": None})

    async def test_key_with_selector(self) -> None:
        browser = FakeBrowser(press_key={"success": True})
        result = await PressTool(browser=browser).execute(
            {"key": "Tab", "selector": {"type": "css", "value": "#i"}}
        )
        assert result.success
        assert browser.calls[0][2]["selector"] == "#i"

    async def test_failure(self) -> None:
        browser = FakeBrowser(press_key={"success": False})
        result = await PressTool(browser=browser).execute({"key": "Enter"})
        assert not result.success


class TestScroll:
    async def test_validation(self) -> None:
        with pytest.raises(ValueError):
            ScrollTool(browser=FakeBrowser()).validate_params({"direction": "sideways"})

    async def test_success(self) -> None:
        browser = FakeBrowser(scroll={"success": True, "y": 500})
        result = await ScrollTool(browser=browser).execute({"direction": "up", "amount_px": 100})
        assert result.success
        assert browser.calls[0][2] == {"direction": "up", "amount": 100}

    async def test_failure(self) -> None:
        browser = FakeBrowser(scroll={"success": False, "error": "cant scroll"})
        result = await ScrollTool(browser=browser).execute({})
        assert not result.success and result.error == "cant scroll"


class TestWait:
    async def test_validation(self) -> None:
        with pytest.raises(ValueError):
            WaitTool(browser=FakeBrowser()).validate_params({"ms": -1})
        with pytest.raises(ValueError):
            WaitTool(browser=FakeBrowser()).validate_params({"ms": 999999})

    async def test_success(self) -> None:
        browser = FakeBrowser()
        result = await WaitTool(browser=browser).execute({"ms": 10})
        assert result.success and result.data["waited_ms"] == 10


class TestGetAttribute:
    def test_text_properties_are_rejected_with_extraction_hint(self) -> None:
        tool = GetAttributeTool(browser=FakeBrowser())

        for attribute in ("innerText", "textContent"):
            with pytest.raises(ValueError, match="extract_text"):
                tool.validate_params(
                    {"selector": {"type": "css", "value": "main"}, "attribute": attribute}
                )

    async def test_real_attribute_is_allowed(self) -> None:
        browser = FakeBrowser(get_attribute={"success": True, "value": "/docs"})
        tool = GetAttributeTool(browser=browser)
        params = {"selector": {"type": "css", "value": "a"}, "attribute": "href"}

        tool.validate_params(params)
        result = await tool.execute(params)

        assert result.success and result.data["value"] == "/docs"


class TestHistory:
    async def test_forward(self) -> None:
        browser = FakeBrowser()
        result = await ForwardTool(browser=browser).execute({"steps": 2})
        assert result.success and browser.page.forward == 2

    async def test_back_default_step(self) -> None:
        browser = FakeBrowser()
        result = await BackTool(browser=browser).execute({})
        assert result.success and browser.page.back == 1

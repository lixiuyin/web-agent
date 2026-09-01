"""Tests for the async extraction paths of the snapshot module.

The pure rendering helpers are covered elsewhere; here we drive ``take_snapshot``
and the CDP / AX-tree / basic element extraction branches with fakes so the
network-free async code paths execute.
"""

from __future__ import annotations

from typing import Any

import pytest

from webagent.browser import snapshot as snap
from webagent.browser.snapshot import (
    _extract_element_label,
    _extract_elements_basic,
    _extract_elements_enhanced,
    _extract_from_ax_tree,
    take_snapshot,
    wait_for_page_stability,
)


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com"
        self.viewport_size = {"width": 1000, "height": 800}
        self._eval_result: Any = {"x": 1, "y": 2, "width": 3, "height": 4}
        self.eval_raises = False

    async def wait_for_timeout(self, ms: int) -> None:
        return None

    async def content(self) -> str:
        return "<html><head><title>T</title></head><body><h1>H</h1></body></html>"

    async def screenshot(self, full_page: bool = False, type: str = "png") -> bytes:
        return b"PNGDATA"

    async def title(self) -> str:
        return "T"

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        if self.eval_raises:
            raise RuntimeError("eval failed")
        return self._eval_result


class TestTakeSnapshot:
    async def test_basic_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_extract(page):
            return [{"tag": "button", "text": "Go", "attrs": {}, "css_path": "#go"}]

        monkeypatch.setattr(snap, "extract_interactive_elements", fake_extract)
        result = await take_snapshot(FakePage(), use_cdp=False, wait_after_load=0)
        assert result["meta"]["url"] == "https://example.com"
        assert result["screenshot_bytes"] == b"PNGDATA"
        assert result["meta"]["element_count"] == 1
        assert "Interactive Controls" in result["markdown"]

    async def test_default_viewport_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_extract(page):
            return []

        monkeypatch.setattr(snap, "extract_interactive_elements", fake_extract)
        page = FakePage()
        page.viewport_size = None
        result = await take_snapshot(page, use_cdp=False, wait_after_load=5)
        assert result["meta"]["viewport"] == {"width": 1280, "height": 720}

    async def test_rejects_html_screenshot_pair_crossing_navigation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_extract(page):
            return []

        class NavigatingPage(FakePage):
            async def screenshot(self, full_page: bool = False, type: str = "png") -> bytes:
                self.url = "https://example.com/redirected"
                return b"PNGDATA"

        monkeypatch.setattr(snap, "extract_interactive_elements", fake_extract)
        with pytest.raises(RuntimeError, match="page navigated during snapshot"):
            await take_snapshot(NavigatingPage(), use_cdp=False, wait_after_load=0)


class TestWaitForPageStability:
    async def test_waits_for_repeated_ready_dom_signature(self) -> None:
        class StabilizingPage:
            def __init__(self) -> None:
                self.states = iter(
                    [
                        {
                            "url": "https://example.com",
                            "readyState": "loading",
                            "nodeCount": 2,
                            "textLength": 0,
                            "scrollHeight": 100,
                        },
                        {
                            "url": "https://example.com",
                            "readyState": "complete",
                            "nodeCount": 20,
                            "textLength": 200,
                            "scrollHeight": 800,
                        },
                        {
                            "url": "https://example.com",
                            "readyState": "complete",
                            "nodeCount": 20,
                            "textLength": 200,
                            "scrollHeight": 800,
                        },
                    ]
                )

            async def evaluate(self, _script: str) -> dict[str, Any]:
                return next(self.states)

        assert (
            await wait_for_page_stability(
                StabilizingPage(), timeout_ms=1000, stable_ms=0, poll_ms=0
            )
            is True
        )

    async def test_zero_timeout_skips_stability_wait(self) -> None:
        assert await wait_for_page_stability(FakePage(), timeout_ms=0) is False


class _FakeCDP:
    def __init__(self, ax_tree: Any) -> None:
        self._ax_tree = ax_tree

    async def __aenter__(self) -> _FakeCDP:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get_ax_tree(self) -> Any:
        return self._ax_tree


class TestExtractElementsEnhanced:
    async def test_uses_ax_tree_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(snap, "CDPService", lambda page: _FakeCDP({"nodes": []}))

        async def fake_from_ax(ax_tree, page):
            return [{"tag": "button", "text": "AX", "css_path": "#ax"}]

        monkeypatch.setattr(snap, "_extract_from_ax_tree", fake_from_ax)
        out = await _extract_elements_enhanced(FakePage())
        assert out == [{"tag": "button", "text": "AX", "css_path": "#ax"}]

    async def test_falls_back_to_js_when_ax_tree_has_no_actionable_selectors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(snap, "CDPService", lambda page: _FakeCDP({"nodes": []}))

        async def fake_from_ax(ax_tree, page):
            return [{"tag": "textbox", "text": "User"}]

        async def fake_extract(page):
            return [{"tag": "input", "text": "", "css_path": "#username"}]

        monkeypatch.setattr(snap, "_extract_from_ax_tree", fake_from_ax)
        monkeypatch.setattr(snap, "extract_interactive_elements", fake_extract)

        out = await _extract_elements_enhanced(FakePage())

        assert out == [{"tag": "input", "text": "", "css_path": "#username"}]

    async def test_falls_back_to_js_when_no_ax_tree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(snap, "CDPService", lambda page: _FakeCDP(None))

        async def fake_extract(page):
            return [{"tag": "a", "text": "JS"}]

        monkeypatch.setattr(snap, "extract_interactive_elements", fake_extract)
        out = await _extract_elements_enhanced(FakePage())
        assert out == [{"tag": "a", "text": "JS"}]

    async def test_cdp_exception_uses_basic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(page):
            raise RuntimeError("no cdp")

        monkeypatch.setattr(snap, "CDPService", boom)

        async def fake_extract(page):
            return [{"tag": "a", "text": "basic"}]

        monkeypatch.setattr(snap, "extract_interactive_elements", fake_extract)
        out = await _extract_elements_enhanced(FakePage())
        assert out == [{"tag": "a", "text": "basic"}]


class TestExtractElementsBasic:
    async def test_returns_empty_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def boom(page):
            raise RuntimeError("fail")

        monkeypatch.setattr(snap, "extract_interactive_elements", boom)
        assert await _extract_elements_basic(FakePage()) == []


class TestExtractFromAxTree:
    async def test_filters_roles_and_reads_bbox(self) -> None:
        ax_tree = {
            "nodes": [
                {
                    "role": {"value": "button"},
                    "backendDOMNodeId": 5,
                    "name": {"value": "Submit"},
                },
                {"role": {"value": "generic"}, "backendDOMNodeId": 6},  # skipped (role)
                {"role": {"value": "link"}},  # skipped (no backend id)
            ]
        }
        out = await _extract_from_ax_tree(ax_tree, FakePage())
        assert len(out) == 1
        assert out[0]["text"] == "Submit"
        assert out[0]["bbox"] == {"x": 1, "y": 2, "width": 3, "height": 4}

    async def test_bbox_eval_exception_defaults_zero(self) -> None:
        page = FakePage()
        page.eval_raises = True
        ax_tree = {
            "nodes": [{"role": {"value": "textbox"}, "backendDOMNodeId": 9, "name": {"value": "Q"}}]
        }
        out = await _extract_from_ax_tree(ax_tree, page)
        assert out[0]["bbox"] == {"x": 0, "y": 0, "width": 0, "height": 0}


class TestExtractElementLabel:
    def test_aria_label_first(self) -> None:
        assert _extract_element_label({"attrs": {"aria-label": "Menu"}}) == "Menu"

    def test_text_content(self) -> None:
        assert _extract_element_label({"attrs": {}, "text": "Click me"}) == "Click me"

    def test_title_attribute(self) -> None:
        assert _extract_element_label({"attrs": {"title": "Info"}, "text": ""}) == "Info"

    def test_input_with_type(self) -> None:
        label = _extract_element_label({"tag": "input", "attrs": {"type": "email"}, "text": ""})
        assert label == "email input"

    def test_input_without_type(self) -> None:
        assert _extract_element_label({"tag": "input", "attrs": {}, "text": ""}) == "input"

    def test_tag_fallback(self) -> None:
        assert _extract_element_label({"tag": "section", "attrs": {}, "text": ""}) == "section"

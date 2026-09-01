"""Tests for BrowserController action methods against a fake Playwright page."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import pytest

from webagent.browser.controller import (
    BrowserController,
    _mark_profile_clean,
    _same_navigation_site,
)


class FakeResponse:
    def __init__(self, ok: bool = True, status: int = 200) -> None:
        self.ok = ok
        self.status = status


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    async def press(self, key: str) -> None:
        self.pressed.append(key)


class FakeMouse:
    def __init__(self, fail: bool = False) -> None:
        self.wheels: list[tuple[int, int]] = []
        self.fail = fail

    async def wheel(self, x: int, y: int) -> None:
        if self.fail:
            raise RuntimeError("wheel failed")
        self.wheels.append((x, y))


class FakeElement:
    def __init__(self, text: str = "", attributes: dict[str, str] | None = None) -> None:
        self.text = text
        self.attributes = attributes or {}
        self.scrolled_into_view = False

    async def text_content(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    async def scroll_into_view_if_needed(self) -> None:
        self.scrolled_into_view = True


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector

    def filter(self, *, visible: bool | None = None) -> FakeLocator:
        self.page.calls.append(("locator_filter", {"selector": self.selector, "visible": visible}))
        return self

    @property
    def first(self) -> FakeLocator:
        return self

    async def wait_for(self, **kwargs: Any) -> None:
        self.page.calls.append(("locator_wait_for", {"selector": self.selector, **kwargs}))
        if self.page.fail:
            raise RuntimeError("selector never appeared")

    async def fill(self, value: str, **kwargs: Any) -> None:
        self.page.calls.append(
            ("locator_fill", {"selector": self.selector, "value": value, **kwargs})
        )

    async def type(self, text: str, **kwargs: Any) -> None:
        self.page.calls.append(
            ("locator_type", {"selector": self.selector, "text": text, **kwargs})
        )

    async def count(self) -> int:
        return 1


class FakePage:
    """Configurable Page double covering the controller's Playwright surface."""

    def __init__(self, *, fail: bool = False) -> None:
        self.url = "https://example.test/page"
        self._title = "Example Page"
        self.fail = fail
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse(fail=fail)
        self.elements: dict[str, list[FakeElement]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.response = FakeResponse()
        self.viewport: dict[str, int] = {"width": 1280, "height": 720}

    async def goto(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("goto", {"url": url, **kwargs}))
        if self.fail:
            raise RuntimeError("navigation crashed")
        self.url = url
        return self.response

    async def title(self) -> str:
        return self._title

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        self.calls.append(("wait_for_load_state", {"state": state, **kwargs}))

    async def click(self, selector: str, **kwargs: Any) -> None:
        self.calls.append(("click", {"selector": selector, **kwargs}))
        if self.fail:
            raise RuntimeError("click failed")

    def locator(self, selector: str) -> FakeLocator:
        self.calls.append(("locator", {"selector": selector}))
        return FakeLocator(self, selector)

    async def wait_for_selector(self, selector: str, **kwargs: Any) -> FakeElement | None:
        self.calls.append(("wait_for_selector", {"selector": selector, **kwargs}))
        if self.fail:
            raise RuntimeError("selector never appeared")
        found = self.elements.get(selector, [])
        return found[0] if found else FakeElement()

    async def fill(self, selector: str, value: str, **kwargs: Any) -> None:
        self.calls.append(("fill", {"selector": selector, "value": value}))

    async def type(self, selector: str, text: str, **kwargs: Any) -> None:
        self.calls.append(("type", {"selector": selector, "text": text}))

    async def text_content(self, selector: str, **kwargs: Any) -> str:
        if self.fail:
            raise RuntimeError("gone")
        found = self.elements.get(selector, [])
        return found[0].text if found else ""

    async def evaluate(self, expr: str, *args: Any) -> Any:
        return None

    async def reload(self, **kwargs: Any) -> None:
        self.calls.append(("reload", kwargs))

    async def screenshot(self, **kwargs: Any) -> bytes:
        if self.fail:
            raise RuntimeError("cannot screenshot")
        from io import BytesIO

        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (20, 10), color="red").save(buf, format="JPEG")
        return buf.getvalue()

    def set_default_timeout(self, timeout: int) -> None:
        self.calls.append(("set_default_timeout", {"timeout": timeout}))

    def set_viewport_size(self, size: dict[str, int]) -> None:
        self.viewport = size

    async def focus(self, selector: str, **kwargs: Any) -> None:
        self.calls.append(("focus", {"selector": selector}))
        if self.fail:
            raise RuntimeError("cannot focus")

    async def hover(self, selector: str, **kwargs: Any) -> None:
        self.calls.append(("hover", {"selector": selector}))
        if self.fail:
            raise RuntimeError("cannot hover")

    async def select_option(self, selector: str, **kwargs: Any) -> None:
        self.calls.append(("select_option", {"selector": selector, **kwargs}))
        if self.fail:
            raise RuntimeError("no options")

    async def query_selector(self, selector: str) -> FakeElement | None:
        found = self.elements.get(selector, [])
        return found[0] if found else None

    async def query_selector_all(self, selector: str) -> list[FakeElement]:
        return self.elements.get(selector, [])


def _controller(page: FakePage) -> BrowserController:
    controller = BrowserController(headless=True)
    controller._page = page
    return controller


class TestNavigation:
    async def test_goto_success(self) -> None:
        page = FakePage()
        result = await _controller(page).goto("https://example.test")
        assert result["success"] is True
        assert result["status"] == 200
        assert result["title"] == "Example Page"

    async def test_goto_failure_returns_error_dict(self) -> None:
        result = await _controller(FakePage(fail=True)).goto("https://example.test")
        assert result["success"] is False
        assert "error" in result

    async def test_goto_recovers_inspectable_page_after_err_aborted(self) -> None:
        class RedirectPage(FakePage):
            async def goto(self, url: str, **kwargs: Any) -> FakeResponse:
                self.url = "https://regional.example.test/results"
                raise RuntimeError("Page.goto: net::ERR_ABORTED")

        result = await _controller(RedirectPage()).goto("https://example.test/search")

        assert result == {
            "success": True,
            "url": "https://regional.example.test/results",
            "title": "Example Page",
            "status": None,
            "recovered_from": "net::ERR_ABORTED",
        }


def test_same_navigation_site_accepts_regional_redirect_but_not_stale_page() -> None:
    assert _same_navigation_site("https://www.bing.com/search", "https://cn.bing.com/search")
    assert not _same_navigation_site("https://search.seznam.cz/", "https://cn.bing.com/search")

    async def test_refresh(self) -> None:
        result = await _controller(FakePage()).refresh()
        assert result["success"] is True

    async def test_refresh_failure(self) -> None:
        page = FakePage()
        page.reload = _raise
        result = await _controller(page).refresh()
        assert result["success"] is False

    async def test_open_local_file_missing(self) -> None:
        result = await _controller(FakePage()).open_local_file("/nonexistent/file.png")
        assert result["success"] is False


async def test_close_removes_owned_temporary_profile(tmp_path) -> None:
    profile = tmp_path / "owned-profile"
    profile.mkdir()
    (profile / "marker").write_text("x", encoding="utf-8")
    controller = BrowserController(temporary_profile=True)
    controller._owned_profile_dir = profile
    controller.user_data_dir = str(profile)

    await controller.close()

    assert not profile.exists()


async def test_temporary_profile_can_use_configured_root(tmp_path) -> None:
    root = tmp_path / "runtime-profiles"
    controller = BrowserController(
        temporary_profile=True,
        temporary_profile_root=root,
    )

    profile = controller._create_temporary_profile()
    controller._owned_profile_dir = profile
    controller.user_data_dir = str(profile)

    assert profile.parent == root
    await controller.close()
    assert not profile.exists()


async def test_new_profile_removes_only_marked_stale_dead_owner(tmp_path) -> None:
    root = tmp_path / "runtime-profiles"
    root.mkdir()
    stale = root / "webagent-profile-stale"
    stale.mkdir()
    (stale / ".webagent-owner.json").write_text(
        json.dumps({"pid": 99_999_999, "created_at": time.time() - 7200}),
        encoding="utf-8",
    )
    unmarked = root / "webagent-profile-unmarked"
    unmarked.mkdir()
    active = root / "webagent-profile-active"
    active.mkdir()
    (active / ".webagent-owner.json").write_text(
        json.dumps({"pid": os.getpid(), "created_at": time.time() - 7200}),
        encoding="utf-8",
    )
    controller = BrowserController(
        temporary_profile=True,
        temporary_profile_root=root,
        stale_profile_max_age_seconds=3600,
    )

    current = controller._create_temporary_profile()
    controller._owned_profile_dir = current
    controller.user_data_dir = str(current)

    assert not stale.exists()
    assert unmarked.exists()
    assert active.exists()
    marker = json.loads((current / ".webagent-owner.json").read_text(encoding="utf-8"))
    assert marker["pid"] == os.getpid()
    await controller.close()
    assert not current.exists()

    async def test_open_local_file(self, tmp_path: Any) -> None:
        target = tmp_path / "view.png"
        target.write_bytes(b"x")
        page = FakePage()
        result = await _controller(page).open_local_file(str(target))
        assert result["success"] is True
        assert result["url"].startswith("file://")


class _CheckpointPage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.closed = False
        self.front = False

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url

    async def close(self) -> None:
        self.closed = True

    async def bring_to_front(self) -> None:
        self.front = True


class _CheckpointContext:
    def __init__(self, pages: list[_CheckpointPage]) -> None:
        self.pages = pages
        self.cookies: list[dict[str, Any]] = []
        self.scripts: list[str] = []

    async def storage_state(self) -> dict[str, Any]:
        return {
            "cookies": [{"name": "session", "value": "private"}],
            "origins": [
                {
                    "origin": "https://example.test",
                    "localStorage": [{"name": "mode", "value": "resume"}],
                }
            ],
        }

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self.cookies.extend(cookies)

    async def add_init_script(self, script: str) -> None:
        self.scripts.append(script)

    async def new_page(self) -> _CheckpointPage:
        page = _CheckpointPage("about:blank")
        self.pages.append(page)
        return page


async def test_browser_checkpoint_round_trip_restores_tabs_and_optional_storage() -> None:
    first = _CheckpointPage("https://example.test/one")
    second = _CheckpointPage("https://example.test/two")
    context = _CheckpointContext([first, second])
    controller = BrowserController(headless=True)
    controller._context = context  # type: ignore[assignment]
    controller._page = second  # type: ignore[assignment]

    state = await controller.export_checkpoint_state(include_storage=True)
    assert state["active_index"] == 1
    assert state["storage_state"]["cookies"][0]["value"] == "private"

    context.pages.append(_CheckpointPage("https://example.test/stale"))
    restored = await controller.restore_checkpoint_state(state)

    assert restored == {"success": True, "tabs": 2, "active_index": 1}
    assert context.cookies[0]["name"] == "session"
    assert "localStorage.setItem" in context.scripts[0]
    assert controller.page.url == "https://example.test/two"


async def test_browser_checkpoint_rejects_unsafe_or_malformed_tabs() -> None:
    context = _CheckpointContext([_CheckpointPage("about:blank")])
    controller = BrowserController(headless=True)
    controller._context = context  # type: ignore[assignment]
    controller._page = context.pages[0]  # type: ignore[assignment]

    with pytest.raises(ValueError, match="tab state"):
        await controller.restore_checkpoint_state(
            {"schema_version": 1, "tabs": ["file:///tmp/secret"], "active_index": 0}
        )
    with pytest.raises(ValueError, match="schema mismatch"):
        await controller.restore_checkpoint_state({"schema_version": 9})


async def _raise(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError("boom")


class TestInteractions:
    async def test_click(self) -> None:
        result = await _controller(FakePage()).click("#submit")
        assert result == {"success": True, "selector": "#submit"}

    async def test_click_failure(self) -> None:
        result = await _controller(FakePage(fail=True)).click("#submit")
        assert result["success"] is False

    async def test_click_activates_new_tab(self) -> None:
        class PopupPage(FakePage):
            def __init__(self) -> None:
                super().__init__()
                self.url = "https://destination.test/docs"
                self.front = False

            async def bring_to_front(self) -> None:
                self.front = True

        source = FakePage()
        popup = PopupPage()

        class Context:
            def __init__(self) -> None:
                self.pages = [source]

        context = Context()
        delayed_tasks: list[asyncio.Task[None]] = []

        async def click_and_open(selector: str, **kwargs: Any) -> None:
            source.calls.append(("click", {"selector": selector, **kwargs}))

            async def delayed_open() -> None:
                await asyncio.sleep(0.1)
                context.pages.append(popup)

            delayed_tasks.append(asyncio.create_task(delayed_open()))

        source.click = click_and_open  # type: ignore[method-assign]
        controller = _controller(source)
        controller._context = context  # type: ignore[assignment]

        result = await controller.click("#result")

        assert controller.page is popup
        assert popup.front is True
        assert result["opened_new_tab"] is True
        assert result["url"] == "https://destination.test/docs"
        assert result["tab_index"] == 1
        assert delayed_tasks[0].done()

    async def test_type_text(self) -> None:
        page = FakePage()
        result = await _controller(page).type_text("#q", "hello")
        assert result["success"] is True
        assert result["text"] == "hello"
        assert ("locator_filter", {"selector": "#q", "visible": True}) in page.calls
        assert any(call == "locator_type" for call, _params in page.calls)

    async def test_type_text_failure(self) -> None:
        result = await _controller(FakePage(fail=True)).type_text("#q", "hello")
        assert result["success"] is False

    async def test_press_key_without_selector(self) -> None:
        page = FakePage()
        result = await _controller(page).press_key("Enter")
        assert result["success"] is True
        assert page.keyboard.pressed == ["Enter"]

    async def test_press_key_failure(self) -> None:
        page = FakePage(fail=True)
        result = await _controller(page).press_key("Enter", selector="#q")
        assert result["success"] is False

    async def test_wait(self) -> None:
        result = await _controller(FakePage()).wait(10)
        assert result == {"success": True, "waited_ms": 10}

    async def test_scroll_down_and_up(self) -> None:
        page = FakePage()
        assert (await _controller(page).scroll("down", 300))["success"] is True
        assert (await _controller(page).scroll("up", 300))["success"] is True
        assert page.mouse.wheels == [(0, 300), (0, -300)]

    async def test_scroll_failure(self) -> None:
        result = await _controller(FakePage(fail=True)).scroll()
        assert result["success"] is False

    async def test_hover(self) -> None:
        assert (await _controller(FakePage()).hover("#menu"))["success"] is True

    async def test_hover_failure(self) -> None:
        assert (await _controller(FakePage(fail=True)).hover("#menu"))["success"] is False

    async def test_select_option_by_value(self) -> None:
        result = await _controller(FakePage()).select_option("#s", value="a")
        assert result["success"] is True
        assert result["option"] == {"value": "a"}

    async def test_select_option_failure(self) -> None:
        assert (await _controller(FakePage(fail=True)).select_option("#s", value="a"))[
            "success"
        ] is False

    async def test_wait_for_selector(self) -> None:
        result = await _controller(FakePage()).wait_for_selector("#x")
        assert result["success"] is True

    async def test_get_element_text(self) -> None:
        page = FakePage()
        page.elements["#p"] = [FakeElement(text="hello world")]
        result = await _controller(page).get_element_text("#p")
        assert result == {"success": True, "selector": "#p", "text": "hello world"}

    async def test_get_element_text_failure(self) -> None:
        assert (await _controller(FakePage(fail=True)).get_element_text("#p"))["success"] is False


class TestScreenshot:
    async def test_pil_format(self) -> None:
        result = await _controller(FakePage()).screenshot()
        assert result["success"] is True
        assert (result["width"], result["height"]) == (20, 10)

    async def test_base64_format(self) -> None:
        result = await _controller(FakePage()).screenshot(return_format="base64")
        assert isinstance(result["image"], str)

    async def test_bytes_format(self) -> None:
        result = await _controller(FakePage()).screenshot(return_format="bytes")
        assert isinstance(result["image"], bytes)

    async def test_failure(self) -> None:
        result = await _controller(FakePage(fail=True)).screenshot()
        assert result["success"] is False


class TestElementQueries:
    async def test_get_attribute(self) -> None:
        page = FakePage()
        page.elements["#a"] = [FakeElement(attributes={"href": "https://x"})]
        result = await _controller(page).get_attribute("#a", "href")
        assert result["success"] is True
        assert result["value"] == "https://x"

    async def test_get_attribute_missing_element(self) -> None:
        result = await _controller(FakePage()).get_attribute("#missing", "href")
        assert result["success"] is False
        assert result["error"] == "Element not found"

    async def test_get_all_links_filters(self) -> None:
        page = FakePage()
        page.elements["a"] = [
            FakeElement(attributes={"href": "https://ext.example/x"}),
            FakeElement(attributes={"href": "#anchor"}),
            FakeElement(attributes={"href": "javascript:void(0)"}),
            FakeElement(attributes={"href": "/relative"}),
        ]
        page.elements["a"][0].text = " External "
        result = await _controller(page).get_all_links(
            skip_anchors=True, skip_javascript=True, filter_external_only=True
        )
        assert result["links"] == [{"href": "https://ext.example/x", "text": "External"}]

    async def test_get_all_links_max_results(self) -> None:
        page = FakePage()
        page.elements["a"] = [FakeElement(attributes={"href": f"https://x/{i}"}) for i in range(5)]
        result = await _controller(page).get_all_links(max_results=2)
        assert result["count"] == 2
        assert result["total_count"] == 5

    async def test_get_all_links_prioritizes_document_links_and_deduplicates(self) -> None:
        page = FakePage()
        page.elements["a"] = [
            FakeElement(
                text="GitHub Copilot",
                attributes={"href": "https://github.com/features/copilot"},
            ),
            FakeElement(attributes={"href": "/owner/repo/blob/main/tech_report.pdf"}),
            FakeElement(
                text="Technical Report",
                attributes={"href": "https://arxiv.org/abs/2601.00001"},
            ),
            FakeElement(attributes={"href": "/owner/repo/blob/main/tech_report.pdf"}),
        ]

        result = await _controller(page).get_all_links(max_results=2)

        assert result["links"] == [
            {"href": "/owner/repo/blob/main/tech_report.pdf", "text": ""},
            {"href": "https://arxiv.org/abs/2601.00001", "text": "Technical Report"},
        ]
        assert result["total_count"] == 3

    async def test_get_all_links_no_href_skipped(self) -> None:
        page = FakePage()
        page.elements["a"] = [FakeElement(text="no href")]
        result = await _controller(page).get_all_links()
        assert result["links"] == []

    async def test_scroll_to_element(self) -> None:
        page = FakePage()
        element = FakeElement()
        page.elements["#target"] = [element]
        result = await _controller(page).scroll_to_element("#target")
        assert result["success"] is True
        assert element.scrolled_into_view

    async def test_scroll_to_element_missing(self) -> None:
        result = await _controller(FakePage()).scroll_to_element("#gone")
        assert result["success"] is False


class TestSearchResultsDispatch:
    async def test_unknown_engine_falls_back_to_links(self) -> None:
        page = FakePage()
        page.elements["a"] = [FakeElement(attributes={"href": "https://x/1"})]
        result = await _controller(page).get_search_results()
        assert result["success"] is True
        assert "links" in result

    async def test_google_dispatch(self) -> None:
        page = FakePage()
        page.url = "https://www.google.com/search?q=test"
        link = FakeElement(attributes={"href": "https://result.example/r"})
        link.text = "Result"
        container = FakeElement()
        container.attributes = {}
        page.elements["div.g"] = [container]
        # wire container's child link through elements on the container itself
        container.children_links = {"a": link}  # type: ignore[attr-defined]

        async def container_query(selector: str) -> FakeElement | None:
            return container.children_links.get(selector)  # type: ignore[attr-defined]

        container.query_selector = container_query  # type: ignore[method-assign]
        result = await _controller(page).get_search_results()
        assert result["success"] is True
        assert result["engine"] == "google"
        assert result["query"] == "test"
        assert result["results"][0]["title"] == "Result"

    async def test_engine_error_is_captured(self) -> None:
        page = FakePage()
        page.url = "https://www.bing.com/search?q=x"
        page.query_selector_all = _raise  # type: ignore[method-assign]
        result = await _controller(page).get_search_results()
        assert result["success"] is False
        assert "error" in result


class TestLifecycleGuards:
    async def test_page_before_start_raises(self) -> None:
        controller = BrowserController(headless=True)
        with pytest.raises(RuntimeError, match="Browser not started"):
            _ = controller.page

    async def test_double_start_raises(self) -> None:
        controller = BrowserController(headless=True)
        controller._playwright = object()  # simulate started state
        with pytest.raises(RuntimeError, match="already started"):
            await controller.start()

    async def test_close_without_start_is_noop(self) -> None:
        await BrowserController(headless=True).close()

    def test_repairs_persisted_crash_markers(self, tmp_path: Any) -> None:
        default = tmp_path / "Default"
        default.mkdir()
        preferences = default / "Preferences"
        local_state = tmp_path / "Local State"
        preferences.write_text(json.dumps({"profile": {"exit_type": "Crashed"}}))
        local_state.write_text(
            json.dumps({"user_experience_metrics": {"stability": {"exited_cleanly": False}}})
        )

        _mark_profile_clean(tmp_path)

        assert json.loads(preferences.read_text())["profile"]["exit_type"] == "Normal"
        assert (
            json.loads(local_state.read_text())["user_experience_metrics"]["stability"][
                "exited_cleanly"
            ]
            is True
        )

    def test_ignores_malformed_profile_state(self, tmp_path: Any) -> None:
        default = tmp_path / "Default"
        default.mkdir()
        (default / "Preferences").write_text("not json")
        _mark_profile_clean(tmp_path)


class TestHeadlessResolution:
    def test_headed_preserved_on_macos_without_display(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("webagent.browser.controller.sys.platform", "darwin")
        monkeypatch.delenv("DISPLAY", raising=False)
        assert BrowserController(headless=False).headless is False

    def test_headed_preserved_on_windows_without_display(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("webagent.browser.controller.sys.platform", "win32")
        monkeypatch.delenv("DISPLAY", raising=False)
        assert BrowserController(headless=False).headless is False

    def test_headed_forced_on_linux_without_display(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("webagent.browser.controller.sys.platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        assert BrowserController(headless=False).headless is True

    def test_headed_works_on_linux_with_wayland(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("webagent.browser.controller.sys.platform", "linux")
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert BrowserController(headless=False).headless is False

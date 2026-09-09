"""Rendered text, clipping, frames, and stale-node action acceptance."""

from __future__ import annotations

import json

import pytest

from webagent.browser.controller import BrowserController
from webagent.browser.snapshot import take_snapshot
from webagent.tools.builtin.advanced_browser_tools import FrameInteractTool
from webagent.tools.builtin.browser_tools import ClickTool
from webagent.tools.builtin.interaction_tools import HoverTool, ScrollToElementTool
from webagent.tools.registry import ToolRegistry


def _selector(snapshot, element_id):
    element = next(e for e in snapshot["elements"] if e["attrs"].get("id") == element_id)
    return {
        "type": "css",
        "value": element["css_path"],
        "observation_id": element["observation_id"],
        "ref": element["ref"],
    }


@pytest.mark.integration
async def test_custom_pointer_menu_has_one_reference_and_supports_hover_then_click():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
            <style>#more{cursor:pointer;width:150px} #menu{display:none}
            #more:hover #menu{display:block}</style>
            <div id="more"><span id="caption">More</span><i id="icon">+</i>
              <div id="menu"><div id="research" role="menuitem">Research</div></div>
            </div>
            <script>document.querySelector('#research').onclick=()=>document.body.dataset.opened='yes';</script>
        """)
        registry = ToolRegistry()
        registry.register(HoverTool(browser=browser))
        registry.register(ClickTool(browser=browser))
        before = await take_snapshot(browser.page, wait_after_load=0)
        ids = {e["attrs"].get("id") for e in before["elements"]}
        assert "more" in ids and not {"caption", "icon", "research"} & ids
        assert "affordance=pointer_hint" in before["viewport_context"]
        assert (await registry.execute("hover", {"selector": _selector(before, "more")})).success
        after = await take_snapshot(browser.page, wait_after_load=0)
        assert "role='menuitem'" in after["viewport_context"]
        assert (await registry.execute("click", {"selector": _selector(after, "research")})).success
        assert await browser.page.get_attribute("body", "data-opened") == "yes"


@pytest.mark.integration
async def test_custom_aria_focus_and_event_controls_are_observed():
    roles = ("combobox", "checkbox", "radio", "option", "slider", "switch", "tab", "treeitem")
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content(
            "".join(
                f'<div id="{role}" role="{role}" aria-checked="false">{role}</div>'
                for role in roles
            )
            + '<div id="focus" tabindex="0">Focusable</div><div id="event">Handler</div>'
            '<div id="disclosure" aria-haspopup="menu" aria-expanded="false">Disclosure</div>'
            '<div id="hidden" role="button" style="display:none">Hidden</div>'
            '<details><summary id="summary">Details</summary>Content</details>'
            '<script>document.querySelector("#event").onclick=()=>{};</script>'
        )
        observed = await take_snapshot(browser.page, wait_after_load=0)
        ids = {e["attrs"].get("id") for e in observed["elements"]}
        assert set(roles) | {"focus", "event", "disclosure", "summary"} <= ids
        assert "hidden" not in ids
        assert "aria-checked='false'" in observed["viewport_context"]


@pytest.mark.integration
async def test_observed_form_values_and_checked_state_exclude_passwords():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
            <input id="text" value="old"><input id="password" type="password" value="secret-not-for-context">
            <input id="check" type="checkbox"><select id="select"><option value="a">A</option><option value="b">B</option></select>
        """)
        await browser.page.locator("#text").fill("changed")
        await browser.page.locator("#check").check()
        await browser.page.locator("#select").select_option("b")
        observed = await take_snapshot(browser.page, wait_after_load=0)
        elements = {e["attrs"].get("id"): e for e in observed["elements"]}
        assert elements["text"]["attrs"]["value"] == "changed"
        assert elements["select"]["attrs"]["value"] == "b"
        assert elements["check"]["checked"] is True
        assert "value" not in elements["password"]["attrs"]
        assert "value='changed'" in observed["viewport_context"]
        assert "checked=True" in observed["viewport_context"]
        assert "secret-not-for-context" not in observed["markdown"]
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        await browser.page.locator("#check").evaluate("el => el.checked = false")
        stale = await registry.execute("click", {"selector": _selector(observed, "check")})
        assert not stale.success and "Stale observation" in stale.error


@pytest.mark.integration
async def test_viewport_text_respects_lines_clipping_and_overlay():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_viewport_size({"width": 800, "height": 400})
        await browser.page.set_content("""
          <style>body{margin:0} p{margin:0} #clip{height:20px;overflow:hidden;width:200px;line-height:20px}
          #covered,#overlay{position:absolute;top:100px;left:0;width:180px;height:30px}
          #overlay{z-index:5;background:white}</style>
          <p>Visible introduction</p>
          <div id="clip"><p>Visible container line</p><p>Clipped container secret</p></div>
          <p id="covered">Covered paragraph secret</p><div id="overlay">Overlay label</div>
          <p style="margin-top:600px">Below fold secret</p>
          <p style="opacity:0">Transparent secret</p>
        """)
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        viewport, document = snapshot["viewport_context"], snapshot["document_context"]
        assert snapshot["meta"]["rendered_projection"] is True
        assert "Visible introduction" in viewport
        assert "Visible container line" in viewport
        assert "Overlay label" in viewport
        assert "secret" not in viewport
        assert "Clipped container secret" in document
        assert "Below fold secret" in document
        assert "Transparent secret" not in document


@pytest.mark.integration
async def test_reference_requires_scroll_reobserve_and_original_node():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
            <style>body{height:2500px} #target{position:absolute;top:1500px}</style>
            <button id="target" onclick="document.body.dataset.clicked='yes'">Target</button>
        """)
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        registry.register(ScrollToElementTool(browser=browser))
        first = await take_snapshot(browser.page, wait_after_load=0)
        selector = _selector(first, "target")
        refused = await registry.execute("click", {"selector": selector})
        assert not refused.success and "outside viewport" in refused.error
        assert (await registry.execute("scroll_to_element", {"selector": selector})).success
        second = await take_snapshot(browser.page, wait_after_load=0)
        stale = await registry.execute("click", {"selector": selector})
        assert not stale.success and "Stale observation" in stale.error
        selector = _selector(second, "target")
        assert (await registry.execute("click", {"selector": selector})).success
        assert await browser.page.get_attribute("body", "data-clicked") == "yes"
        third = await take_snapshot(browser.page, wait_after_load=0)
        old = _selector(third, "target")
        await browser.page.evaluate(
            "document.querySelector('#target').outerHTML='<button id=target>Replacement</button>'"
        )
        replaced = await registry.execute("click", {"selector": old})
        assert not replaced.success and "Stale observation" in replaced.error


@pytest.mark.integration
async def test_iframe_coordinates_and_bound_action_across_origins():
    async with BrowserController(headless=True, temporary_profile=True) as browser:

        async def route(request):
            body = (
                "<style>body{margin:0}iframe{position:absolute;left:120px;top:160px;width:300px;height:150px;border:0}</style>"
                '<iframe src="http://child.test/"></iframe>'
                if request.request.url == "http://parent.test/"
                else '<style>body{margin:0}</style><button id="child" onclick="this.textContent=\'Clicked child\'">Child button</button>'
            )
            await request.fulfill(content_type="text/html", body=body)

        await browser.page.route("**/*", route)
        await browser.page.goto("http://parent.test/")
        await browser.page.frames[1].wait_for_selector("#child")
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        control = next(e for e in snapshot["elements"] if e["attrs"].get("id") == "child")
        assert control["frame_index"] == 1
        assert control["viewport_bbox"]["x"] == 120
        assert control["viewport_bbox"]["y"] == 160
        assert "Child button" in snapshot["viewport_context"]
        registry = ToolRegistry()
        registry.register(FrameInteractTool(browser=browser))
        result = await registry.execute(
            "frame_interact",
            {
                "frame_index": 1,
                "action": "click",
                "selector": _selector(snapshot, "child"),
            },
        )
        assert result.success, result.error
        assert await browser.page.frames[1].inner_text("#child") == "Clicked child"


@pytest.mark.integration
async def test_overlay_and_force_cannot_bypass_reference_check():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
          <button id="target" style="position:absolute;top:20px;left:20px">Covered target</button>
          <div style="position:absolute;inset:0;background:white;z-index:10">Modal</div>
        """)
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        assert "Covered target" not in snapshot["viewport_context"]
        assert "Covered target" in snapshot["document_context"]
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        selector = _selector(snapshot, "target")
        result = await registry.execute("click", {"selector": selector})
        assert not result.success and "obscured" in result.error
        result = await registry.execute("click", {"selector": selector, "force": True})
        assert not result.success and "force cannot bypass" in result.error


@pytest.mark.integration
async def test_long_wrapped_text_only_exports_viewport_words():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_viewport_size({"width": 300, "height": 100})
        text = " ".join(f"word{i}" for i in range(150))
        await browser.page.set_content(
            '<p style="margin:0;font:20px/25px monospace">' + text + "</p>"
        )
        snapshot = await take_snapshot(browser.page, wait_after_load=0, document_chars=4000)
        assert "word0" in snapshot["viewport_context"]
        assert "word149" not in snapshot["viewport_context"]
        assert "word149" in snapshot["document_context"]
        assert json.dumps(snapshot["meta"]["context_omissions"])


@pytest.mark.integration
async def test_same_url_same_size_text_replacement_invalidates_capture(monkeypatch):
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content('<p id="status" style="width:200px;height:30px">Before</p>')
        screenshot = browser.page.screenshot

        async def changing_screenshot(**kwargs):
            image = await screenshot(**kwargs)
            await browser.page.evaluate("document.querySelector('#status').textContent='After'")
            return image

        monkeypatch.setattr(browser.page, "screenshot", changing_screenshot)
        with pytest.raises(RuntimeError, match="content or layout changed"):
            await take_snapshot(browser.page, wait_after_load=0)


@pytest.mark.integration
async def test_open_shadow_root_text_and_original_node_action():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content('<div id="host"></div>')
        await browser.page.evaluate(r"""() => {
            const root=document.querySelector('#host').attachShadow({mode:'open'});
            root.innerHTML='<button id="shadow" onclick="this.textContent=\'Shadow clicked\'">Shadow control</button>';
        }""")
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        assert "Shadow control" in snapshot["viewport_context"]
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        result = await registry.execute("click", {"selector": _selector(snapshot, "shadow")})
        assert result.success, result.error
        assert await browser.page.locator("#host #shadow").inner_text() == "Shadow clicked"

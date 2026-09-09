"""Local action validity is distinct from whole-capture consistency."""

import pytest

from webagent.browser.controller import BrowserController
from webagent.browser.snapshot import take_snapshot
from webagent.tools.builtin.advanced_browser_tools import FrameInteractTool
from webagent.tools.builtin.browser_tools import ClickTool
from webagent.tools.builtin.interaction_tools import GetAttributeTool
from webagent.tools.registry import ToolRegistry


def _reference(snapshot, element_id):
    element = next(e for e in snapshot["elements"] if e["attrs"].get("id") == element_id)
    return {"type": "ref", "value": f"{snapshot['meta']['observation_id']}/{element['ref']}"}


@pytest.mark.integration
async def test_offscreen_link_read_is_allowed_but_click_still_requires_scroll():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content(
            '<a id="report" href="https://publisher.test/report.pdf" style="position:absolute;top:2000px">Technical report</a>'
        )
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        selector = _reference(snapshot, "report")
        registry = ToolRegistry()
        registry.register(GetAttributeTool(browser=browser))
        registry.register(ClickTool(browser=browser))
        result = await registry.execute(
            "get_attribute", {"selector": selector, "attribute": "href"}
        )
        assert result.success and result.data["value"] == "https://publisher.test/report.pdf"
        assert await browser.page.evaluate("scrollY") == 0
        result = await registry.execute("click", {"selector": selector})
        assert not result.success and "outside viewport" in result.error


@pytest.mark.integration
async def test_unrelated_text_update_does_not_invalidate_compact_target():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
          <button id="target" onclick="this.dataset.clicked='yes'">Continue</button>
          <p id="clock">12:00</p>
        """)
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        await browser.page.locator("#clock").evaluate("el=>el.textContent='12:01'")
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        result = await registry.execute("click", {"selector": _reference(snapshot, "target")})
        assert result.success, result.error
        assert await browser.page.locator("#target").get_attribute("data-clicked") == "yes"


@pytest.mark.integration
@pytest.mark.parametrize(
    "mutation",
    [
        "target.textContent='Delete account'",
        "target.outerHTML='<button id=target>Continue</button>'",
        "target.form.action='/delete-account'",
        "document.querySelector('#label').textContent='Delete account'",
        "target.parentElement.style.overflow='hidden'; target.parentElement.style.height='1px'",
        "target.style.marginLeft='40px'",
    ],
)
async def test_relevant_target_changes_still_invalidate_reference(mutation):
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
          <form action="/continue" onsubmit="event.preventDefault()">
            <button id="target" aria-labelledby="label">Continue</button>
          </form><span id="label">Continue</span>
        """)
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        await browser.page.evaluate(
            "() => {const target=document.querySelector('#target');" + mutation + "}"
        )
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        result = await registry.execute("click", {"selector": _reference(snapshot, "target")})
        assert not result.success and "Stale observation" in result.error


@pytest.mark.integration
async def test_iframe_parent_text_can_change_but_frame_geometry_cannot():
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_content("""
          <iframe style="width:300px;height:100px" srcdoc="<button id='target' onclick='this.dataset.clicked=1'>Continue</button>"></iframe>
          <p id="clock">12:00</p>
        """)
        frame = browser.page.frames[1]
        await frame.wait_for_selector("#target")
        registry = ToolRegistry()
        registry.register(FrameInteractTool(browser=browser))
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        await browser.page.locator("#clock").evaluate("el=>el.textContent='12:01'")
        result = await registry.execute(
            "frame_interact",
            {
                "frame_index": 1,
                "action": "click",
                "selector": _reference(snapshot, "target"),
            },
        )
        assert result.success, result.error
        snapshot = await take_snapshot(browser.page, wait_after_load=0)
        await browser.page.locator("iframe").evaluate("el=>el.style.marginLeft='40px'")
        result = await registry.execute(
            "frame_interact",
            {
                "frame_index": 1,
                "action": "click",
                "selector": _reference(snapshot, "target"),
            },
        )
        assert not result.success and "Ancestor frame changed" in result.error

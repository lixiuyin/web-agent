"""Real-Chromium coverage for modern browser interaction primitives."""

from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.environments.controlled_web.general_site import benchmark_site

from webagent.browser.controller import BrowserController
from webagent.core.config import AgentConfig
from webagent.core.models import ToolCall
from webagent.tools.builtin.advanced_browser_tools import (
    DownloadFileTool,
    FrameInteractTool,
    ListFramesTool,
    ShadowDomTool,
    UploadFileTool,
)
from webagent.tools.builtin.browser_tools import ClickTool
from webagent.tools.executor import ToolExecutor
from webagent.tools.registry import ToolRegistry
from webagent.tools.risk import ActionRiskPolicy, BrowserRiskContext


@pytest.mark.integration
@pytest.mark.asyncio
async def test_frames_tabs_upload_download_and_shadow_dom(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    upload = upload_root / "sample.txt"
    upload.write_text("upload payload", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    config = AgentConfig(_env_file=None, browser_upload_root=upload_root, output_dir=tmp_path)
    browser = BrowserController(headless=True, temporary_profile=True, humanize_delays=False)
    await browser.start()
    try:
        await browser.page.set_content(
            """
            <iframe srcdoc="<button id='inside'>Frame action</button>"></iframe>
            <input id="upload" type="file">
            <a id="download" download="sample.txt" href="data:text/plain,download-payload">Save</a>
            <div id="shadow-host"></div>
            <script>
              const root = document.querySelector('#shadow-host').attachShadow({mode: 'open'});
              root.innerHTML = '<button id="shadow-button">Shadow action</button>';
            </script>
            """
        )

        frames = await ListFramesTool(browser=browser).execute({})
        frame_text = await FrameInteractTool(browser=browser).execute(
            {
                "frame_index": 1,
                "action": "extract_text",
                "selector": {"type": "css", "value": "#inside"},
            }
        )
        uploaded = await UploadFileTool(browser=browser, config=config).execute(
            {
                "selector": {"type": "css", "value": "#upload"},
                "path": str(upload),
            }
        )
        downloaded = await DownloadFileTool(
            browser=browser, artifacts_dir=artifacts, config=config
        ).execute({"selector": {"type": "css", "value": "#download"}})
        shadow = await ShadowDomTool(browser=browser).execute(
            {
                "action": "extract_text",
                "selector": {"type": "css", "value": "#shadow-button"},
            }
        )

        original_tabs = (await browser.list_tabs())["count"]
        opened = await browser.open_tab()
        switched = await browser.switch_tab(0)
        closed = await browser.close_tab(opened["index"])

        assert len(frames.data["frames"]) == 2
        assert frame_text.data["text"] == "Frame action"
        assert uploaded.success is True
        assert await browser.page.locator("#upload").input_value() == "C:\\fakepath\\sample.txt"
        assert Path(downloaded.data["path"]).read_text() == "download-payload"
        assert shadow.data["text"] == "Shadow action"
        assert opened["success"] is True and original_tabs == 1
        assert switched["success"] is True
        assert closed["success"] is True
    finally:
        await browser.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dom_context_denies_opaque_purchase_button_before_click() -> None:
    browser = BrowserController(headless=True, temporary_profile=True, humanize_delays=False)
    await browser.start()
    try:
        await browser.page.set_content(
            """
            <button class="primary" onclick="document.body.dataset.purchased='yes'">
              Place order
            </button>
            """
        )
        registry = ToolRegistry()
        registry.register(ClickTool(browser=browser))
        executor = ToolExecutor(
            registry,
            risk_policy=ActionRiskPolicy("deny", context_provider=BrowserRiskContext(browser)),
        )

        result = await executor.execute(
            ToolCall(
                tool_name="click",
                parameters={"selector": {"type": "css", "value": ".primary"}},
            )
        )

        assert result.success is False
        assert result.audit["risk"]["external_effect"] == "external_state_change"
        assert await browser.page.locator("body").get_attribute("data-purchased") is None
    finally:
        await browser.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_reset_clears_storage_cookies_permissions_and_extra_tabs() -> None:
    browser = BrowserController(headless=True, temporary_profile=True, humanize_delays=False)
    await browser.start()
    try:
        with benchmark_site() as base_url:
            await browser.page.goto(base_url)
            await browser.page.evaluate("localStorage.setItem('leak', 'yes')")
            await browser.context.add_cookies([{"name": "leak", "value": "yes", "url": base_url}])
            await browser.context.grant_permissions(["geolocation"], origin=base_url)
            await browser.open_tab()

            reset = await browser.reset_session_state()
            await browser.page.goto(base_url)

            assert reset == {"success": True, "tabs": 1}
            assert await browser.page.evaluate("localStorage.getItem('leak')") is None
            assert await browser.context.cookies(base_url) == []
            assert (await browser.list_tabs())["count"] == 1
    finally:
        await browser.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_type_text_uses_visible_match_when_hidden_input_comes_first() -> None:
    browser = BrowserController(headless=True, temporary_profile=True, humanize_delays=False)
    await browser.start()
    try:
        await browser.page.set_content(
            """
            <form>
              <input type="hidden" name="stage" value="44">
              <label>Remembered cue <input id="recall-answer" name="answer"></label>
            </form>
            """
        )

        result = await browser.type_text("input", "CEDAR", delay=0)

        assert result["success"] is True
        assert await browser.page.locator("#recall-answer").input_value() == "CEDAR"
        assert await browser.page.locator('input[type="hidden"]').input_value() == "44"
    finally:
        await browser.close()

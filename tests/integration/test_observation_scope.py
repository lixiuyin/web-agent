"""Real rendered geometry remains tied to its viewport after scrolling."""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image

from webagent.agent.loop import WebAgent
from webagent.agent.observations import save_observation
from webagent.browser.controller import BrowserController
from webagent.browser.snapshot import take_snapshot
from webagent.core.config import AgentConfig
from webagent.core.models import BrowserState, ToolCall
from webagent.evaluation.artifacts import RunLayout
from webagent.planner.api import APIPlanner
from webagent.tools.builtin.browser_tools import ScrollTool
from webagent.tools.builtin.task_tools import DoneTool
from webagent.tools.executor import ToolExecutor
from webagent.tools.policy import BrowserGroundedPolicy
from webagent.tools.registry import ToolRegistry
from webagent.utils.images import image_to_jpeg_b64


@pytest.mark.integration
async def test_rendered_inline_urls_ground_policy_but_hidden_urls_do_not(tmp_path):
    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()
    try:
        await browser.page.route(
            "https://fixture.test/**",
            lambda route: route.fulfill(
                content_type="text/html",
                body="<main>Source Code: https://source.test/project</main>"
                "<span hidden>https://hidden.test/private</span>",
            ),
        )
        await browser.goto("https://fixture.test/page")
        captured = await take_snapshot(browser.page, use_cdp=False, wait_after_load=0)
        state = BrowserState(
            url=captured["url"],
            title=captured["title"],
            timestamp=captured["meta"]["timestamp"],
            dom_summary=captured["markdown"],
            observation_metadata=captured["meta"],
            viewport_context=captured["viewport_context"],
            document_context=captured["document_context"],
        )
        assert "https://source.test/project" in state.viewport_context
        policy = BrowserGroundedPolicy(
            browser, artifacts_dir=tmp_path, allowed_tools={"goto", "done"}
        )
        policy.reset("Summarize the current page")
        policy.record_observation(state)
        done = ToolCall(
            tool_name="done", parameters={"summary": "Source: https://source.test/project"}
        )
        assert policy.validate_planner_call(done) is None
        assert (await policy.authorize(done)).allowed
        hidden = ToolCall(tool_name="goto", parameters={"url": "https://hidden.test/private"})
        assert not (await policy.authorize(hidden)).allowed
    finally:
        await browser.close()


@pytest.mark.integration
async def test_scroll_and_full_page_evidence(tmp_path: Path) -> None:
    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()
    try:
        await browser.page.set_viewport_size({"width": 800, "height": 600})
        await browser.page.set_content("""
            <style>body {margin:0; height:2400px} button {position:absolute}</style>
            <button id="top" style="top:20px">Top control</button>
            <button id="bottom" style="top:1600px">Bottom control</button>
            <button id="hidden" style="top:40px;opacity:0">Invisible control</button>
        """)
        before = await take_snapshot(browser.page, use_cdp=False, wait_after_load=0)
        await browser.page.evaluate("window.scrollTo(0, 1400)")
        after = await take_snapshot(
            browser.page,
            use_cdp=False,
            wait_after_load=0,
            supplemental_full_page=True,
        )
        pre = {e["attrs"]["id"]: e for e in before["elements"]}
        post = {e["attrs"]["id"]: e for e in after["elements"]}
        assert pre["top"]["in_viewport"] is True
        assert pre["bottom"]["in_viewport"] is False
        assert "Bottom control" not in before["viewport_context"]
        assert "Bottom control" in before["document_context"]
        assert "hidden" not in pre
        assert post["bottom"]["in_viewport"] is True
        assert post["top"]["in_viewport"] is False
        assert post["bottom"]["bbox"]["y"] == 1600
        assert post["bottom"]["viewport_bbox"]["y"] == 200
        assert after["elements"][0]["attrs"]["id"] == "bottom"
        assert after["meta"]["scroll"]["y"] == 1400
        viewport = Image.open(BytesIO(after["screenshot_bytes"]))
        full = Image.open(BytesIO(after["full_page_screenshot_bytes"]))
        assert viewport.size == (800, 600)
        assert full.size == (800, 2400)
        state = BrowserState(
            screenshot=viewport,
            full_page_screenshot=full,
            dom_summary=after["markdown"],
            elements=after["elements"],
            observation_metadata=after["meta"],
            url=after["url"],
            title=after["title"],
            timestamp=after["meta"]["timestamp"],
        )
        path = save_observation(state, RunLayout.from_root(tmp_path), 1, "post")
        saved = json.loads((tmp_path / path).read_text())
        assert saved["screenshot"]["height"] == 600
        assert saved["full_page_screenshot"]["height"] == 2400
    finally:
        await browser.close()


@pytest.mark.integration
async def test_agent_pairs_scroll_evidence_and_planner_retries(tmp_path, monkeypatch) -> None:
    planner = APIPlanner(
        api_url="https://planner.invalid/api",
        api_key="test",
        output_mode="prompt-json",
        screenshot_mode="always",
    )
    planner._vision._supports_vision = True
    replies = iter(
        [
            "{}",
            '{"tool": "scroll", "parameters": {"direction": "down", "amount_px": 1400}}',
            '{"tool": "done", "parameters": {"summary": "Bottom control is now visible."}}',
        ]
    )

    async def post(url, payload, headers, timeout=None):
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"choices": [{"message": {"content": next(replies)}}]},
        )

    monkeypatch.setattr(planner, "_bounded_post", post)
    async with BrowserController(headless=True, temporary_profile=True) as browser:
        await browser.page.set_viewport_size({"width": 800, "height": 600})
        await browser.page.set_content("""
            <style>body {margin:0; height:2400px} button {position:absolute}</style>
            <button style="top:20px">Top control</button>
            <button style="top:1600px">Bottom control</button>
        """)
        registry = ToolRegistry()
        registry.register(ScrollTool(browser=browser))
        registry.register(DoneTool())
        agent = WebAgent(
            planner=planner,
            browser=browser,
            tool_executor=ToolExecutor(registry),
            config=AgentConfig(
                _env_file=None,
                max_steps=2,
                captcha_pause=False,
                observation_full_page_screenshot=True,
            ),
            output_dir=tmp_path / "run",
        )
        result = await agent.run("Scroll down to inspect the bottom control, then finish.")
    assert result.success
    trace = json.loads(agent.run_layout.trace_path.read_text())
    root = agent.run_layout.root
    refs = trace["steps"][0]["observations"]
    pre = json.loads((root / refs["pre"]).read_text())
    post = json.loads((root / refs["post"]).read_text())
    assert pre["metadata"]["scroll"]["y"] == 0
    assert post["metadata"]["scroll"]["y"] == 1400
    assert pre["screenshot"]["sha256"] != post["screenshot"]["sha256"]
    assert post["full_page_screenshot"]["height"] == 2400
    attempts = trace["planner_attempts"]
    assert [a["success"] for a in attempts] == [False, True, True]
    assert attempts[0]["observation_path"] == attempts[1]["observation_path"] == refs["pre"]
    assert attempts[2]["observation_path"] == trace["steps"][1]["observations"]["pre"]
    assert all(a["observation_input"]["screenshot_sent"] for a in attempts)
    sent_bytes = base64.b64decode(image_to_jpeg_b64(Image.open(root / pre["screenshot"]["path"])))
    assert (
        hashlib.sha256(sent_bytes).hexdigest()
        == attempts[0]["observation_input"]["screenshot_sha256"]
    )

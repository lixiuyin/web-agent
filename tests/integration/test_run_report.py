"""The offline reader is rendered in a real browser, including missing captures."""

import json
from pathlib import Path

import pytest
from PIL import Image

from webagent.agent.catalog import write_catalog
from webagent.agent.observations import save_observation
from webagent.agent.report import write_report
from webagent.browser.controller import BrowserController
from webagent.core.models import BrowserState
from webagent.evaluation.artifacts import RunLayout


@pytest.mark.integration
async def test_offline_report_loads_images_and_does_not_execute_page_content(tmp_path: Path):
    state = BrowserState(
        screenshot=Image.new("RGB", (800, 600), "navy"),
        dom_summary="<script>window.pwned=true</script>",
        url="https://test/",
        title="Test",
        timestamp="now",
        observation_metadata={"status": "complete"},
    )
    pre = save_observation(state, RunLayout.from_root(tmp_path), 1, "pre")
    trace = {
        "task": "Report reader regression",
        "status": "failed",
        "steps": [],
        "planner_attempts": [
            {
                "step_number": 1,
                "observation_path": pre,
                "observation_input": {
                    "screenshot_sent": False,
                    "screenshot_omission_reason": "auto_dom_sufficient",
                },
            }
        ],
    }
    path = write_report(tmp_path, trace)
    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()
    try:
        await browser.page.goto(path.as_uri())
        assert await browser.page.locator("section").count() == 1
        assert await browser.page.locator("img").get_attribute("width") == "800"
        assert await browser.page.locator("img").get_attribute("height") == "600"
        assert "no executed action" in await browser.page.locator("h2").inner_text()
        await browser.page.locator("img").scroll_into_view_if_needed()
        await browser.page.wait_for_function(
            "Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)"
        )
        assert await browser.page.locator("img").evaluate(
            "image => image.complete && image.naturalWidth === 800"
        )
        assert await browser.page.evaluate("window.pwned === undefined")
        await browser.page.set_viewport_size({"width": 390, "height": 844})
        assert await browser.page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert "auto_dom_sufficient" in await browser.page.inner_text("body")
        assert json.loads((tmp_path / pre).read_text())["screenshot"]["width"] == 800
    finally:
        await browser.close()


@pytest.mark.integration
async def test_date_catalog_links_to_readers_at_mobile_width(tmp_path):
    root = tmp_path / "2026-09-08"
    run = root / "model" / ("long-task-name-" * 6)
    (run / "trajectory").mkdir(parents=True)
    trace = {
        "task": "Audit",
        "status": "blocked",
        "steps": [],
        "events": [{"type": "captcha_detected", "reason": "challenge"}],
    }
    (run / "trajectory/trace.json").write_text(json.dumps(trace))
    write_report(run, trace)
    path = write_catalog(root)
    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()
    try:
        await browser.page.set_viewport_size({"width": 390, "height": 844})
        await browser.page.goto(path.as_uri())
        assert await browser.page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await browser.page.locator("a").click()
        assert await browser.page.locator("h1").inner_text() == "Audit"
        assert "challenge" in await browser.page.inner_text("body")
    finally:
        await browser.close()

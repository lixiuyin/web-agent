"""Real-Chromium coverage for delayed hydration before screenshot capture."""

from __future__ import annotations

import asyncio

import pytest

from webagent.browser.controller import BrowserController
from webagent.browser.snapshot import take_snapshot, wait_for_page_stability


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delayed_hydration_is_visible_before_snapshot() -> None:
    browser = BrowserController(headless=True, temporary_profile=True)
    await browser.start()
    try:
        await browser.page.set_content(
            """
            <main id="app">Loading…</main>
            <script>
              setTimeout(() => {
                document.querySelector('#app').textContent = 'Final hydrated content';
                const button = document.createElement('button');
                button.textContent = 'Continue';
                document.querySelector('#app').appendChild(button);
              }, 650);
            </script>
            """,
            wait_until="domcontentloaded",
        )
        loading_screenshot = await browser.page.screenshot(type="png")

        # Mirrors the default post-action minimum wait before WebAgent._observe().
        await asyncio.sleep(0.5)
        stable = await wait_for_page_stability(
            browser.page,
            timeout_ms=3000,
            stable_ms=400,
        )
        snapshot = await take_snapshot(
            browser.page,
            use_cdp=False,
            wait_after_load=0,
        )

        assert stable is True
        assert "Final hydrated content" in snapshot["html"]
        assert "Loading…" not in snapshot["html"]
        assert snapshot["screenshot_bytes"]
        assert snapshot["screenshot_bytes"] != loading_screenshot
    finally:
        await browser.close()

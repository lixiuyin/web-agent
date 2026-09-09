"""Chromium navigation response evidence and stale-document rejection."""

import pytest

from webagent.benchmarks.environments.controlled_web.general_site import benchmark_site
from webagent.browser.controller import BrowserController


@pytest.mark.integration
async def test_navigation_recovery_requires_real_redirect_response(tmp_path, monkeypatch):
    with benchmark_site() as origin:
        async with BrowserController(
            headless=True,
            temporary_profile=True,
            temporary_profile_root=tmp_path,
            humanize_delays=False,
        ) as browser:
            goto = browser.page.goto

            async def redirected_then_aborted(url, **kwargs):
                await goto(url, **kwargs)
                raise RuntimeError("net::ERR_ABORTED")

            monkeypatch.setattr(browser.page, "goto", redirected_then_aborted)
            result = await browser.goto(origin + "/dashboard")
            assert result["success"] is True, result
            assert result["url"] == origin + "/login"
            assert result["recovered_from"] == "net::ERR_ABORTED"

            async def aborted_before_navigation(url, **kwargs):
                raise RuntimeError("net::ERR_ABORTED")

            monkeypatch.setattr(browser.page, "goto", aborted_before_navigation)
            result = await browser.goto(origin + "/catalog")
            assert result["success"] is False
            assert browser.page.url == origin + "/login"

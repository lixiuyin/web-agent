"""Real browser checkpoint storage round-trip and validation boundaries."""

from copy import deepcopy

import pytest

from webagent.benchmarks.environments.controlled_web.general_site import benchmark_site
from webagent.browser.controller import BrowserController


@pytest.mark.integration
async def test_checkpoint_restores_browser_storage_and_rejects_invalid_input(tmp_path):
    with benchmark_site() as origin:
        async with BrowserController(
            headless=True,
            temporary_profile=True,
            temporary_profile_root=tmp_path / "profiles",
            humanize_delays=False,
        ) as browser:
            await browser.page.goto(origin)
            await browser.page.evaluate(
                "localStorage.setItem('checkpoint-mode', 'saved'); document.cookie = 'checkpoint_session=original; Path=/'"
            )
            state = await browser.export_checkpoint_state(include_storage=True)
            invalid = deepcopy(state)
            invalid["storage_state"]["cookies"][0]["value"] = "must-not-be-applied"
            invalid["storage_state"]["origins"].append(
                {"origin": "file:///tmp/invalid", "localStorage": []}
            )
            with pytest.raises(ValueError):
                await browser.restore_checkpoint_state(invalid)
            assert await browser.page.evaluate("document.cookie") == "checkpoint_session=original"
            assert browser.page.url == origin + "/"
            await browser.page.evaluate(
                "localStorage.clear(); document.cookie = 'checkpoint_session=changed; Path=/'"
            )
            await browser.restore_checkpoint_state(state)
            assert await browser.page.evaluate("localStorage.getItem('checkpoint-mode')") == "saved"
            assert await browser.page.evaluate("document.cookie") == "checkpoint_session=original"

            await browser.page.evaluate("localStorage.setItem('checkpoint-mode', 'new-edit')")
            await browser.page.reload()
            assert (
                await browser.page.evaluate("localStorage.getItem('checkpoint-mode')") == "new-edit"
            )
            await browser.page.goto(origin + "/catalog")
            assert (
                await browser.page.evaluate("localStorage.getItem('checkpoint-mode')") == "new-edit"
            )
            new_state = await browser.export_checkpoint_state(include_storage=True)
            await browser.restore_checkpoint_state(state)
            await browser.restore_checkpoint_state(new_state)
            await browser.page.reload()
            assert (
                await browser.page.evaluate("localStorage.getItem('checkpoint-mode')") == "new-edit"
            )

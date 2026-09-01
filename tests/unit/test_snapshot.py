"""Tests for snapshot configuration and the minimal CDP wrapper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from webagent.browser.cdp_service import CDPService
from webagent.browser.snapshot import _filter_and_dedupe, _sanitize_html


def test_ad_filtering_can_be_disabled():
    raw = [
        {"tag": "button", "text": "Sponsored result", "attrs": {"class": "ad"}},
        {"tag": "button", "text": "Continue", "attrs": {"id": "continue"}},
    ]

    assert [element["text"] for element in _filter_and_dedupe(raw)] == ["Continue"]
    assert [element["text"] for element in _filter_and_dedupe(raw, filter_ads=False)] == [
        "Sponsored result",
        "Continue",
    ]


def test_html_ad_filtering_can_be_disabled():
    html = '<html><body><div class="ad-banner">Offer</div><main>Article</main></body></html>'

    assert "Offer" not in _sanitize_html(html)
    assert "Offer" in _sanitize_html(html, filter_ads=False)


async def test_cdp_service_enables_only_accessibility_domain():
    session = MagicMock()
    session.send = AsyncMock(side_effect=[None, {"nodes": [{"role": {"value": "button"}}]}])
    session.detach = AsyncMock()
    page = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=session)

    async with CDPService(page) as service:
        tree = await service.get_ax_tree()

    assert tree == {"nodes": [{"role": {"value": "button"}}]}
    assert session.send.await_args_list[0].args == ("Accessibility.enable", {})
    assert session.send.await_args_list[1].args == ("Accessibility.getFullAXTree",)
    assert session.send.await_count == 2
    session.detach.assert_awaited_once()


async def test_cdp_start_failure_leaves_cdp_none():
    page = MagicMock()
    page.context.new_cdp_session = AsyncMock(side_effect=RuntimeError("no cdp"))
    service = CDPService(page)
    await service.start()
    assert service._cdp is None
    # get_ax_tree short-circuits to None when there is no session.
    assert await service.get_ax_tree() is None


async def test_cdp_get_ax_tree_send_failure_returns_none():
    session = MagicMock()
    session.send = AsyncMock(side_effect=[None, RuntimeError("send failed")])
    session.detach = AsyncMock()
    page = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=session)

    async with CDPService(page) as service:
        assert await service.get_ax_tree() is None


async def test_cdp_enable_domain_failure_is_swallowed():
    session = MagicMock()
    session.send = AsyncMock(side_effect=RuntimeError("enable failed"))
    session.detach = AsyncMock()
    page = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=session)

    service = CDPService(page)
    await service.start()  # enable fails but start() must not raise
    assert "Accessibility" not in service._domain_enabled
    await service.stop()


async def test_cdp_stop_swallows_detach_error():
    session = MagicMock()
    session.send = AsyncMock(return_value=None)
    session.detach = AsyncMock(side_effect=RuntimeError("detach boom"))
    page = MagicMock()
    page.context.new_cdp_session = AsyncMock(return_value=session)

    service = CDPService(page)
    await service.start()
    await service.stop()  # must not raise despite detach error
    assert service._cdp is None

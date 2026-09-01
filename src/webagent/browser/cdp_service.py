"""Minimal Chrome DevTools Protocol wrapper for accessibility snapshots."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import CDPSession, Page

logger = logging.getLogger("webagent")


class CDPService:
    """Own a CDP session and expose the accessibility tree used by snapshots."""

    def __init__(self, page: Page) -> None:
        """Initialize CDP service.

        Args:
            page: Playwright page object
        """
        self.page = page
        self._cdp: CDPSession | None = None
        self._domain_enabled: set[str] = set()

    async def __aenter__(self) -> CDPService:
        """Initialize CDP session."""
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Cleanup CDP session."""
        await self.stop()

    async def start(self) -> None:
        """Start CDP session and enable required domains."""
        try:
            self._cdp = await self.page.context.new_cdp_session(self.page)
            await self._enable_domain("Accessibility")
            logger.debug("CDP service started")
        except Exception as exc:
            logger.warning("Failed to start CDP service: %s", exc)
            self._cdp = None

    async def stop(self) -> None:
        """Stop CDP session."""
        if self._cdp:
            try:
                await self._cdp.detach()
            except Exception:
                pass
        self._cdp = None
        self._domain_enabled.clear()

    async def _enable_domain(self, domain: str) -> None:
        """Enable a CDP domain if not already enabled."""
        if domain not in self._domain_enabled and self._cdp:
            try:
                method = f"{domain}.enable"
                await self._cdp.send(method, {})
                self._domain_enabled.add(domain)
                logger.debug("CDP domain enabled: %s", domain)
            except Exception as exc:
                logger.debug("Failed to enable CDP domain %s: %s", domain, exc)

    async def get_ax_tree(self) -> dict[str, Any] | None:
        """Get the full Accessibility Tree.

        Returns:
            AX tree with semantic information, or None if unavailable
        """
        if not self._cdp:
            return None

        try:
            return await self._cdp.send("Accessibility.getFullAXTree")
        except Exception as exc:
            logger.warning("Failed to get AX tree: %s", exc)
            return None

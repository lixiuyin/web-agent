"""Chrome DevTools Protocol service wrapper.

This module provides low-level access to Chrome's CDP for advanced
features like AX Tree, computed styles, and DOM inspection that browser-use
relies on for accurate semantic understanding of web pages.
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

logger = logging.getLogger("webagent")


class CDPService:
    """Chrome DevTools Protocol service wrapper.

    Provides access to:
    - AX Tree: Accessibility tree with semantic information
    - Computed Styles: Real CSS styles applied to elements
    - DOM: Complete DOM tree with node IDs
    - Runtime: JavaScript execution in page context
    """

    def __init__(self, page: Page) -> None:
        """Initialize CDP service.

        Args:
            page: Playwright page object
        """
        self.page = page
        self._cdp: Any = None
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
            # Enable essential domains
            await self._enable_domain("DOM")
            await self._enable_domain("Runtime")
            await self._enable_domain("Accessibility")
            await self._enable_domain("CSS")
            logger.debug("CDP service started")
        except Exception as e:
            logger.warning(f"Failed to start CDP service: {e}")
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
                logger.debug(f"CDP domain enabled: {domain}")
            except Exception as e:
                logger.debug(f"Failed to enable domain {domain}: {e}")

    async def get_ax_tree(self) -> dict[str, Any] | None:
        """Get the full Accessibility Tree.

        Returns:
            AX tree with semantic information, or None if unavailable
        """
        if not self._cdp:
            return None

        try:
            result = await self._cdp.send("Accessibility.getFullAXTree")
            return result
        except Exception as e:
            logger.warning(f"Failed to get AX tree: {e}")
            return None

    async def get_dom_tree(self) -> dict[str, Any] | None:
        """Get the flattened DOM tree.

        Returns:
            DOM tree with node structure, or None if unavailable
        """
        if not self._cdp:
            return None

        try:
            result = await self._cdp.send(
                "DOM.getFlattenedDocument",
                {
                    "depth": -1,  # Include all descendants
                    "pierce": True,  # Include shadow DOM and iframes
                },
            )
            return result
        except Exception as e:
            logger.warning(f"Failed to get DOM tree: {e}")
            return None

    async def get_computed_style(
        self,
        object_id: str | None = None,
        node_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Get computed styles for an element.

        Args:
            object_id: Object ID from AX tree (for Runtime.evaluate)
            node_id: Node ID from DOM tree (for CSS.getComputedStyle)

        Returns:
            Dictionary of computed CSS properties, or None if unavailable
        """
        if not self._cdp:
            return None

        try:
            if node_id is not None:
                result = await self._cdp.send("CSS.getComputedStyle", {"nodeId": node_id})
                return result
            elif object_id is not None:
                result = await self._cdp.send(
                    "CSS.getComputedStyleForNode", {"objectId": object_id}
                )
                return result.get("computedStyle", {})
            return None
        except Exception as e:
            logger.debug(f"Failed to get computed style: {e}")
            return None

    async def evaluate(
        self,
        expression: str,
        await_promise: bool = False,
        return_by_value: bool = False,
    ) -> Any:
        """Execute JavaScript in page context.

        Args:
            expression: JavaScript expression to evaluate
            await_promise: Whether to wait for Promise resolution
            return_by_value: Whether to return JSON-serializable result

        Returns:
            Result of evaluation, or None if unavailable
        """
        if not self._cdp:
            return None

        try:
            result = await self._cdp.send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "awaitPromise": await_promise,
                    "returnByValue": return_by_value,
                },
            )
            return result.get("result", {})
        except Exception as e:
            logger.debug(f"Failed to evaluate JS: {e}")
            return None

    async def get_box_model(
        self,
        node_id: int | None = None,
        object_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get the box model for an element.

        Args:
            node_id: Backend DOM node ID
            object_id: JavaScript object ID

        Returns:
            Box model with content/padding/border/margin boxes
        """
        if not self._cdp:
            return None

        try:
            if object_id:
                result = await self._cdp.send("DOM.getBoxModel", {"objectId": object_id})
            elif node_id:
                result = await self._cdp.send("DOM.getBoxModel", {"nodeId": node_id})
            else:
                return None
            return result
        except Exception as e:
            logger.debug(f"Failed to get box model: {e}")
            return None


async def get_cdp_service(page: Page) -> CDPService:
    """Convenience function to get a CDP service instance.

    Args:
        page: Playwright page object

    Returns:
        CDPService instance (auto-started)
    """
    service = CDPService(page)
    await service.start()
    return service

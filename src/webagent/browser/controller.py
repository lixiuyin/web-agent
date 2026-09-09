"""Playwright browser lifecycle and user-facing browser actions."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import shutil
import sys
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from PIL import Image
from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)

from webagent.browser.checkpoint import export_checkpoint_state, restore_checkpoint_tabs
from webagent.browser.link_clicking import click_link_by_text_strategies
from webagent.browser.navigation import NavigationAttempt
from webagent.browser.profiles import create_temporary_profile, mark_profile_clean
from webagent.browser.search_parsers import SEARCH_PARSERS, detect_search_engine
from webagent.browser.stealth import (
    ENHANCED_STEALTH_SCRIPT,
    get_stealth_args,
    get_stealth_user_agent,
)

logger = logging.getLogger(__name__)


_LINK_EVIDENCE_TERMS = (
    "technical report",
    "tech_report",
    "tech-report",
    "paper",
    "report",
    "arxiv",
    "download",
    "raw",
)
_LINK_CHROME_PATHS = (
    "/features/",
    "/marketplace",
    "/resources/",
    "/solutions/",
    "/enterprise",
    "/customer-stories",
)


def _link_priority(link: dict[str, str]) -> int:
    """Rank document/content links ahead of global navigation chrome."""
    href = link["href"].casefold()
    text = link["text"].casefold()
    searchable = f"{href} {text}"
    score = 0
    if href.split("?", 1)[0].endswith(".pdf"):
        score += 100
    score += 20 * sum(term in searchable for term in _LINK_EVIDENCE_TERMS)
    if any(path in href for path in _LINK_CHROME_PATHS):
        score -= 100
    if not text:
        score -= 5
    return score


def _effective_headless(requested_headless: bool) -> bool:
    """Resolve whether to run headless.

    macOS and Windows do not use ``DISPLAY``; Playwright can open a headed window
    there without it. Only on Linux, when no display server is available (Docker,
    SSH without X forwarding), fall back to headless even if the caller asked for
    a visible browser.
    """
    if requested_headless:
        return True
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        logger.info(
            "No DISPLAY or WAYLAND_DISPLAY on Linux — running headless despite headed request"
        )
        return True
    return False


class BrowserController:
    """Async Playwright Chromium controller with opt-in compatibility stealth."""

    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        default_timeout: int = 30000,
        slow_mo: int = 0,
        user_data_dir: str | Path = "./browser_profile",
        temporary_profile: bool = True,
        temporary_profile_root: str | Path | None = None,
        browser_channel: str | None = None,
        proxy_server: str | None = None,
        stealth_mode: bool = False,
        humanize_delays: bool = False,
        ignore_https_errors: bool = False,
        locale: str | None = None,
        timezone_id: str | None = None,
        stale_profile_max_age_seconds: float = 3600.0,
    ) -> None:
        self.headless = _effective_headless(headless)
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.default_timeout = default_timeout
        self.slow_mo = slow_mo
        self.user_data_dir = str(user_data_dir)
        self.temporary_profile = temporary_profile
        self.temporary_profile_root = (
            Path(temporary_profile_root).resolve() if temporary_profile_root is not None else None
        )
        self._owned_profile_dir: Path | None = None
        self.browser_channel = browser_channel
        self.proxy_server = proxy_server.strip() if proxy_server else None
        self.stealth_mode = stealth_mode
        self.humanize_delays = humanize_delays
        self.ignore_https_errors = ignore_https_errors
        self.locale = locale
        self.timezone_id = timezone_id
        self.stale_profile_max_age_seconds = stale_profile_max_age_seconds

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None

    async def __aenter__(self) -> BrowserController:
        await self.start()
        return self

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._playwright is not None:
            raise RuntimeError("Browser already started; call close() before starting again")

        if self.temporary_profile:
            self._owned_profile_dir = create_temporary_profile(
                self.temporary_profile_root,
                stale_max_age_seconds=self.stale_profile_max_age_seconds,
            )
            self.user_data_dir = str(self._owned_profile_dir)
        else:
            # Suppress Chromium's stale crash-restore banner before launching.
            mark_profile_clean(self.user_data_dir)

        self._playwright = await async_playwright().start()

        # Enhanced stealth is optional; default mode uses native Playwright settings.
        if self.stealth_mode:
            user_agent = get_stealth_user_agent()
            args = get_stealth_args(headless=self.headless)
            stealth_script = ENHANCED_STEALTH_SCRIPT
        else:
            user_agent = None
            args = []
            stealth_script = ""

        # Locale/timezone overrides are explicit; defaults preserve the native environment.
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=args,
            user_agent=user_agent,
            locale=self.locale,
            timezone_id=self.timezone_id,
            # Do not pre-grant privacy-sensitive capabilities to arbitrary sites.
            permissions=[],
            color_scheme="light",
            device_scale_factor=1.0,
            ignore_https_errors=self.ignore_https_errors,
            accept_downloads=True,
            proxy={"server": self.proxy_server} if self.proxy_server else None,
            channel=self.browser_channel,
        )

        # CDP is used for richer page observation, not browser fingerprint modification.
        try:
            self._cdp = await self._context.new_cdp_session(self._context.pages[0])
            await self._cdp.send("Page.enable")
            await self._cdp.send("Runtime.enable")
        except Exception:
            self._cdp = None

        # Inject stealth script
        if stealth_script:
            await self._context.add_init_script(stealth_script)

        self._browser = self._context.browser
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        await self._page.set_viewport_size(
            {"width": self.viewport_width, "height": self.viewport_height}
        )
        self._page.set_default_timeout(self.default_timeout)

        # Random delay is an explicit compatibility opt-in.
        if self.humanize_delays:
            await asyncio.sleep(random.uniform(0.5, 1.5))

    async def close(self) -> None:
        # Detach CDP session first (non-fatal)
        if self._cdp is not None:
            try:
                await self._cdp.detach()
            except Exception:
                pass
            self._cdp = None

        self._page = None  # owned by context; don't close separately

        # Closing the persistent context also closes all pages and the browser
        if self._context is not None:
            try:
                await asyncio.wait_for(self._context.close(), timeout=15)
            except Exception as exc:
                logger.warning("Failed to close Chromium context cleanly: %s", exc)
            self._context = None

        self._browser = None  # already gone after context.close()

        if self._playwright is not None:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=10)
            except Exception as exc:
                logger.warning("Failed to stop Playwright cleanly: %s", exc)
            self._playwright = None

        if self._owned_profile_dir is not None:
            profile = self._owned_profile_dir
            self._owned_profile_dir = None
            try:
                shutil.rmtree(profile)
            except OSError as exc:
                logger.warning("Failed to remove temporary browser profile %s: %s", profile, exc)
        else:
            mark_profile_clean(self.user_data_dir)

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started; call start() first")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser not started; call start() first")
        return self._context

    async def reset_session_state(self) -> dict[str, Any]:
        """Clear per-task browser state while keeping the Chromium process warm."""
        if self._context is None:
            return {"success": False, "error": "Browser not started"}
        pages = list(self._context.pages)
        for page in pages:
            if page.url.startswith(("http://", "https://")):
                try:
                    await page.evaluate("localStorage.clear(); sessionStorage.clear()")
                except Exception:
                    pass
        for page in pages[1:]:
            await page.close()
        self._page = pages[0] if pages else await self._context.new_page()
        await self._context.clear_cookies()
        await self._context.clear_permissions()
        await self._page.bring_to_front()
        return {"success": True, "tabs": len(self._context.pages)}

    async def export_checkpoint_state(self, *, include_storage: bool = False) -> dict[str, Any]:
        """Capture tabs and optionally private authenticated storage for resume."""
        if self._context is None or self._page is None:
            raise RuntimeError("Browser not started")
        return await export_checkpoint_state(
            self._context, self._page, include_storage=include_storage
        )

    async def restore_checkpoint_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Restore validated checkpoint tabs and select the saved active page."""
        if self._context is None:
            raise RuntimeError("Browser not started")
        self._page, tab_count, active_index = await restore_checkpoint_tabs(self._context, state)
        await self._page.bring_to_front()
        return {"success": True, "tabs": tab_count, "active_index": active_index}

    async def list_tabs(self) -> dict[str, Any]:
        """Return all open pages and identify the active tab."""
        if self._context is None:
            return {"success": False, "tabs": [], "error": "Browser not started"}
        tabs = []
        for index, page in enumerate(self._context.pages):
            tabs.append(
                {
                    "index": index,
                    "url": page.url,
                    "title": await page.title(),
                    "active": page is self._page,
                }
            )
        return {"success": True, "tabs": tabs, "count": len(tabs)}

    async def switch_tab(self, index: int) -> dict[str, Any]:
        """Make one existing page the active tab."""
        if self._context is None or index < 0 or index >= len(self._context.pages):
            return {"success": False, "index": index, "error": "Tab index out of range"}
        self._page = self._context.pages[index]
        await self._page.bring_to_front()
        return {
            "success": True,
            "index": index,
            "url": self._page.url,
            "title": await self._page.title(),
        }

    async def open_tab(self, url: str | None = None) -> dict[str, Any]:
        """Open and activate a new tab, optionally navigating to a grounded URL."""
        if self._context is None:
            return {"success": False, "error": "Browser not started"}
        self._page = await self._context.new_page()
        if url:
            response = await self._page.goto(url, wait_until="domcontentloaded")
            status = response.status if response else None
        else:
            status = None
        return {
            "success": True,
            "index": self._context.pages.index(self._page),
            "url": self._page.url,
            "title": await self._page.title(),
            "status": status,
        }

    async def close_tab(self, index: int | None = None) -> dict[str, Any]:
        """Close a tab while keeping at least one page available."""
        if self._context is None or not self._context.pages:
            return {"success": False, "error": "Browser not started"}
        pages = self._context.pages
        target_index = pages.index(self.page) if index is None else index
        if target_index < 0 or target_index >= len(pages):
            return {"success": False, "index": target_index, "error": "Tab index out of range"}
        target = pages[target_index]
        await target.close()
        remaining = self._context.pages
        if not remaining:
            self._page = await self._context.new_page()
        elif target is self._page:
            self._page = remaining[min(target_index, len(remaining) - 1)]
            await self._page.bring_to_front()
        return {
            "success": True,
            "closed_index": target_index,
            "active_url": self.page.url,
            "remaining": len(self._context.pages),
        }

    async def goto(
        self,
        url: str,
        wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load",
        timeout: int | None = None,
    ) -> dict[str, Any]:
        attempt: NavigationAttempt | None = None
        try:
            attempt = NavigationAttempt(self.page, url)
            response = await self.page.goto(
                url, wait_until=wait_until, timeout=timeout or self.default_timeout
            )

            # Add human-like behavior after navigation in stealth mode
            if self.stealth_mode and self.humanize_delays and response and response.ok:
                await asyncio.sleep(random.uniform(0.5, 1.5))
                # Random small scroll to simulate human behavior
                if random.random() > 0.5:
                    await self.page.evaluate(f"window.scrollBy(0, {random.randint(50, 200)})")

            return {
                "success": True,
                "url": self.page.url,
                "title": await self.page.title(),
                "status": response.status if response else None,
            }
        except PlaywrightTimeout as e:
            return {"success": False, "url": url, "title": None, "error": f"Timeout: {e}"}
        except Exception as e:
            error = str(e)
            # Chromium can raise ERR_ABORTED when a navigation is replaced by
            # an immediate redirect even though the destination page finishes
            # loading.  Search engines do this frequently for regional routing.
            # Recover only when an ordinary HTTP(S) document is inspectable;
            # downloads and genuinely aborted/blank navigations still fail.
            if "net::ERR_ABORTED" in error and attempt is not None:
                recovered = await self._recover_aborted_navigation(attempt)
                if recovered is not None:
                    return recovered
            return {"success": False, "url": url, "title": None, "error": error}
        finally:
            if attempt is not None:
                attempt.close()

    async def _recover_aborted_navigation(
        self, attempt: NavigationAttempt
    ) -> dict[str, Any] | None:
        """Recover only an inspectable document on the requested host or a subdomain."""
        for retry_index in range(3):
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception:
                pass
            try:
                current_url = self.page.url
                parsed = urlparse(current_url)
                title = await self.page.title()
                body = await self.page.locator("body").count()
                if (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and body
                    and attempt.can_recover(current_url)
                ):
                    return {
                        "success": True,
                        "url": current_url,
                        "title": title,
                        "status": None,
                        "recovered_from": "net::ERR_ABORTED",
                    }
            except Exception:
                pass
            if retry_index < 2:
                await asyncio.sleep(0.5)
        return None

    async def click(
        self, selector: str, timeout: int | None = None, force: bool = False
    ) -> dict[str, Any]:
        pages_before = set(self._context.pages) if self._context is not None else set()
        try:
            await self.page.click(selector, timeout=timeout or self.default_timeout, force=force)
            activated = await self._activate_new_page(pages_before)
            return {"success": True, "selector": selector, **activated}
        except PlaywrightTimeout:
            return {"success": False, "selector": selector, "error": f"Not found: {selector}"}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    async def click_link_by_text(
        self,
        text: str,
        timeout: int | None = None,
        fuzzy: bool = True,
    ) -> dict[str, Any]:
        """Click a link by searching for matching text.

        This is more flexible than exact text matching and works well for
        search results where the visible text might differ from snippets.
        Matching strategies (exact, fuzzy, keyword, arXiv/DOI id, PDF URL)
        live in ``webagent.browser.link_clicking``.

        Args:
            text: The text to search for in the link
            timeout: Maximum time to wait
            fuzzy: If True, tries partial matching when exact match fails

        Returns:
            Dict with success status and found element info
        """
        pages_before = set(self._context.pages) if self._context is not None else set()
        result = await click_link_by_text_strategies(self.page, text, fuzzy)
        if result.get("success"):
            result.update(await self._activate_new_page(pages_before))
        return result

    async def _activate_new_page(self, pages_before: set[Page]) -> dict[str, Any]:
        """Follow a popup/new-tab click so the next observation sees its destination."""
        if self._context is None:
            return {}
        new_pages = [page for page in self._context.pages if page not in pages_before]
        # Chromium can emit the page event just after ``page.click`` resolves.
        # Wait briefly for that bounded race instead of forcing the planner to
        # spend two environment steps on list_tabs + switch_tab.
        for _ in range(10):
            if new_pages:
                break
            await asyncio.sleep(0.05)
            new_pages = [page for page in self._context.pages if page not in pages_before]
        if not new_pages:
            return {}
        self._page = new_pages[-1]
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await self._page.bring_to_front()
        title = ""
        try:
            title = await self._page.title()
        except Exception:
            pass
        return {
            "opened_new_tab": True,
            "tab_index": self._context.pages.index(self._page),
            "url": self._page.url,
            "title": title,
        }

    async def type_text(
        self,
        selector: str,
        text: str,
        delay: int = 50,
        clear_first: bool = True,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        try:
            effective_timeout = timeout or self.default_timeout
            # A broad selector such as ``input`` commonly matches a hidden form
            # field before the user-editable control. Page-level fill/type use
            # strict selector resolution and can therefore wait on the hidden
            # first match until timeout. Resolve the first *visible* match as a
            # locator so ordinary browser-grounded selectors remain usable.
            target = self.page.locator(selector).filter(visible=True).first
            await target.wait_for(state="visible", timeout=effective_timeout)
            if clear_first:
                await target.fill("", timeout=effective_timeout)
            await target.type(text, delay=delay, timeout=effective_timeout)
            return {"success": True, "selector": selector, "text": text}
        except PlaywrightTimeout:
            return {
                "success": False,
                "selector": selector,
                "text": text,
                "error": f"Not found: {selector}",
            }
        except Exception as e:
            return {"success": False, "selector": selector, "text": text, "error": str(e)}

    async def press_key(
        self, key: str, selector: str | None = None, timeout: int | None = None
    ) -> dict[str, Any]:
        try:
            if selector:
                await self.page.focus(selector, timeout=timeout or self.default_timeout)
            await self.page.keyboard.press(key)
            return {"success": True, "key": key, "selector": selector}
        except Exception as e:
            return {"success": False, "key": key, "selector": selector, "error": str(e)}

    async def wait(self, milliseconds: int) -> dict[str, Any]:
        await asyncio.sleep(milliseconds / 1000)
        return {"success": True, "waited_ms": milliseconds}

    async def screenshot(
        self,
        full_page: bool = False,
        quality: int = 80,
        return_format: Literal["pil", "base64", "bytes"] = "pil",
    ) -> dict[str, Any]:
        try:
            raw = await self.page.screenshot(type="jpeg", quality=quality, full_page=full_page)
            image = Image.open(BytesIO(raw))
            w, h = image.size
            if return_format == "pil":
                img_out: Any = image
            elif return_format == "base64":
                img_out = base64.b64encode(raw).decode("utf-8")
            else:
                img_out = raw
            return {"success": True, "image": img_out, "width": w, "height": h}
        except Exception as e:
            return {"success": False, "image": None, "width": 0, "height": 0, "error": str(e)}

    async def scroll(
        self, direction: Literal["up", "down"] = "down", amount: int = 500
    ) -> dict[str, Any]:
        try:
            delta = amount if direction == "down" else -amount
            await self.page.mouse.wheel(0, delta)
            await asyncio.sleep(0.1)
            return {"success": True, "direction": direction, "amount": amount}
        except Exception as e:
            return {"success": False, "direction": direction, "amount": amount, "error": str(e)}

    async def get_element_text(self, selector: str, timeout: int | None = None) -> dict[str, Any]:
        try:
            text = await self.page.text_content(selector, timeout=timeout or self.default_timeout)
            return {"success": True, "selector": selector, "text": text or ""}
        except Exception as e:
            return {"success": False, "selector": selector, "text": "", "error": str(e)}

    async def wait_for_selector(
        self,
        selector: str,
        state: Literal["attached", "detached", "visible", "hidden"] = "visible",
        timeout: int | None = None,
    ) -> dict[str, Any]:
        try:
            await self.page.wait_for_selector(
                selector, state=state, timeout=timeout or self.default_timeout
            )
            return {"success": True, "selector": selector, "state": state}
        except Exception as e:
            return {"success": False, "selector": selector, "state": state, "error": str(e)}

    async def hover(self, selector: str, timeout: int | None = None) -> dict[str, Any]:
        """Hover over an element."""
        try:
            await self.page.hover(selector, timeout=timeout or self.default_timeout)
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    async def select_option(
        self,
        selector: str,
        value: str | None = None,
        label: str | None = None,
        index: int | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Select an option from a <select> element by value, label, or index."""
        try:
            # Build select_option kwargs - only pass non-None values
            select_kwargs: dict[str, Any] = {}
            if value is not None:
                select_kwargs["value"] = value
            if label is not None:
                select_kwargs["label"] = label
            if index is not None:
                select_kwargs["index"] = index

            await self.page.select_option(
                selector, **select_kwargs, timeout=timeout or self.default_timeout
            )
            return {"success": True, "selector": selector, "option": select_kwargs}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    async def get_attribute(
        self, selector: str, attribute: str, timeout: int | None = None
    ) -> dict[str, Any]:
        """Get an attribute value from an element."""
        try:
            # Playwright's query_selector doesn't accept timeout directly
            # Use wait_for_selector with timeout instead
            if timeout:
                element = await self.page.wait_for_selector(selector, timeout=timeout)
            else:
                element = await self.page.query_selector(selector)

            if element is None:
                return {
                    "success": False,
                    "selector": selector,
                    "attribute": attribute,
                    "error": "Element not found",
                }
            value = await element.get_attribute(attribute)
            return {"success": True, "selector": selector, "attribute": attribute, "value": value}
        except Exception as e:
            return {"success": False, "selector": selector, "attribute": attribute, "error": str(e)}

    async def get_all_links(
        self,
        skip_anchors: bool = False,
        skip_javascript: bool = False,
        filter_external_only: bool = False,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """Extract all links (hrefs) from the current page with optional filtering.

        Args:
            skip_anchors: Skip anchor links (starting with #)
            skip_javascript: Skip javascript: links
            filter_external_only: Only return external links (containing http/https)
            max_results: Maximum number of links to return
        """
        try:
            elements = await self.page.query_selector_all("a")
            links: list[dict[str, str]] = []
            seen_hrefs: set[str] = set()
            for element in elements:
                href = await element.get_attribute("href")
                text = await element.text_content()

                if not href:
                    continue

                # Apply filters
                if skip_anchors and href.startswith("#"):
                    continue
                if skip_javascript and href.startswith("javascript:"):
                    continue
                if filter_external_only and not href.startswith(("http://", "https://")):
                    continue
                if href in seen_hrefs:
                    continue

                seen_hrefs.add(href)
                links.append({"href": href, "text": (text or "").strip()})

            links.sort(key=_link_priority, reverse=True)
            total_count = len(links)
            if max_results is not None:
                links = links[:max_results]

            return {
                "success": True,
                "links": links,
                "count": len(links),
                "total_count": total_count,
            }
        except Exception as e:
            return {"success": False, "links": [], "count": 0, "error": str(e)}

    async def open_local_file(self, file_path: str) -> dict[str, Any]:
        """Open a local file (image, PDF, etc.) in the browser for viewing.

        Uses file:// protocol to load local files.
        """
        import os

        try:
            abs_path = os.path.abspath(file_path)
            if not os.path.exists(abs_path):
                return {"success": False, "error": f"File not found: {abs_path}"}
            file_url = f"file://{abs_path}"
            await self.page.goto(file_url, wait_until="load")
            return {"success": True, "url": file_url, "file_path": abs_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def refresh(self) -> dict[str, Any]:
        """Refresh the current page."""
        try:
            await self.page.reload(wait_until="load")
            return {"success": True, "url": self.page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def scroll_to_element(self, selector: str, timeout: int | None = None) -> dict[str, Any]:
        """Scroll an element into view."""
        try:
            # query_selector doesn't accept timeout, use wait_for_selector instead
            if timeout:
                element = await self.page.wait_for_selector(selector, timeout=timeout)
            else:
                element = await self.page.query_selector(selector)
            if element is None:
                return {"success": False, "selector": selector, "error": "Element not found"}
            await element.scroll_into_view_if_needed()
            return {"success": True, "selector": selector}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    async def get_search_results(self, max_results: int = 10) -> dict[str, Any]:
        """Extract search results from major search engines (Google, Bing, DuckDuckGo).

        Returns structured search results with title, link, and snippet.
        Engine-specific parsing lives in ``webagent.browser.search_parsers``.
        """
        try:
            engine = detect_search_engine(self.page.url)
            parser = SEARCH_PARSERS.get(engine) if engine else None
            if parser is None:
                # Unknown search engine, fall back to generic link extraction
                return await self.get_all_links(
                    skip_anchors=True,
                    skip_javascript=True,
                    filter_external_only=True,
                    max_results=max_results,
                )

            results = await parser(self.page, max_results)
            query_params = parse_qs(urlparse(self.page.url).query)
            query = next(
                (
                    values[0]
                    for key in ("q", "p", "query")
                    if (values := query_params.get(key)) and values[0]
                ),
                "",
            )
            return {
                "success": True,
                "engine": engine,
                "query": query,
                "results": results,
                "count": len(results),
            }

        except Exception as e:
            return {"success": False, "results": [], "count": 0, "error": str(e)}

    async def check_captcha(self) -> dict[str, Any]:
        """Check if current page has a captcha challenge.

        Returns:
            Dictionary with detection results:
                - detected (bool): Whether captcha was detected
                - type (str): Type of captcha (recaptcha, hcaptcha, etc.)
                - confidence (float): Detection confidence (0.0-1.0)
                - reason (str): Human-readable explanation
                - selectors (list[str]): Matching CSS selectors
        """
        from webagent.browser.captcha_detector import CaptchaDetector

        detector = CaptchaDetector()
        return await detector.detect_captcha(self.page)

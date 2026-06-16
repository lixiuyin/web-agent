"""Playwright browser controller (migrated from root browser_controller.py)."""

from __future__ import annotations

import asyncio
import base64
import random
from io import BytesIO
from typing import Any, Literal

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

from webagent.browser.stealth import (
    ENHANCED_STEALTH_SCRIPT,
    get_stealth_args,
    get_stealth_user_agent,
)


class BrowserController:
    """Async browser controller using Playwright Chromium with anti-detection."""

    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        default_timeout: int = 30000,
        slow_mo: int = 0,
        user_data_dir: str = "./browser_profile",
        stealth_mode: bool = True,
    ) -> None:
        # Auto-detect headless mode: force headless if no DISPLAY is set (Docker/env without X)
        import os

        if not headless and not os.environ.get("DISPLAY"):
            headless = True
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.default_timeout = default_timeout
        self.slow_mo = slow_mo if slow_mo > 0 else random.randint(50, 150)
        self.user_data_dir = user_data_dir
        self.stealth_mode = stealth_mode

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None

    async def __aenter__(self) -> BrowserController:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def start(self) -> None:
        if self._playwright is not None:
            raise RuntimeError("Browser already started; call close() before starting again")

        # Remove stale Chromium lock file left by previous crash
        import os

        for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = os.path.join(self.user_data_dir, lock_name)
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                pass
            except Exception:
                pass

        self._playwright = await async_playwright().start()

        # Use stealth mode or fallback to original
        if self.stealth_mode:
            user_agent = get_stealth_user_agent()
            args = get_stealth_args(headless=self.headless)
            stealth_script = ENHANCED_STEALTH_SCRIPT
        else:
            # Original behavior for backward compatibility
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
            args = [
                "--disable-blink-features=AutomationControlled",
                "--exclude-switches=enable-automation",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process,VizDisplayCompositor",
                "--window-size=1920,1080",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--metrics-recording-only",
                "--mute-audio",
                "--no-first-run",
                "--safebrowsing-disable-auto-update",
                "--disable-setuid-sandbox",
                "--disable-client-side-phishing-detection",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-hang-monitor",
                "--disable-domain-reliability",
                "--disable-component-update",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--disable-features=ImprovedCookieControls,LazyFrameLoading,GlobalMediaControls,DestroyProfileOnBrowserClose",
                "--enable-features=NetworkService,NetworkServiceInProcess",
                "--user-agent=" + user_agent,
            ]
            stealth_script = self._get_stealth_script()

        # Launch with anti-detection settings
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=args,
            user_agent=user_agent,
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["geolocation", "notifications"],
            color_scheme="light",
            device_scale_factor=1.0,
            ignore_https_errors=True,
            accept_downloads=False,
            proxy=None,
        )

        # Get CDP session for advanced stealth
        try:
            self._cdp = await self._context.new_cdp_session(self._context.pages[0])
            await self._cdp.send("Page.enable")
            await self._cdp.send("Runtime.enable")
        except Exception:
            self._cdp = None

        # Inject stealth script
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

        # Add random delay to appear more human
        await asyncio.sleep(random.uniform(0.5, 1.5))

    def _get_stealth_script(self) -> str:
        """Generate comprehensive stealth script to hide automation."""
        return """
        (() => {
            'use strict';

            // Generate random values for fingerprint variation
            const randomInt = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;

            // 1. Hide webdriver property (most important)
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
                configurable: true
            });

            // 2. Mock plugins with realistic data
            const pluginData = [
                { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer', length: 1 },
                { name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', length: 1 },
                { name: 'Native Client', description: '', filename: 'internal-nacl-plugin', length: 1 }
            ];

            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = Array.from(pluginData);
                    plugins.item = (i) => plugins[i];
                    plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
                    Object.defineProperty(plugins, 'length', { get: () => pluginData.length });
                    return plugins;
                },
                configurable: true
            });

            // 3. Mock languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
                configurable: true
            });

            // 4. Mock platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
                configurable: true
            });

            // 5. Mock hardware concurrency (random realistic value)
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => randomInt(4, 16),
                configurable: true
            });

            // 6. Mock device memory
            Object.defineProperty(navigator, 'deviceMemory', {
                get: () => 8,
                configurable: true
            });

            // 7. Mock max touch points
            Object.defineProperty(navigator, 'maxTouchPoints', {
                get: () => 0,
                configurable: true
            });

            // 8. Mock vendor
            Object.defineProperty(navigator, 'vendor', {
                get: () => 'Google Inc.',
                configurable: true
            });

            // 9. Mock product
            Object.defineProperty(navigator, 'product', {
                get: () => 'Gecko',
                configurable: true
            });

            // 10. Mock product sub
            Object.defineProperty(navigator, 'productSub', {
                get: () => '20030107',
                configurable: true
            });

            // 11. Mock vendor sub
            Object.defineProperty(navigator, 'vendorSub', {
                get: () => '',
                configurable: true
            });

            // 12. Mock app version
            Object.defineProperty(navigator, 'appVersion', {
                get: () => '5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                configurable: true
            });

            // 13. Mock app name
            Object.defineProperty(navigator, 'appName', {
                get: () => 'Netscape',
                configurable: true
            });

            // 14. Mock chrome object (CRITICAL for Google detection)
            window.chrome = {
                runtime: {
                    id: 'chrome-runtime-id',
                    onMessage: { addListener: () => {}, removeListener: () => {} },
                    sendMessage: () => {},
                    connect: () => ({})
                },
                app: {
                    isInstalled: false
                },
                loadTimes: function() {
                    return {
                        requestTime: Date.now() / 1000,
                        startLoadTime: Date.now() / 1000 - 0.2,
                        commitLoadTime: Date.now() / 1000 - 0.1,
                        finishDocumentLoadTime: Date.now() / 1000 - 0.05,
                        finishLoadTime: Date.now() / 1000 - 0.02,
                        firstPaintTime: Date.now() / 1000 - 0.03,
                        firstPaintAfterLoadTime: 0,
                        navigationType: 'Other'
                    };
                },
                csi: function() {
                    return {
                        startE: Date.now(),
                        onloadT: Date.now(),
                        pageT: Date.now()
                    };
                }
            };

            // 15. Mock permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: 'default' }) :
                    originalQuery(parameters)
            );

            // 16. Delete automation indicators
            delete navigator.__proto__.webdriver;
            if (window.chrome?.runtime) {
                window.chrome.runtime.id = 'chrome-runtime-id';
            }

            // 17. Mock connection info
            Object.defineProperty(navigator, 'connection', {
                get: () => ({
                    effectiveType: '4g',
                    rtt: randomInt(50, 150),
                    downlink: randomInt(5, 10),
                    saveData: false,
                    type: 'wifi'
                }),
                configurable: true
            });

            // 18. Mock screen properties with realistic values
            const screenWidth = randomInt(1920, 2560);
            const screenHeight = randomInt(1080, 1440);
            Object.defineProperty(screen, 'availHeight', { get: () => screenHeight - 40, configurable: true });
            Object.defineProperty(screen, 'availWidth', { get: () => screenWidth, configurable: true });
            Object.defineProperty(screen, 'height', { get: () => screenHeight, configurable: true });
            Object.defineProperty(screen, 'width', { get: () => screenWidth, configurable: true });
            Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
            Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });

            // 19. Mock window dimensions
            Object.defineProperty(window, 'outerWidth', { get: () => screenWidth, configurable: true });
            Object.defineProperty(window, 'outerHeight', { get: () => screenHeight, configurable: true });
            Object.defineProperty(window, 'innerWidth', { get: () => screenWidth, configurable: true });
            Object.defineProperty(window, 'innerHeight', { get: () => screenHeight - 40, configurable: true });

            // 20. Override WebGL
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                if (parameter === 37447) return 'Intel';
                if (parameter === 34473) return 24;
                return getParameter.call(this, parameter);
            };

            // 21. Mock WebGL2
            if (window.WebGL2RenderingContext) {
                const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                WebGL2RenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter2.call(this, parameter);
                };
            }

            // 22. Mock AudioContext
            if (window.AudioContext) {
                const audioContext = new AudioContext();
                const originalCreateAnalyser = audioContext.createAnalyser;
                AudioContext.prototype.createAnalyser = function() {
                    const analyser = originalCreateAnalyser.call(this);
                    analyser.fftSize = 2048;
                    return analyser;
                };
            }

            // 23. Mock Canvas with noise (applied to a clone, not the live canvas)
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                if (type === 'image/png') {
                    try {
                        const context = this.getContext('2d');
                        if (context) {
                            const clone = document.createElement('canvas');
                            clone.width = this.width;
                            clone.height = this.height;
                            const cloneCtx = clone.getContext('2d');
                            cloneCtx.drawImage(this, 0, 0);
                            const imageData = cloneCtx.getImageData(0, 0, clone.width, clone.height);
                            for (let i = 0; i < imageData.data.length; i += 4) {
                                imageData.data[i] = imageData.data[i] + (Math.random() > 0.5 ? 1 : 0);
                            }
                            cloneCtx.putImageData(imageData, 0, 0);
                            return originalToDataURL.call(clone, type);
                        }
                    } catch(e) {}
                }
                return originalToDataURL.apply(this, arguments);
            };

            // 24. Mock timezone
            const originalGetTimezoneOffset = Date.prototype.getTimezoneOffset;
            Date.prototype.getTimezoneOffset = function() {
                return 300;
            };
            const originalToString = Date.prototype.toString;
            Date.prototype.toString = function() {
                return originalToString.call(this).replace(/\\(.*\\)/, '(Eastern Standard Time)');
            };

            // 25. Mock Notification
            Object.defineProperty(Notification, 'permission', {
                get: () => 'default',
                configurable: true
            });

            // 26. Mock doNotTrack
            Object.defineProperty(navigator, 'doNotTrack', {
                get: () => null,
                configurable: true
            });

            // 27. Hide Playwright attributes
            try { delete window.__playwright; } catch(e) {}
            try { delete window.__PW_inspect; } catch(e) {}
            try { delete window._playwright; } catch(e) {}
            try { delete window.playwright; } catch(e) {}

            // 28. Mock Shadow DOM (v0)
            if (!Element.prototype.createShadowRoot) {
                Element.prototype.createShadowRoot = function() {
                    return this.attachShadow({mode: 'open'});
                };
            }

            // 29. Mock SpeechSynthesis
            if (window.speechSynthesis) {
                window.speechSynthesis.getVoices = function() {
                    return [
                        { name: 'Google US English', lang: 'en-US', default: true }
                    ];
                };
            }

            // 30. Override toString methods to look native
            const nativeToString = Function.prototype.toString;
            Function.prototype.toString = function() {
                if (this === HTMLCanvasElement.prototype.toDataURL) {
                    return 'function toDataURL() { [native code] }';
                }
                return nativeToString.call(this);
            };
        })();
        """

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
                await self._context.close()
            except Exception:
                pass
            self._context = None

        self._browser = None  # already gone after context.close()

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started; call start() first")
        return self._page

    async def goto(
        self,
        url: str,
        wait_until: Literal["load", "domcontentloaded", "networkidle", "commit"] = "load",
        timeout: int | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self.page.goto(
                url, wait_until=wait_until, timeout=timeout or self.default_timeout
            )

            # Add human-like behavior after navigation in stealth mode
            if self.stealth_mode and response and response.ok:
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
            return {"success": False, "url": url, "title": None, "error": str(e)}

    async def click(self, selector: str, timeout: int | None = None, force: bool = False) -> dict:
        try:
            await self.page.click(selector, timeout=timeout or self.default_timeout, force=force)
            return {"success": True, "selector": selector}
        except PlaywrightTimeout:
            return {"success": False, "selector": selector, "error": f"Not found: {selector}"}
        except Exception as e:
            return {"success": False, "selector": selector, "error": str(e)}

    async def click_link_by_text(
        self,
        text: str,
        timeout: int | None = None,
        fuzzy: bool = True,
    ) -> dict:
        """Click a link by searching for matching text.

        This is more flexible than exact text matching and works well for
        search results where the visible text might differ from snippets.

        Args:
            text: The text to search for in the link
            timeout: Maximum time to wait
            fuzzy: If True, tries partial matching when exact match fails

        Returns:
            Dict with success status and found element info
        """
        timeout = timeout or self.default_timeout

        # Strategy 1: Try exact text match first (fastest)
        try:
            selector = f'text="{text}"'
            await self.page.click(selector, timeout=5000, force=False)
            return {"success": True, "selector": selector, "method": "exact"}
        except Exception:
            pass

        if not fuzzy:
            return {"success": False, "error": f"No link found with text: {text}"}

        # Strategy 2: Try Playwright's get_by_text with exact=False
        try:
            element = self.page.get_by_text(text, exact=False).first
            await element.click(timeout=5000)
            return {
                "success": True,
                "selector": f"get_by_text({text}, exact=False)",
                "method": "fuzzy",
            }
        except Exception:
            pass

        # Strategy 3: Find all links and search for text match
        try:
            # Extract key words from the search text (skip common words)
            words_to_search = [
                w
                for w in text.split()
                if len(w) > 3
                and w.lower()
                not in {
                    "the",
                    "and",
                    "for",
                    "are",
                    "but",
                    "not",
                    "you",
                    "all",
                    "can",
                    "had",
                    "her",
                    "was",
                    "one",
                    "our",
                    "out",
                    "with",
                }
            ]

            # Find all links on the page
            links = await self.page.query_selector_all("a")
            for link in links:
                try:
                    link_text = await link.inner_text() or ""
                    # Check if multiple key words match
                    match_count = sum(
                        1 for word in words_to_search if word.lower() in link_text.lower()
                    )
                    if match_count >= 2:  # At least 2 words should match
                        await link.click(timeout=5000)
                        return {
                            "success": True,
                            "selector": f"link_by_text: {text}",
                            "found_text": link_text.strip()[:100],
                            "method": "keyword_match",
                        }
                except Exception:
                    continue
        except Exception:
            pass

        # Strategy 4: Try URL-based matching for common patterns
        try:
            # Look for arXiv IDs, DOIs, etc.
            import re

            arxiv_match = re.search(r"\d{4}\.\d+", text)
            if arxiv_match:
                arxiv_id = arxiv_match.group(0)
                links = await self.page.query_selector_all("a")
                for link in links:
                    try:
                        href = await link.get_attribute("href") or ""
                        if arxiv_id in href:
                            await link.click(timeout=5000)
                            return {
                                "success": True,
                                "selector": f"link_by_arxiv_id: {arxiv_id}",
                                "found_href": href[:100],
                                "method": "url_match",
                            }
                    except Exception:
                        continue
        except Exception:
            pass

        # Strategy 5: Fallback for PDF links - search for links containing "pdf" in URL
        if fuzzy and any(term in text.lower() for term in ["pdf", "view", "download"]):
            try:
                links = await self.page.query_selector_all("a")
                for link in links:
                    try:
                        href = await link.get_attribute("href") or ""
                        if "pdf" in href.lower():
                            await link.click(timeout=5000)
                            return {
                                "success": True,
                                "selector": "link_by_pdf_url",
                                "found_href": href[:100],
                                "method": "pdf_url_fallback",
                            }
                    except Exception:
                        continue
            except Exception:
                pass

        return {
            "success": False,
            "error": f"No link found matching: {text}",
            "tried_methods": ["exact", "fuzzy", "keyword_match", "url_match"],
        }

    async def type_text(
        self,
        selector: str,
        text: str,
        delay: int = 50,
        clear_first: bool = True,
        timeout: int | None = None,
    ) -> dict:
        try:
            await self.page.wait_for_selector(
                selector, state="visible", timeout=timeout or self.default_timeout
            )
            if clear_first:
                await self.page.fill(selector, "", timeout=timeout or self.default_timeout)
            await self.page.type(
                selector, text, delay=delay, timeout=timeout or self.default_timeout
            )
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
    ) -> dict:
        try:
            if selector:
                await self.page.focus(selector, timeout=timeout or self.default_timeout)
            await self.page.keyboard.press(key)
            return {"success": True, "key": key, "selector": selector}
        except Exception as e:
            return {"success": False, "key": key, "selector": selector, "error": str(e)}

    async def wait(self, milliseconds: int) -> dict:
        await asyncio.sleep(milliseconds / 1000)
        return {"success": True, "waited_ms": milliseconds}

    async def screenshot(
        self,
        full_page: bool = False,
        quality: int = 80,
        return_format: Literal["pil", "base64", "bytes"] = "pil",
    ) -> dict:
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

    async def scroll(self, direction: Literal["up", "down"] = "down", amount: int = 500) -> dict:
        try:
            delta = amount if direction == "down" else -amount
            await self.page.mouse.wheel(0, delta)
            await asyncio.sleep(0.1)
            return {"success": True, "direction": direction, "amount": amount}
        except Exception as e:
            return {"success": False, "direction": direction, "amount": amount, "error": str(e)}

    async def get_element_text(self, selector: str, timeout: int | None = None) -> dict:
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
    ) -> dict:
        try:
            await self.page.wait_for_selector(
                selector, state=state, timeout=timeout or self.default_timeout
            )
            return {"success": True, "selector": selector, "state": state}
        except Exception as e:
            return {"success": False, "selector": selector, "state": state, "error": str(e)}

    async def hover(self, selector: str, timeout: int | None = None) -> dict:
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
    ) -> dict:
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
    ) -> dict:
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
    ) -> dict:
        """Extract all links (hrefs) from the current page with optional filtering.

        Args:
            skip_anchors: Skip anchor links (starting with #)
            skip_javascript: Skip javascript: links
            filter_external_only: Only return external links (containing http/https)
            max_results: Maximum number of links to return
        """
        try:
            elements = await self.page.query_selector_all("a")
            links = []
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
                if filter_external_only and not (
                    href.startswith("http://") or href.startswith("https://")
                ):
                    continue

                links.append({"href": href, "text": (text or "").strip()})

                if max_results and len(links) >= max_results:
                    break

            return {"success": True, "links": links, "count": len(links)}
        except Exception as e:
            return {"success": False, "links": [], "count": 0, "error": str(e)}

    async def open_local_file(self, file_path: str) -> dict:
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

    async def refresh(self) -> dict:
        """Refresh the current page."""
        try:
            await self.page.reload(wait_until="load")
            return {"success": True, "url": self.page.url}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def scroll_to_element(self, selector: str, timeout: int | None = None) -> dict:
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

    async def get_search_results(self, max_results: int = 10) -> dict:
        """Extract search results from major search engines (Google, Bing, DuckDuckGo).

        Returns structured search results with title, link, and snippet.
        """
        try:
            url = self.page.url.lower()
            results = []

            # Google search results
            if "google.com" in url or "google." in url:
                # Try classic organic result containers first; fall back to
                # data-hveid which now also matches navigation tabs.
                elements = await self.page.query_selector_all("div.g")
                if not elements:
                    elements = await self.page.query_selector_all("div[data-hveid]")
                for element in elements[: max_results * 2]:  # over-fetch to account for filtering
                    try:
                        link_el = await element.query_selector("a")
                        if not link_el:
                            continue

                        href = await link_el.get_attribute("href")
                        title = await link_el.text_content()

                        # Skip Google-internal navigation links (tabs, redirects)
                        if not href or not href.startswith("http"):
                            continue
                        if "google.com" in href:
                            continue

                        # Get snippet if available
                        snippet_el = await element.query_selector(
                            "[style*='-webkit-line-clamp'], .VwiC3b, .IsZvec"
                        )
                        snippet = await snippet_el.text_content() if snippet_el else ""

                        title_clean = (title or "").strip()
                        if title_clean:
                            results.append(
                                {
                                    "title": title_clean,
                                    "link": href,
                                    "snippet": (snippet or "").strip(),
                                }
                            )
                        if len(results) >= max_results:
                            break
                    except Exception:
                        continue

            # Bing search results
            elif "bing.com" in url:
                elements = await self.page.query_selector_all("li.b_algo")
                for element in elements[:max_results]:
                    try:
                        link_el = await element.query_selector("h2 a")
                        if not link_el:
                            continue

                        href = await link_el.get_attribute("href")
                        title = await link_el.text_content()

                        # Get snippet if available
                        snippet_el = await element.query_selector("p")
                        snippet = await snippet_el.text_content() if snippet_el else ""

                        if href and title:
                            results.append(
                                {
                                    "title": (title or "").strip(),
                                    "link": href,
                                    "snippet": (snippet or "").strip(),
                                }
                            )
                    except Exception:
                        continue

            # DuckDuckGo search results
            elif "duckduckgo.com" in url:
                # Try multiple selector patterns for DuckDuckGo
                # Pattern 1: Modern DuckDuckGo with article tags
                elements = await self.page.query_selector_all("article.result")
                if not elements:
                    # Pattern 2: Legacy web-result class
                    elements = await self.page.query_selector_all("div.web-result")
                if not elements:
                    # Pattern 3: Try finding all links in main content area
                    main_content = await self.page.query_selector(
                        "main#content__main, #links, .results"
                    )
                    if main_content:
                        elements = await main_content.query_selector_all("a")

                for element in elements[:max_results]:
                    try:
                        # Get the link element
                        link_el = element
                        if await link_el.evaluate("el => el.tagName") != "A":
                            link_el = await element.query_selector("a[href]")
                        if not link_el:
                            continue

                        href = await link_el.get_attribute("href")
                        if not href or href.startswith("/") or "duckduckgo.com" in href:
                            # Skip internal links
                            continue

                        title = (
                            await link_el.text_content()
                            or await link_el.get_attribute("title")
                            or ""
                        )
                        title = title.strip()

                        # Get snippet - look for nearby text
                        snippet = ""
                        try:
                            # Try to find description in parent or sibling
                            parent = await link_el.evaluate("el => el.parentElement")
                            if parent:
                                snippet_el = await element.query_selector(
                                    ".result__snippet, .snippet, p"
                                )
                                if snippet_el:
                                    snippet = (await snippet_el.text_content() or "").strip()
                        except Exception:
                            pass

                        if title and href and href.startswith("http"):
                            results.append(
                                {
                                    "title": title,
                                    "link": href,
                                    "snippet": snippet,
                                }
                            )
                    except Exception:
                        continue

            else:
                # Unknown search engine, fall back to generic link extraction
                return await self.get_all_links(
                    skip_anchors=True,
                    skip_javascript=True,
                    filter_external_only=True,
                    max_results=max_results,
                )

            return {"success": True, "results": results, "count": len(results)}

        except Exception as e:
            return {"success": False, "results": [], "count": 0, "error": str(e)}

    async def check_captcha(self) -> dict:
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

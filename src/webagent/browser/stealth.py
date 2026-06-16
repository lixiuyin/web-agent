"""Enhanced browser stealth mode inspired by browser-use.

This module provides advanced anti-detection techniques to avoid captchas
and bot detection when automating web browsers.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

# Chrome arguments optimized for anti-detection
CHROME_STEALTH_ARGS = [
    # Anti-automation flags
    "--disable-blink-features=AutomationControlled",
    "--exclude-switches=enable-automation",
    "--disable-infobars",
    # Disable features that reveal automation
    "--disable-features=AutomationControlled,VizDisplayCompositor",
    # Browser appearance
    "--disable-field-trial-config",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--no-default-browser-check",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-service-autorun",
    "--disable-sync",
    "--allow-legacy-extension-manifests",
    "--test-type=gpu",
    # Security relaxed (for testing)
    "--disable-web-security",
    "--ignore-certificate-errors",
    "--ignore-ssl-errors",
    "--ignore-certificate-errors-spki-list",
    # Extra stealth
    "--disable-focus-on-load",
    "--disable-window-activation",
    "--no-pings",
    "--ash-no-nudges",
    "--suppress-message-center-popups",
    "--disable-domain-reliability",
    "--noerrdialogs",
    "--disable-features=ImprovedCookieControls,LazyFrameLoading,GlobalMediaControls",
    # Performance
    "--disable-gpu",
    "--disable-dev-shm-usage",
    # Enable important features
    "--enable-features=NetworkService,NetworkServiceInProcess",
    # Hide automation indicators
    "--disable-blink-features=AutomationControlled",
]


# Enhanced stealth script (more comprehensive than original)
ENHANCED_STEALTH_SCRIPT = """
(() => {
    'use strict';

    // 1. Remove webdriver indicators (most important)
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Delete from prototype chain
    delete navigator.__proto__.webdriver;
    delete Object.getPrototypeOf(navigator).webdriver;

    // 3. Mock chrome object (CRITICAL for Google detection)
    if (!window.chrome) {
        window.chrome = {
            runtime: {
                id: 'chrome-runtime-id',
                onMessage: {
                    addListener: () => {},
                    removeListener: () => {}
                },
                sendMessage: () => {},
                connect: () => ({})
            },
            app: {
                isInstalled: false,
                InstallState: {
                    DISABLED: 2,
                    INSTALLED: 1,
                    NOT_INSTALLED: 0
                }
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
    }

    // 4. Override permissions query for notifications
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: 'default' }) :
            originalQuery(parameters)
    );

    // 5. Mock plugins with realistic data
    const pluginData = [
        {
            name: 'Chrome PDF Plugin',
            description: 'Portable Document Format',
            filename: 'internal-pdf-viewer',
            length: 1
        },
        {
            name: 'Chrome PDF Viewer',
            description: '',
            filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
            length: 1
        },
        {
            name: 'Native Client',
            description: '',
            filename: 'internal-nacl-plugin',
            length: 1
        }
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

    // 6. Mock languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });

    // 7. Mock platform
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
        configurable: true
    });

    // 8. Mock hardware properties
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => Math.max(4, Math.min(16, Math.floor(Math.random() * 8) + 4)),
        configurable: true
    });

    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true
    });

    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 0,
        configurable: true
    });

    // 9. Mock vendor
    Object.defineProperty(navigator, 'vendor', {
        get: () => 'Google Inc.',
        configurable: true
    });

    // 10. Mock product
    Object.defineProperty(navigator, 'product', {
        get: () => 'Gecko',
        configurable: true
    });

    // 11. Mock productSub
    Object.defineProperty(navigator, 'productSub', {
        get: () => '20030107',
        configurable: true
    });

    // 12. Mock vendorSub
    Object.defineProperty(navigator, 'vendorSub', {
        get: () => '',
        configurable: true
    });

    // 13. Remove Playwright indicators
    try { delete window.__playwright; } catch(e) {}
    try { delete window.__PW_inspect; } catch(e) {}
    try { delete window._playwright; } catch(e) {}
    try { delete window.playwright; } catch(e) {}

    // 14. Mock connection info
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: Math.floor(Math.random() * 100) + 50,
            downlink: Math.floor(Math.random() * 5) + 5,
            saveData: false,
            type: 'wifi'
        }),
        configurable: true
    });

    // 15. Mock screen properties with realistic variation
    const screenConfigs = [
        { w: 1920, h: 1080 },
        { w: 2560, h: 1440 },
        { w: 1680, h: 1050 },
        { w: 1440, h: 900 }
    ];
    const config = screenConfigs[Math.floor(Math.random() * screenConfigs.length)];

    Object.defineProperty(screen, 'availHeight', { get: () => config.h - 40, configurable: true });
    Object.defineProperty(screen, 'availWidth', { get: () => config.w, configurable: true });
    Object.defineProperty(screen, 'height', { get: () => config.h, configurable: true });
    Object.defineProperty(screen, 'width', { get: () => config.w, configurable: true });

    // 16. Override WebGL
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        if (parameter === 37447) return 'Intel';
        if (parameter === 34473) return 24;
        return getParameter.call(this, parameter);
    };

    // 17. Mock Canvas with minimal noise
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        if (type === 'image/png') {
            const context = this.getContext('2d');
            if (context) {
                try {
                    const imageData = context.getImageData(0, 0, this.width, this.height);
                    for (let i = 0; i < imageData.data.length; i += 4) {
                        imageData.data[i] = imageData.data[i] + (Math.random() > 0.5 ? 1 : 0);
                    }
                    context.putImageData(imageData, 0, 0);
                } catch(e) {}
            }
        }
        return originalToDataURL.apply(this, arguments);
    };

    // 18. Mock Notification
    Object.defineProperty(Notification, 'permission', {
        get: () => 'default',
        configurable: true
    });

    // 19. Mock doNotTrack
    Object.defineProperty(navigator, 'doNotTrack', {
        get: () => null,
        configurable: true
    });

    // 20. Override toString methods to look native
    const nativeToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === HTMLCanvasElement.prototype.toDataURL) {
            return 'function toDataURL() { [native code] }';
        }
        return nativeToString.call(this);
    };

    // 21. Mock Permission API
    if (window.PermissionStatus) {
        const originalState = Object.getOwnPropertyDescriptor(PermissionStatus.prototype, 'state');
        Object.defineProperty(PermissionStatus.prototype, 'state', {
            get: function() {
                const currentState = originalState.get.call(this);
                if (this.permissionName === 'notifications') {
                    return 'default';
                }
                return currentState;
            },
            configurable: true
        });
    }

    // 22. Extra: Hide CDP indicators
    try {
        const originalCall = Function.prototype.call;
        Function.prototype.call = function() {
            if (arguments.length > 0 && typeof arguments[0] === 'string' && arguments[0].includes('devtools')) {
                return undefined;
            }
            return originalCall.apply(this, arguments);
        };
    } catch(e) {}

    // 23: Mock outerHeight/outerWidth with variation
    Object.defineProperty(window, 'outerWidth', { get: () => config.w, configurable: true });
    Object.defineProperty(window, 'outerHeight', { get: () => config.h, configurable: true });

    // 24: Ensure Chrome runtime ID is set
    if (window.chrome && window.chrome.runtime && !window.chrome.runtime.id) {
        window.chrome.runtime.id = 'chrome-runtime-id';
    }

    console.log('[Stealth] Anti-detection script injected');
})();
"""


async def add_human_like_behavior(page: Any) -> None:
    """Add random human-like behaviors to the page.

    Args:
        page: Playwright page object
    """
    # Random mouse movements
    await page.mouse.move(
        random.randint(100, 500),
        random.randint(100, 500),
    )
    await asyncio.sleep(random.uniform(0.05, 0.2))

    # Random scroll
    await page.evaluate(f"window.scrollBy(0, {random.randint(50, 200)})")
    await asyncio.sleep(random.uniform(0.1, 0.5))


def get_stealth_user_agent() -> str:
    """Get a realistic user agent string.

    Returns:
        Realistic Chrome user agent
    """
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    ]
    return random.choice(user_agents)


def get_stealth_args(headless: bool = False) -> list[str]:
    """Get Chrome arguments for stealth mode.

    Args:
        headless: Whether to use headless mode

    Returns:
        List of Chrome arguments
    """
    args = CHROME_STEALTH_ARGS.copy()

    if headless:
        args.append("--headless=new")

    return args

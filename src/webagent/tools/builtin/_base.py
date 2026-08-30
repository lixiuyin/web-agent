"""Shared base class for tools that operate on the browser controller."""

from __future__ import annotations

from typing import Any


class BrowserToolBase:
    """Stores the injected browser controller and ignores unrelated tool kwargs.

    Tools are auto-discovered and constructed with a common ``**kwargs`` bundle
    (``browser``, ``artifacts_dir``, ``config``, ``planner``); this base keeps
    only the browser and swallows the rest so browser-only tools need no
    ``__init__`` of their own.
    """

    def __init__(self, browser: Any = None, **kw: Any) -> None:
        self.browser = browser

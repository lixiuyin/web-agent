"""Preserve partial capture evidence without claiming DOM/image consistency."""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import uuid4

from PIL import Image

from webagent.core.models import BrowserState


class InconsistentCapture(RuntimeError):
    def __init__(self, message: str, screenshot: bytes, url: str) -> None:
        super().__init__(message)
        self.screenshot = screenshot
        self.url = url


async def failed_observation(
    page: Any,
    attempts: list[dict[str, Any]],
    partial: InconsistentCapture | None,
    *,
    timeout_seconds: float = 5.0,
) -> BrowserState:
    image = None
    status = "failed"
    url = str(page.url)
    try:
        async with asyncio.timeout(timeout_seconds):
            raw = (
                partial.screenshot
                if partial
                else await page.screenshot(full_page=False, type="png")
            )
        image = Image.open(BytesIO(raw))
        image.load()
        status = "inconsistent" if partial else "partial"
        url = partial.url if partial else str(page.url)
    except Exception as exc:
        attempts.append({"phase": "fallback_screenshot", "error_type": type(exc).__name__})
    return BrowserState(
        observation_id=uuid4().hex,
        screenshot=image,
        dom_summary="(observation incomplete; DOM unavailable, not evidence of an empty/loading page)",
        url=url,
        title="",
        timestamp=datetime.now(UTC).isoformat(),
        observation_metadata={
            "status": status,
            "dom_status": "failed",
            "screenshot_status": "captured" if image else "failed",
            "pair_consistent": False,
            "capture_attempts": attempts,
            "screenshot_scope": "viewport",
            "dom_content_chars": 0,
        },
    )

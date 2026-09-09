"""Geometry and timing for auditable browser observations."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

_GEOMETRY_SCRIPT = """() => ({
    viewport: {width: window.innerWidth, height: window.innerHeight},
    scroll: {x: window.scrollX, y: window.scrollY},
    document: {width: document.documentElement.scrollWidth,
               height: document.documentElement.scrollHeight}
})"""


async def capture_geometry(page: Page) -> dict[str, Any]:
    """Use null for unknown coordinates rather than inventing a scroll origin."""
    fallback = {
        "viewport": page.viewport_size or {"width": 1280, "height": 720},
        "scroll": None,
        "document": None,
    }
    try:
        result = await page.evaluate(_GEOMETRY_SCRIPT)
        if isinstance(result, dict) and {"viewport", "scroll", "document"} <= result.keys():
            return result
    except Exception:
        pass
    return fallback


def observation_scope_text(meta: dict[str, Any]) -> str:
    """Explain the distinct coverage of the image and document projection."""
    return (
        "## Observation scope\n"
        f"Screenshot: {meta['screenshot_scope']}; viewport: {meta['viewport']}; "
        f"scroll: {meta['scroll']}.\n"
        f"Observation: {meta.get('observation_id', 'unknown')}. "
        "Viewport content and document supplement are separate. Semantic control labels "
        "are not necessarily painted text. Hit tests are sampled, not a proof of pixel visibility.\n\n"
    )

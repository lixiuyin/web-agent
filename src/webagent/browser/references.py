"""Resolve observation-bound nodes and execute ordinary browser actions on those nodes."""

from __future__ import annotations

from typing import Any, cast

from playwright.async_api import ElementHandle, Page

from webagent.browser.reference_selectors import is_observed_selector, reference_parts
from webagent.browser.rendered_script import CHECK_FRAME_REFERENCE_JS, RESOLVE_REFERENCE_JS
from webagent.core.models import ToolResult


async def resolve_reference(
    page: Page, params: dict[str, Any], *, require_viewport: bool
) -> ElementHandle:
    selector = params["selector"]
    observation_id, ref, css = reference_parts(selector)
    index = params.get("frame_index", 0)
    frame = page.frames[index]
    if not ref.startswith(f"f{index}:"):
        raise ValueError(
            "Reference frame mismatch; use frame_interact with the observed frame_index"
        )
    await _check_frame_ancestors(frame, observation_id, require_viewport)
    handle = await frame.evaluate_handle(
        RESOLVE_REFERENCE_JS,
        {
            "observationId": observation_id,
            "ref": ref,
            "selector": css,
            "requireViewport": require_viewport,
        },
    )
    element = handle.as_element()
    if element is None:
        await handle.dispose()
        raise ValueError("Observation reference no longer resolves to an element")
    return cast(ElementHandle, element)


async def _check_frame_ancestors(frame: Any, observation_id: str, require_viewport: bool) -> None:
    while frame.parent_frame is not None:
        handle = await frame.frame_element()
        try:
            if not await handle.evaluate(
                CHECK_FRAME_REFERENCE_JS,
                {"observationId": observation_id, "requireViewport": require_viewport},
            ):
                raise ValueError("Ancestor frame changed; re-observe before acting")
        finally:
            await handle.dispose()
        frame = frame.parent_frame


async def _click(browser: Any, target: ElementHandle) -> dict[str, Any]:
    pages = set(browser.page.context.pages)
    await target.click()
    adopt = getattr(browser, "_activate_new_page", None)
    return await adopt(pages) if callable(adopt) else {}


async def _type(target: ElementHandle, params: dict[str, Any]) -> dict[str, Any]:
    if params.get("action") == "type":
        await target.fill(params["text"])
        return {"typed": True}
    if params.get("clear_first", True):
        await target.fill("")
    await target.type(params["text"], delay=params.get("delay_ms", 50))
    return {"typed": True, "text": params["text"]}


async def _perform(
    browser: Any, target: ElementHandle, action: str, params: dict[str, Any]
) -> dict[str, Any]:
    if action == "click":
        return await _click(browser, target)
    if action == "type":
        return await _type(target, params)
    if action == "scroll_to_element":
        await target.scroll_into_view_if_needed()
        return {"reobserve_required": True}
    if action == "hover":
        await target.hover()
    elif action == "press":
        await target.press(params["key"])
        return {"key": params["key"]}
    elif action == "select_dropdown":
        options = {key: params[key] for key in ("value", "label", "index") if key in params}
        return {"option": await target.select_option(**options)}
    elif action == "extract_text":
        return {"text": await target.inner_text()}
    elif action == "get_attribute":
        return {
            "attribute": params["attribute"],
            "value": await target.get_attribute(params["attribute"]),
        }
    else:
        raise ValueError(f"Observation-bound references are not supported by {action}")
    return {}


async def execute_reference(browser: Any, name: str, params: dict[str, Any]) -> ToolResult | None:
    selector = params.get("selector")
    if not isinstance(selector, dict) or not is_observed_selector(selector):
        return None
    action = params.get("action", name) if name in {"frame_interact", "shadow_dom"} else name
    if params.get("force"):
        raise ValueError("force cannot bypass observation-bound target checks")
    target = await resolve_reference(
        browser.page,
        params,
        require_viewport=action not in {"scroll_to_element", "get_attribute", "extract_text"},
    )
    try:
        data = await _perform(browser, target, action, params)
        return ToolResult(
            success=True,
            tool_name=name,
            data={
                "selector": selector,
                "observation_id": reference_parts(selector)[0],
                **(
                    {"frame_index": params["frame_index"], "action": action}
                    if name == "frame_interact"
                    else {}
                ),
                **data,
            },
        )
    finally:
        await target.dispose()

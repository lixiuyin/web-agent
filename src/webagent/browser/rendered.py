"""Collect rendered text/controls across frames in top-level viewport coordinates."""

from __future__ import annotations

from typing import Any

from playwright.async_api import Frame, Page

from webagent.browser.rendered_script import (
    CHECK_PROJECTION_JS,
    FRAME_GEOMETRY_JS,
    RENDER_PROJECTION_JS,
)


def _intersection(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    x, y = max(a["x"], b["x"]), max(a["y"], b["y"])
    return {
        "x": x,
        "y": y,
        "width": max(0, min(a["x"] + a["width"], b["x"] + b["width"]) - x),
        "height": max(0, min(a["y"] + a["height"], b["y"] + b["height"]) - y),
    }


def _transform(rect: dict[str, float], mapping: dict[str, Any]) -> dict[str, float]:
    return {
        "x": mapping["x"] + rect["x"] * mapping["sx"],
        "y": mapping["y"] + rect["y"] * mapping["sy"],
        "width": rect["width"] * mapping["sx"],
        "height": rect["height"] * mapping["sy"],
    }


async def _frame_mapping(frame: Frame, parent: dict[str, Any]) -> dict[str, Any]:
    handle = await frame.frame_element()
    try:
        geometry = await handle.evaluate(FRAME_GEOMETRY_JS)
    finally:
        await handle.dispose()
    content = _transform(geometry["content"], parent)
    clip = _intersection(_transform(geometry["clip"], parent), parent["clip"])
    if not geometry["supported"] or geometry["receives_events"] is not True:
        clip = {"x": 0, "y": 0, "width": 0, "height": 0}
    return {
        "x": content["x"],
        "y": content["y"],
        "sx": geometry["sx"] * parent["sx"],
        "sy": geometry["sy"] * parent["sy"],
        "clip": clip,
        "viewport_status": "mapped" if geometry["supported"] else "unsupported_transform",
    }


def _local_clip(mapping: dict[str, Any]) -> dict[str, float]:
    clip = mapping["clip"]
    return {
        "x": (clip["x"] - mapping["x"]) / mapping["sx"],
        "y": (clip["y"] - mapping["y"]) / mapping["sy"],
        "width": clip["width"] / mapping["sx"],
        "height": clip["height"] / mapping["sy"],
    }


async def capture_rendered(
    page: Page, observation_id: str, viewport: dict[str, Any], *, max_nodes: int, max_chars: int
) -> dict[str, Any] | None:
    """Unknown frame geometry never becomes purported viewport evidence."""
    frames = getattr(page, "frames", None)
    if not isinstance(frames, list) or not frames:
        return None
    mappings: dict[Frame, dict[str, Any]] = {}
    result: dict[str, Any] = {"controls": [], "texts": [], "frames": [], "unavailable_frames": []}
    for index, frame in enumerate(frames):
        try:
            mapping = (
                await _frame_mapping(frame, mappings[frame.parent_frame])
                if frame.parent_frame is not None
                else {"x": 0, "y": 0, "sx": 1, "sy": 1, "clip": {"x": 0, "y": 0, **viewport}}
            )
            mappings[frame] = mapping
            projection = await frame.evaluate(
                RENDER_PROJECTION_JS,
                {
                    "observationId": observation_id,
                    "frameIndex": index,
                    "maxNodes": max_nodes,
                    "maxChars": max_chars,
                    "clip": _local_clip(mapping),
                },
            )
            _merge_projection(result, projection, mapping)
        except Exception as exc:
            result["unavailable_frames"].append({"frame_index": index, "error": type(exc).__name__})
    return result if result["frames"] else None


def _merge_projection(
    result: dict[str, Any], projection: dict[str, Any], mapping: dict[str, Any]
) -> None:
    for control in projection["controls"]:
        result["controls"].append(
            {
                **control,
                "frame_viewport_bbox": control["viewport_bbox"],
                "viewport_bbox": _transform(control["viewport_bbox"], mapping),
                "visible_bbox": _transform(control["visible_bbox"], mapping),
            }
        )
    result["texts"].extend(projection["texts"])
    result["frames"].append(
        {
            **{key: projection[key] for key in ("frame_index", "document_id", "token", "omitted")},
            "viewport_status": mapping.get("viewport_status", "mapped"),
        }
    )


async def assert_rendered_current(
    page: Page, observation_id: str, projection: dict[str, Any]
) -> None:
    for item in projection["frames"]:
        frame = page.frames[item["frame_index"]]
        if not await frame.evaluate(CHECK_PROJECTION_JS, observation_id):
            raise RuntimeError("page content or layout changed during observation; retry capture")

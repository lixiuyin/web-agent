"""Dependency-light translation between BrowserGym and webagent planner contracts."""

from __future__ import annotations

import base64
import hashlib
import importlib
import io
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from PIL import Image, ImageDraw

from webagent.core.models import BrowserState, ToolCall
from webagent.tools.registry import ToolSpec

BACKEND_VARIABLES: dict[str, tuple[str, ...]] = {
    "webarena_verified": (
        "WA_SHOPPING",
        "WA_SHOPPING_ADMIN",
        "WA_REDDIT",
        "WA_GITLAB",
        "WA_WIKIPEDIA",
        "WA_MAP",
        "WA_HOMEPAGE",
    ),
    "visualwebarena": (
        "VWA_CLASSIFIEDS",
        "VWA_CLASSIFIEDS_RESET_TOKEN",
        "VWA_SHOPPING",
        "VWA_REDDIT",
        "VWA_WIKIPEDIA",
        "VWA_HOMEPAGE",
    ),
}


def backend_configuration_sha256(benchmark: str) -> str:
    """Fail closed on missing URLs and hash values without disclosing private endpoints."""
    try:
        variables = BACKEND_VARIABLES[benchmark]
    except KeyError as exc:
        raise ValueError(f"unsupported BrowserGym benchmark: {benchmark}") from exc
    missing = [name for name in variables if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"{benchmark} backend is not configured; missing: {', '.join(missing)}")
    reset_variable = "WA_FULL_RESET" if benchmark == "webarena_verified" else "VWA_FULL_RESET"
    payload = {name: os.environ.get(name, "") for name in (*variables, reset_variable)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def require_evaluator_device(benchmark: str, evaluator_device: str) -> None:
    """Validate the optional VisualWebArena CUDA evaluator only when requested."""
    if benchmark != "visualwebarena" or evaluator_device != "cuda":
        return
    try:
        torch: Any = importlib.import_module("torch")
    except ModuleNotFoundError as exc:
        if exc.name != "torch":
            raise
        raise RuntimeError(
            "VisualWebArena CUDA evaluation requires torch in the BrowserGym environment"
        ) from exc

    if not torch.cuda.is_available():
        raise RuntimeError(
            "VisualWebArena CUDA evaluation was requested, but torch.cuda.is_available() is false"
        )


def browsergym_tool_specs(descriptors: Sequence[dict[str, Any]]) -> list[ToolSpec]:
    """Convert BrowserGym OpenAI descriptors into provider-neutral tool specs."""
    specs: list[ToolSpec] = []
    for descriptor in descriptors:
        name = descriptor.get("name")
        description = descriptor.get("description", "")
        parameters = descriptor.get("parameters")
        if not isinstance(name, str) or not name:
            raise ValueError("BrowserGym action descriptor has no valid name")
        if not isinstance(description, str) or not isinstance(parameters, dict):
            raise ValueError(f"BrowserGym action descriptor is invalid: {name}")
        specs.append(ToolSpec(name=name, description=description, parameters=parameters))
    return specs


def render_browsergym_action(call: ToolCall, specs: Sequence[ToolSpec]) -> str:
    """Render one structured tool call as BrowserGym high-level action syntax."""
    spec = next((item for item in specs if item.name == call.tool_name), None)
    if spec is None:
        raise ValueError(f"planner selected an unavailable BrowserGym action: {call.tool_name}")
    properties = spec.parameters.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"BrowserGym action schema has invalid properties: {call.tool_name}")
    required = spec.parameters.get("required", [])
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        raise ValueError(f"BrowserGym action schema has invalid required fields: {call.tool_name}")
    missing = sorted(set(required) - set(call.parameters))
    if missing:
        raise ValueError(f"BrowserGym action is missing required parameters: {missing}")
    unknown = sorted(set(call.parameters) - set(properties))
    if unknown:
        raise ValueError(f"BrowserGym action has unknown parameters: {unknown}")
    arguments = ", ".join(
        f"{name}={call.parameters[name]!r}" for name in properties if name in call.parameters
    )
    return f"{call.tool_name}({arguments})"


def goal_text(goal_object: object) -> str:
    """Return all textual BrowserGym goal messages without leaking image payloads."""
    if not isinstance(goal_object, Sequence) or isinstance(goal_object, (str, bytes)):
        return str(goal_object or "").strip()
    values = [
        str(item.get("text", "")).strip()
        for item in goal_object
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return "\n".join(value for value in values if value)


def goal_images(goal_object: object) -> list[Image.Image]:
    """Decode BrowserGym's data-URI goal images for VisualWebArena planning."""
    if not isinstance(goal_object, Sequence) or isinstance(goal_object, (str, bytes)):
        return []
    images: list[Image.Image] = []
    for item in goal_object:
        if not isinstance(item, dict) or item.get("type") != "image_url":
            continue
        source = item.get("image_url")
        if isinstance(source, dict):
            source = source.get("url")
        if not isinstance(source, str) or not source.startswith("data:image/"):
            continue
        try:
            encoded = source.split(",", 1)[1]
            image = Image.open(io.BytesIO(base64.b64decode(encoded)))
            images.append(image.convert("RGB"))
        except (IndexError, OSError, ValueError):
            continue
    return images


def planner_screenshot(page_screenshot: object, references: Sequence[Image.Image]) -> Image.Image:
    """Place VisualWebArena references above the current page in one labeled image."""
    page = (
        page_screenshot.convert("RGB")
        if isinstance(page_screenshot, Image.Image)
        else Image.fromarray(page_screenshot).convert("RGB")  # type: ignore[arg-type]
    )
    if not references:
        return page
    panels = [*references, page]
    width = max(panel.width for panel in panels)
    label_height = 28
    height = sum(panel.height + label_height for panel in panels)
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    top = 0
    for index, panel in enumerate(panels):
        label = f"REFERENCE IMAGE {index + 1}" if index < len(references) else "CURRENT PAGE"
        draw.rectangle((0, top, width, top + label_height), fill=(30, 30, 30))
        draw.text((8, top + 7), label, fill="white")
        top += label_height
        canvas.paste(panel, (0, top))
        top += panel.height
    return canvas


def browser_state_from_observation(obs: dict[str, Any]) -> BrowserState:
    """Build the planner observation while preserving BrowserGym element IDs."""
    pages = [
        f"Tab {index}: {title} | {url}"
        for index, (url, title) in enumerate(
            zip(obs.get("open_pages_urls", []), obs.get("open_pages_titles", []), strict=False)
        )
    ]
    references = goal_images(obs.get("goal_object"))
    screenshot = planner_screenshot(obs["screenshot"], references)
    visual_notice = (
        "\nComposite screenshot contains REFERENCE IMAGE panels above CURRENT PAGE."
        if references
        else ""
    )
    dom = str(obs.get("axtree_txt") or obs.get("pruned_html") or "")
    summary = (
        "OPEN TABS:\n"
        + "\n".join(pages)
        + f"\nLAST ACTION: {obs.get('last_action', '')}"
        + f"\nLAST ACTION ERROR: {obs.get('last_action_error', '')}"
        + visual_notice
        + "\nACCESSIBILITY TREE:\n"
        + dom
    )
    titles = obs.get("open_pages_titles", [])
    active = int(obs.get("active_page_index", 0))
    title = str(titles[active]) if 0 <= active < len(titles) else ""
    return BrowserState(
        screenshot=screenshot,
        dom_summary=summary,
        url=str(obs.get("url", "")),
        title=title,
        timestamp=datetime.now(UTC).isoformat(),
    )


def task_id_from_name(task_name: str) -> int:
    """Extract the canonical integer ID from supported BrowserGym task names."""
    parts = task_name.split(".")
    if len(parts) == 2 and parts[0] == "visualwebarena":
        return int(parts[1])
    if len(parts) == 4 and parts[0] == "webarena_verified":
        return int(parts[2])
    raise ValueError(f"unsupported BrowserGym task name: {task_name}")


def task_set_sha256(
    task_names: Sequence[str],
    task_seeds: Sequence[int] | None = None,
) -> str:
    """Hash the exact ordered external task set and canonical BrowserGym seeds."""
    if task_seeds is None:
        payload: object = list(task_names)
    else:
        if len(task_names) != len(task_seeds):
            raise ValueError("task names and seeds must have equal length")
        payload = [
            {"task_name": name, "task_seed": seed}
            for name, seed in zip(task_names, task_seeds, strict=True)
        ]
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "BACKEND_VARIABLES",
    "backend_configuration_sha256",
    "browser_state_from_observation",
    "browsergym_tool_specs",
    "goal_images",
    "goal_text",
    "planner_screenshot",
    "render_browsergym_action",
    "require_evaluator_device",
    "task_id_from_name",
    "task_set_sha256",
]

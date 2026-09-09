"""Persist paired browser evidence without putting page content in checkpoints."""

from __future__ import annotations

import hashlib
import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from PIL import Image

from webagent.core.models import BrowserState
from webagent.evaluation.artifacts import RunLayout


def _write_image(image: Image.Image | None, path: Path, root: Path) -> dict[str, Any] | None:
    if image is None:
        return None
    encoded = BytesIO()
    image.save(encoded, format="PNG")
    payload = encoded.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    blob = root / "blobs" / f"{digest}.png"
    blob.parent.mkdir(parents=True, exist_ok=True)
    if not blob.exists():
        blob.write_bytes(payload)
    try:
        os.link(blob, path)
    except OSError:
        path.write_bytes(payload)
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": digest,
        "blob_path": blob.relative_to(root).as_posix(),
        "width": image.width,
        "height": image.height,
    }


def _paired_directory(layout: RunLayout, step: int, pre_path: str | None) -> Path:
    directory = layout.observations_dir / f"step_{step:03d}"
    if pre_path is None:
        return directory
    pre = layout.root / pre_path
    if pre.name != "pre.json" or not pre.resolve().is_relative_to(directory.resolve()):
        raise ValueError("pre-observation path is outside the step evidence directory")
    return pre.parent


def save_observation(
    state: BrowserState,
    layout: RunLayout,
    step: int,
    phase: Literal["pre", "post"],
) -> str:
    """Save exact DOM summary, selected controls, geometry and lossless images."""
    directory = _paired_directory(layout, step, state.observation_path if phase == "post" else None)
    if phase == "pre" and directory.exists():
        # Safe replay can replan an interrupted step. Old planner attempts must
        # continue to reference the old pixels and DOM rather than an overwrite.
        directory = directory / f"capture_{uuid4().hex}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{phase}.json"
    if path.exists():
        raise FileExistsError(f"observation already exists: {path}")
    payload = {
        "schema_version": 1,
        "step_number": step,
        "phase": phase,
        "url": state.url,
        "title": state.title,
        "timestamp": state.timestamp,
        "metadata": state.observation_metadata,
        "dom_summary": state.dom_summary,
        "observation_id": state.observation_id,
        "viewport_context": state.viewport_context,
        "document_context": state.document_context,
        "viewport_blocks": state.viewport_blocks,
        "document_blocks": state.document_blocks,
        "dom_sha256": hashlib.sha256(state.dom_summary.encode("utf-8")).hexdigest(),
        "elements": state.elements,
        "screenshot": _write_image(state.screenshot, directory / f"{phase}.png", layout.root),
        "full_page_screenshot": _write_image(
            state.full_page_screenshot, directory / f"{phase}.full.png", layout.root
        ),
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return path.relative_to(layout.root).as_posix()


def observation_references(
    layout: RunLayout, step: int, pre_path: str | None = None
) -> dict[str, str]:
    """Legacy runs and restored steps must not claim observations that do not exist."""
    directory = _paired_directory(layout, step, pre_path)
    paths = {phase: directory / f"{phase}.json" for phase in ("pre", "post")}
    return {
        phase: path.relative_to(layout.root).as_posix()
        for phase, path in paths.items()
        if path.is_file()
    }

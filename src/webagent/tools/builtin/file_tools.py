"""File I/O tools."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.core.models import ToolResult
from webagent.tools.registry import tool
from webagent.utils.paths import get_artifacts_dir, resolve_file_path

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig


def _contained_path(base: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``base`` and reject any path that escapes it.

    ``resolve()`` normalises both ``../`` traversal and absolute-path injection
    (e.g. ``/etc/passwd``), which a plain ``base / rel`` join would otherwise
    allow.  Returns ``None`` when the result is outside ``base``.
    """
    base = base.resolve()
    out = (base / rel).resolve()
    if out == base or out.is_relative_to(base):
        return out
    return None


@tool("save_image", "Save base64 image. params: base64 (string), path (string)")
class SaveImageTool:
    def __init__(
        self,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if "base64" not in params and "image" not in params:
            raise ValueError("'base64' or 'image' required")
        if "path" not in params:
            raise ValueError("'path' required")

    async def execute(self, params: dict) -> ToolResult:
        b64 = params.get("base64") or params.get("image")
        if not b64:
            return ToolResult(success=False, tool_name="save_image", error="No image data provided")
        out = _contained_path(self.artifacts_dir, params["path"])
        if out is None:
            return ToolResult(
                success=False, tool_name="save_image", error="path escapes artifacts directory"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_bytes(base64.b64decode(b64))
            return ToolResult(success=True, tool_name="save_image", data={"path": str(out)})
        except Exception as e:
            return ToolResult(success=False, tool_name="save_image", error=str(e))


@tool("write_text", "Write text to file. params: path (string), content (string)")
class WriteTextTool:
    def __init__(
        self,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if "path" not in params:
            raise ValueError("'path' required")
        if "content" not in params and "text" not in params:
            raise ValueError("'content' or 'text' required")

    async def execute(self, params: dict) -> ToolResult:
        content = params.get("content") or params.get("text")
        out = _contained_path(self.artifacts_dir, params["path"])
        if out is None:
            return ToolResult(
                success=False, tool_name="write_text", error="path escapes artifacts directory"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_text(str(content), encoding="utf-8")
            return ToolResult(success=True, tool_name="write_text", data={"path": str(out)})
        except Exception as e:
            return ToolResult(success=False, tool_name="write_text", error=str(e))


@tool("read_image", "Read image file for vision analysis. params: path (string), open_browser=true")
class ReadImageTool:
    """Read an image file and open in browser for vision model analysis.

    When open_browser=true (default), the image is displayed in the browser
    so the planner's vision model can analyze it in the next observation.
    """

    def __init__(
        self,
        browser: Any = None,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.browser = browser
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if "path" not in params:
            raise ValueError("'path' required")

    async def execute(self, params: dict) -> ToolResult:
        path_str = params["path"].strip()
        # Use robust path resolution from paths module
        path = resolve_file_path(path_str, self.artifacts_dir)

        open_browser = params.get("open_browser", True)

        try:
            if not path.exists():
                return ToolResult(
                    success=False, tool_name="read_image", error=f"File not found: {path}"
                )
            img_data = path.read_bytes()
            b64 = base64.b64encode(img_data).decode("utf-8")
            # Determine mime type
            suffix = path.suffix.lower()
            mime = {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "gif": "image/gif",
                "webp": "image/webp",
            }.get(suffix, "image/jpeg")
            data_url = f"data:{mime};base64,{b64}"

            # Open in browser if available (for vision model analysis)
            browser_url = None
            if self.browser and open_browser:
                result = await self.browser.open_local_file(str(path))
                if result.get("success"):
                    browser_url = result.get("url")

            return ToolResult(
                success=True,
                tool_name="read_image",
                data={
                    "path": str(path),
                    "base64": b64,
                    "data_url": data_url,
                    "size": len(img_data),
                    "mime": mime,
                    "browser_url": browser_url,
                },
            )
        except Exception as e:
            return ToolResult(success=False, tool_name="read_image", error=str(e))


@tool(
    "analyze_image",
    "Directly analyze an IMAGE file (JPG, PNG) using the planner's vision model. "
    "IMPORTANT: Use the image path from pdf_list_figures result (e.g., /path/to/.../images/xxx.jpg), NOT the PDF path. "
    "Returns analysis results without needing browser screenshot. "
    "params: path (string - MUST be image file path, not PDF), question (string)",
)
class AnalyzeImageTool:
    """Analyze an image directly using the planner's vision model.

    This tool bypasses the browser screenshot workflow and directly
    calls the planner's analyze_image method for faster, more reliable
    visual analysis.

    If the vision API is not functional, this tool will return an error
    suggesting to use read_image instead.
    """

    def __init__(
        self,
        planner: Any = None,
        browser: Any = None,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.planner = planner
        self.browser = browser
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict) -> None:
        if "path" not in params:
            raise ValueError("'path' required")
        if "question" not in params:
            raise ValueError("'question' required - what do you want to know about the image?")

    async def execute(self, params: dict) -> ToolResult:
        if self.planner is None:
            return ToolResult(
                success=False,
                tool_name="analyze_image",
                error="Planner not available - this tool requires a vision-enabled planner",
            )

        if not hasattr(self.planner, "analyze_image"):
            return ToolResult(
                success=False,
                tool_name="analyze_image",
                error="Current planner does not support direct image analysis. Use read_image instead.",
            )

        # Check if vision API is actually working
        if (
            hasattr(self.planner, "vision_actually_works")
            and not self.planner.vision_actually_works
        ):
            return ToolResult(
                success=False,
                tool_name="analyze_image",
                error=(
                    "Vision API is not functioning properly. DO NOT retry analyze_image or "
                    "read_image. Instead, use 'pdf_get_figure_info' to get figure captions, "
                    "'pdf_extract_text' or 'pdf_search' to read surrounding text, then use "
                    "'done' to summarize findings based on the caption and textual description."
                ),
            )

        path_str = params["path"].strip()
        question = str(params["question"]).strip()

        # Use robust path resolution from paths module
        path = resolve_file_path(path_str, self.artifacts_dir)

        if not path.exists():
            return ToolResult(
                success=False, tool_name="analyze_image", error=f"File not found: {path}"
            )

        try:
            from PIL import Image

            img: Image.Image = Image.open(path)
            browser_url = None
            if self.browser:
                open_result = await self.browser.open_local_file(str(path))
                if open_result.get("success"):
                    browser_url = open_result.get("url")

            # Resize large images to avoid API limits
            max_dimension = 2000
            if max(img.width, img.height) > max_dimension:
                ratio = max_dimension / max(img.width, img.height)
                new_width = int(img.width * ratio)
                new_height = int(img.height * ratio)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)  # type: ignore[assignment]

            analysis = await self.planner.analyze_image(img, question)

            # Check if analysis indicates vision API failure
            analysis_lower = analysis.lower()
            vision_failure = False
            if "vision api" in analysis_lower and (
                "not functioning" in analysis_lower or "not working" in analysis_lower
            ):
                vision_failure = True
            # Also detect "I don't see any image" style responses
            no_vision_phrases = [
                "i don't see any image",
                "i cannot see",
                "no image attached",
                "no image provided",
                "unable to view",
                "i can't view",
                "i don't have the ability to view",
                "there is no image",
                "cannot analyze images",
            ]
            if any(phrase in analysis_lower for phrase in no_vision_phrases):
                vision_failure = True

            if vision_failure:
                return ToolResult(
                    success=False,
                    tool_name="analyze_image",
                    error=(
                        "Vision model cannot see the image content. "
                        "DO NOT retry analyze_image or read_image — the vision API is broken for this session. "
                        "Instead, use 'pdf_get_figure_info' to get figure captions, "
                        "'pdf_extract_text' or 'pdf_search' to read the surrounding text, "
                        "then use 'done' to summarize your findings based on the caption "
                        "and textual description."
                    ),
                )

            # Check if analysis is meaningful
            if not analysis or len(analysis) < 30:
                return ToolResult(
                    success=False,
                    tool_name="analyze_image",
                    error="Image analysis returned insufficient result. The vision API may not be working properly.",
                )

            return ToolResult(
                success=True,
                tool_name="analyze_image",
                data={
                    "path": str(path),
                    "question": question,
                    "analysis": analysis,
                    "browser_url": browser_url,
                },
            )
        except Exception as e:
            return ToolResult(success=False, tool_name="analyze_image", error=str(e))

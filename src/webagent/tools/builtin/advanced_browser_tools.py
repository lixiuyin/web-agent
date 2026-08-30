"""Browser primitives for frames, tabs, uploads, downloads, and Shadow DOM."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.core.models import ToolResult
from webagent.tools.builtin._artifact_publish import (
    publish_immutable_artifact,
    temporary_artifact_path,
)
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.builtin.browser_tools import _resolve_selector, _validate_selector
from webagent.tools.registry import tool
from webagent.utils.paths import get_artifacts_dir

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig


def _frame(browser: Any, index: int) -> Any:
    frames = browser.page.frames
    if index < 0 or index >= len(frames):
        raise ValueError(f"frame_index out of range (found {len(frames)} frames)")
    return frames[index]


@tool(
    "list_frames", "List main page and iframe contexts with indexes, names, and URLs. params: none"
)
class ListFramesTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        del params

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        del params
        frames = [
            {"index": index, "name": frame.name, "url": frame.url}
            for index, frame in enumerate(self.browser.page.frames)
        ]
        return ToolResult(success=True, tool_name="list_frames", data={"frames": frames})


@tool(
    "frame_interact",
    "Interact inside an iframe. params: frame_index (int), action=click|type|extract_text, "
    "selector={type:'text'|'css',value:string}, text?",
)
class FrameInteractTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("frame_index"), int) or params["frame_index"] < 0:
            raise ValueError("'frame_index' must be a non-negative integer")
        if params.get("action") not in {"click", "type", "extract_text"}:
            raise ValueError("'action' must be click, type, or extract_text")
        _validate_selector(params.get("selector"))
        if params["action"] == "type" and not isinstance(params.get("text"), str):
            raise ValueError("type action requires string 'text'")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        try:
            frame = _frame(self.browser, params["frame_index"])
            selector = _resolve_selector(params["selector"])
            action = params["action"]
            if action == "click":
                await frame.locator(selector).click()
                data: dict[str, Any] = {"clicked": True}
            elif action == "type":
                await frame.locator(selector).fill(params["text"])
                data = {"typed": True}
            else:
                data = {"text": await frame.locator(selector).inner_text()}
            return ToolResult(
                success=True,
                tool_name="frame_interact",
                data={"frame_index": params["frame_index"], "action": action, **data},
            )
        except Exception as exc:
            return ToolResult(success=False, tool_name="frame_interact", error=str(exc))


@tool("list_tabs", "List browser tabs with indexes, titles, URLs, and active state. params: none")
class ListTabsTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        del params

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        del params
        result = await self.browser.list_tabs()
        return ToolResult(
            success=bool(result.get("success")),
            tool_name="list_tabs",
            data={"tabs": result.get("tabs", []), "count": result.get("count", 0)},
            error=result.get("error"),
        )


@tool("switch_tab", "Switch active browser tab. params: index (non-negative int)")
class SwitchTabTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        if not isinstance(params.get("index"), int) or params["index"] < 0:
            raise ValueError("'index' must be a non-negative integer")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result = await self.browser.switch_tab(params["index"])
        return ToolResult(
            success=bool(result.get("success")),
            tool_name="switch_tab",
            data={key: value for key, value in result.items() if key not in {"success", "error"}},
            error=result.get("error"),
        )


@tool("open_tab", "Open and activate a new tab. params: url? (must be observed/user-provided)")
class OpenTabTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        if "url" in params and (not isinstance(params["url"], str) or not params["url"].strip()):
            raise ValueError("'url' must be a non-empty string when provided")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result = await self.browser.open_tab(params.get("url"))
        return ToolResult(
            success=bool(result.get("success")),
            tool_name="open_tab",
            data={key: value for key, value in result.items() if key not in {"success", "error"}},
            error=result.get("error"),
        )


@tool("close_tab", "Close a browser tab. params: index? (defaults to active tab)")
class CloseTabTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        if "index" in params and (not isinstance(params["index"], int) or params["index"] < 0):
            raise ValueError("'index' must be a non-negative integer when provided")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        result = await self.browser.close_tab(params.get("index"))
        return ToolResult(
            success=bool(result.get("success")),
            tool_name="close_tab",
            data={key: value for key, value in result.items() if key not in {"success", "error"}},
            error=result.get("error"),
        )


@tool(
    "upload_file",
    "Upload a local file through an <input type=file>. Requires human approval. "
    "params: selector={type:'text'|'css',value:string}, path (under configured upload root)",
)
class UploadFileTool:
    def __init__(self, browser: Any = None, config: AgentConfig | None = None, **kw: Any) -> None:
        self.browser = browser
        configured = getattr(config, "browser_upload_root", Path("./uploads"))
        self.upload_root = Path(configured).expanduser().resolve()

    def validate_params(self, params: dict[str, Any]) -> None:
        _validate_selector(params.get("selector"))
        if not isinstance(params.get("path"), str) or not params["path"].strip():
            raise ValueError("'path' is required")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        path = Path(params["path"]).expanduser().resolve()
        if not path.is_relative_to(self.upload_root):
            return ToolResult(
                success=False,
                tool_name="upload_file",
                error=f"Upload path must be under {self.upload_root}",
            )
        if not path.is_file():
            return ToolResult(success=False, tool_name="upload_file", error="Upload file not found")
        try:
            selector = _resolve_selector(params["selector"])
            await self.browser.page.locator(selector).set_input_files(str(path))
            return ToolResult(
                success=True,
                tool_name="upload_file",
                data={"selector": params["selector"], "filename": path.name},
            )
        except Exception as exc:
            return ToolResult(success=False, tool_name="upload_file", error=str(exc))


@tool(
    "download_file",
    "Click an element and save the resulting browser download. "
    "params: selector={type:'text'|'css',value:string}, filename?",
)
class DownloadFileTool:
    def __init__(
        self,
        browser: Any = None,
        artifacts_dir: Path | None = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.browser = browser
        self.artifacts_dir = artifacts_dir or get_artifacts_dir(config)

    def validate_params(self, params: dict[str, Any]) -> None:
        _validate_selector(params.get("selector"))
        if "filename" in params and not isinstance(params["filename"], str):
            raise ValueError("'filename' must be a string")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        try:
            selector = _resolve_selector(params["selector"])
            async with self.browser.page.expect_download() as pending:
                await self.browser.page.locator(selector).click()
            download = await pending.value
            requested = Path(str(params.get("filename") or download.suggested_filename)).name
            filename = requested if requested not in {"", ".", ".."} else "download.bin"
            destination = self.artifacts_dir / "downloads" / filename
            with temporary_artifact_path(destination) as temporary:
                await download.save_as(temporary)
                try:
                    deduplicated = await asyncio.to_thread(
                        publish_immutable_artifact, temporary, destination
                    )
                except FileExistsError:
                    return ToolResult(
                        success=False,
                        tool_name="download_file",
                        error=(
                            "Artifact already exists with different content; refusing to "
                            f"overwrite: {destination}"
                        ),
                        data={"path": str(destination), "filename": filename},
                    )
            return ToolResult(
                success=True,
                tool_name="download_file",
                data={
                    "path": str(destination),
                    "filename": filename,
                    "deduplicated": deduplicated,
                },
            )
        except Exception as exc:
            return ToolResult(success=False, tool_name="download_file", error=str(exc))


@tool(
    "shadow_dom",
    "Interact with an element inside open Shadow DOM (Playwright CSS piercing). "
    "params: action=click|type|extract_text, selector={type:'css',value:string}, text?",
)
class ShadowDomTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        _validate_selector(params.get("selector"))
        if params["selector"].get("type") != "css":
            raise ValueError("Shadow DOM interaction requires a CSS selector")
        if params.get("action") not in {"click", "type", "extract_text"}:
            raise ValueError("'action' must be click, type, or extract_text")
        if params["action"] == "type" and not isinstance(params.get("text"), str):
            raise ValueError("type action requires string 'text'")

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        try:
            locator = self.browser.page.locator(params["selector"]["value"])
            action = params["action"]
            if action == "click":
                await locator.click()
                data: dict[str, Any] = {"clicked": True}
            elif action == "type":
                await locator.fill(params["text"])
                data = {"typed": True}
            else:
                data = {"text": await locator.inner_text()}
            return ToolResult(success=True, tool_name="shadow_dom", data={"action": action, **data})
        except Exception as exc:
            return ToolResult(success=False, tool_name="shadow_dom", error=str(exc))


__all__ = [
    "CloseTabTool",
    "DownloadFileTool",
    "FrameInteractTool",
    "ListFramesTool",
    "ListTabsTool",
    "OpenTabTool",
    "ShadowDomTool",
    "SwitchTabTool",
    "UploadFileTool",
]

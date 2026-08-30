"""Page information extraction tools."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.core.models import ToolResult
from webagent.tools.builtin._artifact_publish import publish_immutable_bytes
from webagent.tools.builtin._base import BrowserToolBase
from webagent.tools.registry import tool
from webagent.utils.paths import get_artifacts_dir

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig


def _screenshot_path(artifacts_dir: Path, label: str) -> Path:
    """Build a categorized tool-produced screenshot path without traversal."""
    filename = Path(label).name.strip() or "screenshot"
    if filename in (".", ".."):
        filename = "screenshot"
    if not filename.lower().endswith((".jpg", ".jpeg")):
        filename = f"{filename}.jpg"
    return artifacts_dir / "figures" / "screenshots" / filename


@tool(
    "screenshot",
    "Capture a screenshot under an immutable label; use a new label for changed page state. "
    "params: full_page=false, label='screenshot'",
)
class ScreenshotTool:
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
        pass

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        full_page = bool(params.get("full_page", False))
        label = str(params.get("label", "")).strip() or "screenshot"
        resp = await self.browser.screenshot(full_page=full_page, return_format="base64")
        if not resp.get("success"):
            return ToolResult(success=False, tool_name="screenshot", error=resp.get("error"))
        b64: str = resp["image"]
        path = _screenshot_path(self.artifacts_dir, label)
        try:
            deduplicated = publish_immutable_bytes(base64.b64decode(b64), path)
        except FileExistsError:
            return ToolResult(
                success=False,
                tool_name="screenshot",
                error=f"Artifact already exists with different content: {path}",
                data={"path": str(path)},
            )
        return ToolResult(
            success=True,
            tool_name="screenshot",
            data={
                "path": str(path),
                "width": resp.get("width"),
                "height": resp.get("height"),
                "deduplicated": deduplicated,
            },
        )


@tool("dom_summary", "Get compact DOM summary of interactive elements. params: none")
class DomSummaryTool:
    def __init__(
        self,
        browser: Any = None,
        config: AgentConfig | None = None,
        **kw: Any,
    ) -> None:
        self.browser = browser
        self.config = config

    def validate_params(self, params: dict[str, Any]) -> None:
        pass

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        from webagent.browser.snapshot import take_snapshot

        kwargs: dict[str, Any] = {}
        if self.config is not None:
            kwargs = {
                "use_cdp": self.config.use_cdp,
                "max_elements": self.config.max_snapshot_elements,
                "filter_ads": self.config.enable_ad_filtering,
            }
        snap = await take_snapshot(self.browser.page, **kwargs)
        return ToolResult(
            success=True,
            tool_name="dom_summary",
            data={"dom_summary": snap.get("markdown", "")},
        )


@tool(
    "extract_text",
    "Extract readable text from an element or whole article/page. "
    "Use CSS body, main, or article instead of get_attribute for innerText/textContent. "
    "params: selector={type,value}",
)
class ExtractTextTool(BrowserToolBase):
    def validate_params(self, params: dict[str, Any]) -> None:
        from webagent.tools.builtin.browser_tools import _validate_selector

        _validate_selector(params.get("selector"))

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        from webagent.tools.builtin.browser_tools import _resolve_selector

        selector = _resolve_selector(params["selector"])
        resp = await self.browser.get_element_text(selector)
        if resp.get("success"):
            return ToolResult(
                success=True, tool_name="extract_text", data={"text": resp.get("text", "")}
            )
        return ToolResult(success=False, tool_name="extract_text", error=resp.get("error"))

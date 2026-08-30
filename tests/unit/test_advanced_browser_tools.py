"""Unit coverage for modern browser tool wrappers and failure boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from webagent.core.config import AgentConfig
from webagent.tools.builtin.advanced_browser_tools import (
    CloseTabTool,
    DownloadFileTool,
    FrameInteractTool,
    ListFramesTool,
    ListTabsTool,
    OpenTabTool,
    ShadowDomTool,
    SwitchTabTool,
    UploadFileTool,
)


class _Locator:
    def __init__(self) -> None:
        self.clicked = False
        self.filled: str | None = None
        self.uploaded: str | None = None

    async def click(self) -> None:
        self.clicked = True

    async def fill(self, text: str) -> None:
        self.filled = text

    async def inner_text(self) -> str:
        return "visible text"

    async def set_input_files(self, path: str) -> None:
        self.uploaded = path


class _Frame:
    name = "child"
    url = "https://frame.test/"

    def __init__(self, locator: _Locator) -> None:
        self._locator = locator

    def locator(self, _selector: str) -> _Locator:
        return self._locator


class _Download:
    suggested_filename = "suggested.txt"

    async def save_as(self, destination: Path) -> None:
        destination.write_text("payload", encoding="utf-8")


class _DownloadInfo:
    @property
    async def value(self) -> _Download:
        return _Download()


class _DownloadContext:
    async def __aenter__(self) -> _DownloadInfo:
        return _DownloadInfo()

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Page:
    def __init__(self) -> None:
        self.target = _Locator()
        self.frames = [_Frame(self.target), _Frame(self.target)]

    def locator(self, _selector: str) -> _Locator:
        return self.target

    def expect_download(self) -> _DownloadContext:
        return _DownloadContext()


class _Browser:
    def __init__(self) -> None:
        self.page = _Page()

    async def list_tabs(self) -> dict[str, Any]:
        return {"success": True, "tabs": [{"index": 0}], "count": 1}

    async def switch_tab(self, index: int) -> dict[str, Any]:
        return {"success": True, "index": index, "url": "about:blank"}

    async def open_tab(self, url: str | None) -> dict[str, Any]:
        return {"success": True, "index": 1, "url": url or "about:blank"}

    async def close_tab(self, index: int | None) -> dict[str, Any]:
        return {"success": True, "closed_index": index or 0, "remaining": 1}


def _css(value: str = "#target") -> dict[str, str]:
    return {"type": "css", "value": value}


async def test_frame_and_shadow_actions_cover_click_type_and_extract() -> None:
    browser = _Browser()
    frames = await ListFramesTool(browser=browser).execute({})
    frame_tool = FrameInteractTool(browser=browser)
    clicked = await frame_tool.execute({"frame_index": 1, "action": "click", "selector": _css()})
    typed = await frame_tool.execute(
        {"frame_index": 1, "action": "type", "selector": _css(), "text": "hello"}
    )
    extracted = await frame_tool.execute(
        {"frame_index": 1, "action": "extract_text", "selector": _css()}
    )
    shadow = ShadowDomTool(browser=browser)
    shadow_clicked = await shadow.execute({"action": "click", "selector": _css()})
    shadow_typed = await shadow.execute({"action": "type", "selector": _css(), "text": "shadow"})
    shadow_text = await shadow.execute({"action": "extract_text", "selector": _css()})

    assert len(frames.data["frames"]) == 2
    assert clicked.success and typed.success
    assert extracted.data["text"] == "visible text"
    assert shadow_clicked.success and shadow_typed.success
    assert shadow_text.data["text"] == "visible text"


async def test_tab_tools_forward_controller_results() -> None:
    browser = _Browser()

    listed = await ListTabsTool(browser=browser).execute({})
    switched = await SwitchTabTool(browser=browser).execute({"index": 0})
    opened = await OpenTabTool(browser=browser).execute({"url": "https://example.test"})
    closed = await CloseTabTool(browser=browser).execute({"index": 1})

    assert listed.data["count"] == 1
    assert switched.data["index"] == 0
    assert opened.data["url"] == "https://example.test"
    assert closed.data["remaining"] == 1


async def test_upload_is_confined_and_download_filename_is_sanitized(tmp_path: Path) -> None:
    browser = _Browser()
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    allowed = upload_root / "allowed.txt"
    allowed.write_text("allowed", encoding="utf-8")
    config = AgentConfig(_env_file=None, browser_upload_root=upload_root, output_dir=tmp_path)
    upload_tool = UploadFileTool(browser=browser, config=config)

    escaped = await upload_tool.execute({"selector": _css(), "path": str(tmp_path / "x")})
    missing = await upload_tool.execute(
        {"selector": _css(), "path": str(upload_root / "missing.txt")}
    )
    uploaded = await upload_tool.execute({"selector": _css(), "path": str(allowed)})
    downloaded = await DownloadFileTool(
        browser=browser,
        artifacts_dir=tmp_path / "artifacts",
        config=config,
    ).execute({"selector": _css(), "filename": "../safe.txt"})

    assert escaped.success is False and missing.success is False
    assert uploaded.data["filename"] == "allowed.txt"
    assert Path(downloaded.data["path"]).name == "safe.txt"
    assert Path(downloaded.data["path"]).read_text(encoding="utf-8") == "payload"


async def test_browser_download_same_content_is_idempotent_without_touching_target(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    target = artifacts / "downloads" / "same.txt"
    target.parent.mkdir(parents=True)
    target.write_text("payload", encoding="utf-8")
    fixed_mtime = 1_700_000_000_123_456_789
    os.utime(target, ns=(fixed_mtime, fixed_mtime))

    result = await DownloadFileTool(browser=_Browser(), artifacts_dir=artifacts).execute(
        {"selector": _css(), "filename": "same.txt"}
    )

    assert result.success is True
    assert result.data["deduplicated"] is True
    assert target.read_text(encoding="utf-8") == "payload"
    assert target.stat().st_mtime_ns == fixed_mtime
    assert not list(target.parent.glob(".same.txt.*.part"))


async def test_browser_download_different_content_fails_closed(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    target = artifacts / "downloads" / "same.txt"
    target.parent.mkdir(parents=True)
    target.write_text("original", encoding="utf-8")
    original_mtime = target.stat().st_mtime_ns

    result = await DownloadFileTool(browser=_Browser(), artifacts_dir=artifacts).execute(
        {"selector": _css(), "filename": "same.txt"}
    )

    assert result.success is False
    assert "refusing to overwrite" in result.error
    assert target.read_text(encoding="utf-8") == "original"
    assert target.stat().st_mtime_ns == original_mtime
    assert not list(target.parent.glob(".same.txt.*.part"))


async def test_failed_browser_download_preserves_same_name_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    target = artifacts / "downloads" / "same.txt"
    target.parent.mkdir(parents=True)
    target.write_text("original", encoding="utf-8")
    original_mtime = target.stat().st_mtime_ns

    async def fail_after_partial_write(self: _Download, destination: Path) -> None:
        destination.write_text("partial", encoding="utf-8")
        raise OSError("download interrupted")

    monkeypatch.setattr(_Download, "save_as", fail_after_partial_write)
    result = await DownloadFileTool(browser=_Browser(), artifacts_dir=artifacts).execute(
        {"selector": _css(), "filename": "same.txt"}
    )

    assert result.success is False
    assert target.read_text(encoding="utf-8") == "original"
    assert target.stat().st_mtime_ns == original_mtime
    assert not list(target.parent.glob(".same.txt.*.part"))


@pytest.mark.parametrize(
    ("tool", "params"),
    [
        (FrameInteractTool(), {"frame_index": -1, "action": "click", "selector": _css()}),
        (SwitchTabTool(), {"index": -1}),
        (OpenTabTool(), {"url": ""}),
        (CloseTabTool(), {"index": -1}),
        (UploadFileTool(), {"selector": _css()}),
        (DownloadFileTool(), {"selector": _css(), "filename": 1}),
        (ShadowDomTool(), {"action": "click", "selector": {"type": "text", "value": "x"}}),
    ],
)
def test_advanced_tool_validation_rejects_invalid_parameters(
    tool: Any, params: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        tool.validate_params(params)

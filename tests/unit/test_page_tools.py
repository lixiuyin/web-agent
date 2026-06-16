"""Tests for page tools."""

from __future__ import annotations

import base64

from webagent.tools.builtin.page_tools import ScreenshotTool


class _MockBrowser:
    async def screenshot(self, full_page: bool, return_format: str) -> dict:
        assert full_page is False
        assert return_format == "base64"
        return {
            "success": True,
            "image": base64.b64encode(b"fake-jpeg").decode("ascii"),
            "width": 10,
            "height": 20,
        }


async def test_screenshot_label_cannot_escape_artifacts_dir(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    tool = ScreenshotTool(browser=_MockBrowser(), artifacts_dir=artifacts)

    result = await tool.execute({"label": "../outside"})

    assert result.success is True
    path = artifacts / "outside.jpg"
    assert result.data["path"] == str(path)
    assert path.read_bytes() == b"fake-jpeg"
    assert not (artifacts.parent / "outside.jpg").exists()

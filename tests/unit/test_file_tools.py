"""Tests for file tools."""

from __future__ import annotations

from PIL import Image

from webagent.tools.builtin.file_tools import AnalyzeImageTool


class _Planner:
    vision_actually_works = True

    async def analyze_image(self, image: Image.Image, question: str) -> str:
        assert image.size == (12, 8)
        assert question == "describe it"
        return "This image contains a simple red rectangle used for testing."


class _Browser:
    def __init__(self) -> None:
        self.opened: str | None = None

    async def open_local_file(self, file_path: str) -> dict:
        self.opened = file_path
        return {"success": True, "url": f"file://{file_path}"}


async def test_analyze_image_opens_image_in_browser(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    image_path = artifacts / "figure.jpg"
    Image.new("RGB", (12, 8), "red").save(image_path)
    browser = _Browser()
    tool = AnalyzeImageTool(planner=_Planner(), browser=browser, artifacts_dir=artifacts)

    result = await tool.execute({"path": "figure.jpg", "question": "describe it"})

    assert result.success is True
    assert browser.opened == str(image_path.resolve())
    assert result.data["browser_url"] == f"file://{image_path.resolve()}"

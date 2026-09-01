"""Tests for file tools."""

from __future__ import annotations

import base64

import pytest
from PIL import Image

from webagent.tools.builtin.file_tools import (
    AnalyzeImageTool,
    ReadImageTool,
    SaveImageTool,
    WriteTextTool,
    _detect_vision_failure,
    _resize_for_api,
)


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


class TestSaveImage:
    def test_validation(self, tmp_path):
        tool = SaveImageTool(artifacts_dir=tmp_path)
        with pytest.raises(ValueError):
            tool.validate_params({"path": "a.png"})
        with pytest.raises(ValueError):
            tool.validate_params({"base64": "x"})

    async def test_success(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        b64 = base64.b64encode(b"binarydata").decode()
        tool = SaveImageTool(artifacts_dir=artifacts)
        result = await tool.execute({"base64": b64, "path": "sub/out.png"})
        assert result.success
        assert (artifacts / "figures" / "sub" / "out.png").read_bytes() == b"binarydata"

    async def test_explicit_figure_category_is_not_duplicated(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        b64 = base64.b64encode(b"binarydata").decode()
        result = await SaveImageTool(artifacts_dir=artifacts).execute(
            {"base64": b64, "path": "figures/out.png"}
        )
        assert result.success
        assert (artifacts / "figures" / "out.png").is_file()

    async def test_same_name_is_idempotent_but_different_image_is_rejected(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        tool = SaveImageTool(artifacts_dir=artifacts)
        first = base64.b64encode(b"first").decode()

        assert (await tool.execute({"base64": first, "path": "out.png"})).success
        same = await tool.execute({"base64": first, "path": "out.png"})
        different = await tool.execute(
            {"base64": base64.b64encode(b"different").decode(), "path": "out.png"}
        )

        assert same.success and same.data["deduplicated"] is True
        assert not different.success
        assert (artifacts / "figures" / "out.png").read_bytes() == b"first"

    async def test_no_data(self, tmp_path):
        tool = SaveImageTool(artifacts_dir=tmp_path)
        result = await tool.execute({"base64": "", "path": "a.png"})
        assert not result.success

    async def test_path_escape(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        tool = SaveImageTool(artifacts_dir=artifacts)
        result = await tool.execute({"base64": "aGk=", "path": "../escape.png"})
        assert not result.success
        assert "escapes" in result.error

    @pytest.mark.parametrize("path", ("..\\escape.png", "C:\\private\\escape.png"))
    async def test_windows_path_escape(self, tmp_path, path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        result = await SaveImageTool(artifacts_dir=artifacts).execute(
            {"base64": "aGk=", "path": path}
        )
        assert not result.success

    async def test_decode_error(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        tool = SaveImageTool(artifacts_dir=artifacts)
        result = await tool.execute({"base64": "!!!notbase64!!!", "path": "a.png"})
        assert not result.success


class TestWriteText:
    def test_validation(self, tmp_path):
        tool = WriteTextTool(artifacts_dir=tmp_path)
        with pytest.raises(ValueError):
            tool.validate_params({"content": "x"})
        with pytest.raises(ValueError):
            tool.validate_params({"path": "a.txt"})

    async def test_success_text_alias(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        tool = WriteTextTool(artifacts_dir=artifacts)
        result = await tool.execute({"path": "note.txt", "text": "hello"})
        assert result.success
        assert (artifacts / "files" / "note.txt").read_text() == "hello"

    async def test_empty_text_and_immutable_same_name_contract(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        tool = WriteTextTool(artifacts_dir=artifacts)

        first = await tool.execute({"path": "note.txt", "content": ""})
        same = await tool.execute({"path": "note.txt", "content": ""})
        different = await tool.execute({"path": "note.txt", "content": "changed"})

        assert first.success and first.data["deduplicated"] is False
        assert same.success and same.data["deduplicated"] is True
        assert not different.success
        assert (artifacts / "files" / "note.txt").read_text() == ""

    async def test_path_escape(self, tmp_path):
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        tool = WriteTextTool(artifacts_dir=artifacts)
        result = await tool.execute({"path": "/etc/passwd", "content": "x"})
        assert not result.success


class TestReadImage:
    async def test_missing_file(self, tmp_path):
        artifacts = tmp_path / "outputs" / "artifacts"
        artifacts.mkdir(parents=True)
        tool = ReadImageTool(artifacts_dir=artifacts)
        result = await tool.execute({"path": "gone.png"})
        assert not result.success
        assert "not found" in result.error.lower()

    async def test_success_encodes_and_opens(self, tmp_path):
        artifacts = tmp_path / "outputs" / "artifacts"
        artifacts.mkdir(parents=True)
        img = artifacts / "pic.png"
        Image.new("RGB", (4, 4), "blue").save(img)
        browser = _Browser()
        tool = ReadImageTool(browser=browser, artifacts_dir=artifacts)
        result = await tool.execute({"path": "pic.png"})
        assert result.success
        assert result.data["mime"] == "image/png"
        assert result.data["data_url"].startswith("data:image/png;base64,")
        assert browser.opened == str(img.resolve())

    async def test_success_no_browser_open(self, tmp_path):
        artifacts = tmp_path / "outputs" / "artifacts"
        artifacts.mkdir(parents=True)
        img = artifacts / "pic.jpg"
        Image.new("RGB", (4, 4), "blue").save(img)
        browser = _Browser()
        tool = ReadImageTool(browser=browser, artifacts_dir=artifacts)
        result = await tool.execute({"path": "pic.jpg", "open_browser": False})
        assert result.success
        assert result.data["mime"] == "image/jpeg"
        assert result.data["browser_url"] is None
        assert browser.opened is None


class TestAnalyzeImageBranches:
    async def test_no_planner(self, tmp_path):
        tool = AnalyzeImageTool(planner=None, artifacts_dir=tmp_path)
        result = await tool.execute({"path": "x.png", "question": "q"})
        assert not result.success and "Planner not available" in result.error

    async def test_planner_without_analyze(self, tmp_path):
        class NoVision:
            pass

        tool = AnalyzeImageTool(planner=NoVision(), artifacts_dir=tmp_path)
        result = await tool.execute({"path": "x.png", "question": "q"})
        assert not result.success and "does not support" in result.error

    async def test_vision_not_working(self, tmp_path):
        class Broken:
            vision_actually_works = False

            async def analyze_image(self, image, question):
                return "ok"

        tool = AnalyzeImageTool(planner=Broken(), artifacts_dir=tmp_path)
        result = await tool.execute({"path": "x.png", "question": "q"})
        assert not result.success and "not functioning" in result.error

    async def test_missing_file(self, tmp_path):
        artifacts = tmp_path / "outputs" / "artifacts"
        artifacts.mkdir(parents=True)
        tool = AnalyzeImageTool(planner=_Planner(), artifacts_dir=artifacts)
        result = await tool.execute({"path": "missing.jpg", "question": "q"})
        assert not result.success and "not found" in result.error.lower()

    async def test_detected_vision_failure(self, tmp_path):
        artifacts = tmp_path / "outputs" / "artifacts"
        artifacts.mkdir(parents=True)
        img = artifacts / "f.jpg"
        Image.new("RGB", (6, 6), "red").save(img)

        class BlindPlanner:
            vision_actually_works = True

            async def analyze_image(self, image, question):
                return "I don't see any image attached to analyze."

        tool = AnalyzeImageTool(planner=BlindPlanner(), artifacts_dir=artifacts)
        result = await tool.execute({"path": "f.jpg", "question": "q"})
        assert not result.success and "cannot see" in result.error

    async def test_insufficient_result(self, tmp_path):
        artifacts = tmp_path / "outputs" / "artifacts"
        artifacts.mkdir(parents=True)
        img = artifacts / "f.jpg"
        Image.new("RGB", (6, 6), "red").save(img)

        class TersePlanner:
            vision_actually_works = True

            async def analyze_image(self, image, question):
                return "short"

        tool = AnalyzeImageTool(planner=TersePlanner(), artifacts_dir=artifacts)
        result = await tool.execute({"path": "f.jpg", "question": "q"})
        assert not result.success and "insufficient" in result.error


class TestHelpers:
    def test_resize_no_op_for_small(self):
        img = Image.new("RGB", (100, 50))
        assert _resize_for_api(img) is img

    def test_resize_downscales_large(self):
        img = Image.new("RGB", (4000, 1000))
        out = _resize_for_api(img)
        assert max(out.width, out.height) == 2000

    def test_detect_vision_failure_phrases(self):
        assert _detect_vision_failure("I cannot see the picture") is True
        assert _detect_vision_failure("The vision api is not working right now") is True
        assert _detect_vision_failure("A detailed chart of results") is False

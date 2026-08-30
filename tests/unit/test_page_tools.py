"""Tests for page tools."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from webagent.browser import snapshot as snapshot_module
from webagent.core.config import AgentConfig
from webagent.tools.builtin.page_tools import (
    DomSummaryTool,
    ExtractTextTool,
    ScreenshotTool,
    _screenshot_path,
)


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
    path = artifacts / "figures" / "screenshots" / "outside.jpg"
    assert result.data["path"] == str(path)
    assert path.read_bytes() == b"fake-jpeg"
    assert not (artifacts.parent / "outside.jpg").exists()


async def test_screenshot_same_label_never_overwrites_different_evidence(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    tool = ScreenshotTool(browser=_MockBrowser(), artifacts_dir=artifacts)

    first = await tool.execute({"label": "evidence"})
    same = await tool.execute({"label": "evidence"})
    (artifacts / "figures" / "screenshots" / "evidence.jpg").write_bytes(b"prior")
    different = await tool.execute({"label": "evidence"})

    assert first.success
    assert same.success and same.data["deduplicated"] is True
    assert not different.success
    assert (artifacts / "figures" / "screenshots" / "evidence.jpg").read_bytes() == b"prior"


async def test_dom_summary_applies_snapshot_config(monkeypatch):
    captured: dict = {}

    async def take_snapshot(page, **kwargs):
        captured.update(kwargs)
        return {"markdown": "summary"}

    monkeypatch.setattr(snapshot_module, "take_snapshot", take_snapshot)
    config = AgentConfig(
        _env_file=None,
        use_cdp=False,
        max_snapshot_elements=7,
        enable_ad_filtering=False,
    )
    tool = DomSummaryTool(browser=SimpleNamespace(page=object()), config=config)

    result = await tool.execute({})

    assert result.data == {"dom_summary": "summary"}
    assert captured == {"use_cdp": False, "max_elements": 7, "filter_ads": False}


class TestScreenshotPath:
    def test_appends_jpg(self, tmp_path):
        assert _screenshot_path(tmp_path, "shot") == (
            tmp_path / "figures" / "screenshots" / "shot.jpg"
        )

    def test_keeps_jpeg(self, tmp_path):
        assert _screenshot_path(tmp_path, "a.jpeg").name == "a.jpeg"

    def test_dot_label_falls_back(self, tmp_path):
        assert _screenshot_path(tmp_path, ".").name == "screenshot.jpg"

    def test_empty_label_falls_back(self, tmp_path):
        assert _screenshot_path(tmp_path, "").name == "screenshot.jpg"


class _FailingScreenshotBrowser:
    async def screenshot(self, full_page: bool, return_format: str) -> dict:
        return {"success": False, "error": "capture failed"}


async def test_screenshot_failure(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    tool = ScreenshotTool(browser=_FailingScreenshotBrowser(), artifacts_dir=artifacts)
    result = await tool.execute({})
    assert not result.success and result.error == "capture failed"


async def test_dom_summary_without_config(monkeypatch):
    from types import SimpleNamespace

    captured: dict = {}

    async def take_snapshot(page, **kwargs):
        captured.update(kwargs)
        return {"markdown": "s"}

    monkeypatch.setattr(snapshot_module, "take_snapshot", take_snapshot)
    tool = DomSummaryTool(browser=SimpleNamespace(page=object()), config=None)
    result = await tool.execute({})
    assert result.success
    assert captured == {}


class _TextBrowser:
    def __init__(self, resp: dict) -> None:
        self._resp = resp

    async def get_element_text(self, selector: str) -> dict:
        return self._resp


class TestExtractText:
    def test_validation(self):
        with pytest.raises(ValueError):
            ExtractTextTool(browser=_TextBrowser({})).validate_params({"selector": "bad"})

    async def test_success(self):
        tool = ExtractTextTool(browser=_TextBrowser({"success": True, "text": "hello"}))
        result = await tool.execute({"selector": {"type": "css", "value": "#a"}})
        assert result.success and result.data["text"] == "hello"

    async def test_failure(self):
        tool = ExtractTextTool(browser=_TextBrowser({"success": False, "error": "no element"}))
        result = await tool.execute({"selector": {"type": "css", "value": "#a"}})
        assert not result.success and result.error == "no element"

"""Branch tests for the download/parse PDF tools in ``pdf_tools.py``.

These tools wrap the cloud OCR cascade (``parse_pdf``) and the PyMuPDF helpers
in ``utils.pdf``.  All external work is patched so the full pipeline
(path resolution → parse → ToolResult shaping) runs offline.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from webagent.parser.models import ImageInfo, PDFParseResult, TableInfo, TextBlock
from webagent.tools.builtin import _pdf_common, pdf_tools
from webagent.tools.builtin._pdf_common import pdf_cache_key, pdf_result_cache
from webagent.tools.builtin.pdf_tools import (
    DownloadPdfTool,
    GetFigureInfoTool,
    PdfContentSummaryTool,
    PdfExtractImagesTool,
    PdfExtractTextTool,
    PdfFindImagesTool,
    PdfFindSectionTool,
    PdfFindTablesTool,
    PdfParseTool,
)


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


@pytest.fixture
def pdf_file(artifacts_dir: Path) -> Path:
    pdf = artifacts_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    return pdf


def _result(**extra: Any) -> PDFParseResult:
    r = PDFParseResult(
        markdown_path=None, json_path=None, images_dir="i", output_dir="o", backend="marker"
    )
    r.images.append(
        ImageInfo("/tmp/fig1.png", 0, (0, 0, 0, 0), caption="System overview", figure_number="1")
    )
    r.tables.append(
        TableInfo(
            "/tmp/t1.html",
            0,
            (0, 0, 0, 0),
            caption="Ablation results",
            table_number="1",
            html_body="<table></table>",
        )
    )
    r.sections["1:Introduction"] = [TextBlock("Intro text", 0, (0, 0, 0, 0), level=1)]
    for k, v in extra.items():
        setattr(r, k, v)
    return r


def _patch_parse(monkeypatch: pytest.MonkeyPatch, result: PDFParseResult) -> None:
    monkeypatch.setattr(pdf_tools, "parse_pdf", lambda *a, **k: result)
    monkeypatch.setattr(_pdf_common, "parse_pdf", lambda *a, **k: result)


@pytest.fixture(autouse=True)
def _clear_pdf_cache() -> None:
    pdf_result_cache.clear()
    yield
    pdf_result_cache.clear()


class TestDownloadPdf:
    async def test_validation(self, artifacts_dir: Path) -> None:
        tool = DownloadPdfTool(artifacts_dir=artifacts_dir)
        with pytest.raises(ValueError):
            tool.validate_params({"url": ""})
        with pytest.raises(ValueError):
            tool.validate_params({"url": "ftp://example.com/a.pdf"})
        tool.validate_params({"url": "https://example.com/a.pdf"})

    async def test_success_appends_extension(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        written: dict[str, Any] = {}

        def fake_dl(url: str, out_path: Path, context: Any = None) -> None:
            out_path.write_bytes(b"%PDF-1.4")
            written["path"] = out_path

        monkeypatch.setattr(pdf_tools, "_download_with_urlopen", fake_dl)
        tool = DownloadPdfTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"url": "https://example.com/report"})
        assert result.success
        assert result.data["filename"] == "report.pdf"
        assert written["path"].name.startswith(".report.pdf.")
        assert written["path"].name.endswith(".part")
        assert written["path"].parent == artifacts_dir / "downloads"
        assert (artifacts_dir / "downloads" / "report.pdf").read_bytes() == b"%PDF-1.4"
        assert result.data["deduplicated"] is False

    async def test_failed_same_name_download_preserves_prior_artifact(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = artifacts_dir / "downloads" / "report.pdf"
        target.parent.mkdir()
        target.write_bytes(b"%PDF-1.4 original")
        original_mtime = target.stat().st_mtime_ns

        def fake_dl(url: str, out_path: Path, context: Any = None) -> None:
            out_path.write_bytes(b"partial")
            raise OSError("connection lost")

        monkeypatch.setattr(pdf_tools, "_download_with_urlopen", fake_dl)
        result = await DownloadPdfTool(artifacts_dir=artifacts_dir).execute(
            {"url": "https://example.com/report.pdf"}
        )

        assert result.success is False
        assert target.read_bytes() == b"%PDF-1.4 original"
        assert target.stat().st_mtime_ns == original_mtime
        assert not list(target.parent.glob(".report.pdf.*.part"))

    async def test_identical_same_name_download_is_idempotent_without_touching_target(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = artifacts_dir / "downloads" / "report.pdf"
        target.parent.mkdir()
        content = b"%PDF-1.4 identical"
        target.write_bytes(content)
        fixed_mtime = 1_700_000_000_123_456_789
        os.utime(target, ns=(fixed_mtime, fixed_mtime))

        monkeypatch.setattr(
            pdf_tools,
            "_download_with_urlopen",
            lambda url, out_path, context=None: out_path.write_bytes(content),
        )
        result = await DownloadPdfTool(artifacts_dir=artifacts_dir).execute(
            {"url": "https://example.com/report.pdf"}
        )

        assert result.success is True
        assert result.data["deduplicated"] is True
        assert target.read_bytes() == content
        assert target.stat().st_mtime_ns == fixed_mtime
        assert not list(target.parent.glob(".report.pdf.*.part"))

    async def test_different_same_name_download_fails_closed_and_preserves_target(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = artifacts_dir / "downloads" / "report.pdf"
        target.parent.mkdir()
        target.write_bytes(b"%PDF-1.4 original")
        original_mtime = target.stat().st_mtime_ns
        monkeypatch.setattr(
            pdf_tools,
            "_download_with_urlopen",
            lambda url, out_path, context=None: out_path.write_bytes(b"%PDF-1.4 replacement"),
        )

        result = await DownloadPdfTool(artifacts_dir=artifacts_dir).execute(
            {"url": "https://example.com/report.pdf"}
        )

        assert result.success is False
        assert "refusing to overwrite" in result.error
        assert target.read_bytes() == b"%PDF-1.4 original"
        assert target.stat().st_mtime_ns == original_mtime
        assert not list(target.parent.glob(".report.pdf.*.part"))

    async def test_ssl_fallback(self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import ssl

        calls: list[int] = []

        def fake_dl(url: str, out_path: Path, context: Any = None) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise ssl.SSLCertVerificationError("bad cert")
            out_path.write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(pdf_tools, "_download_with_urlopen", fake_dl)
        tool = DownloadPdfTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"url": "https://example.com/a.pdf"})
        assert result.success
        assert "ssl_warning" in result.data
        assert len(calls) == 2

    async def test_ssl_fallback_also_fails(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ssl

        def fake_dl(url: str, out_path: Path, context: Any = None) -> None:
            if context is None or context.verify_mode != ssl.CERT_NONE:
                raise ssl.SSLCertVerificationError("bad cert")
            raise OSError("network down")

        monkeypatch.setattr(pdf_tools, "_download_with_urlopen", fake_dl)
        tool = DownloadPdfTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"url": "https://example.com/a.pdf"})
        assert not result.success
        assert "even without SSL verify" in result.error

    async def test_download_error(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_dl(url: str, out_path: Path, context: Any = None) -> None:
            raise OSError("boom")

        monkeypatch.setattr(pdf_tools, "_download_with_urlopen", fake_dl)
        tool = DownloadPdfTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"url": "https://example.com/a.pdf"})
        assert not result.success
        assert "Download failed" in result.error

    async def test_html_preview_is_rejected_without_implicit_raw_url_discovery(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw_url = "https://github.com/org/repo/raw/refs/heads/main/report.pdf"

        def fake_dl(url: str, out_path: Path, context: Any = None) -> None:
            out_path.write_text(
                f'<html><script>{{"rawBlobUrl":"{raw_url}"}}</script></html>',
                encoding="utf-8",
            )

        monkeypatch.setattr(pdf_tools, "_download_with_urlopen", fake_dl)
        result = await DownloadPdfTool(artifacts_dir=artifacts_dir).execute(
            {"url": "https://github.com/org/repo/blob/main/report.pdf"}
        )

        assert result.success is False
        assert "not a PDF" in result.error
        assert "suggested_download_urls" not in result.data
        assert raw_url not in str(result.data)
        assert not (artifacts_dir / "downloads" / "report.pdf").exists()

    async def test_non_pdf_without_download_link_is_rejected(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            pdf_tools,
            "_download_with_urlopen",
            lambda url, out_path, context=None: out_path.write_text("not a pdf"),
        )

        result = await DownloadPdfTool(artifacts_dir=artifacts_dir).execute(
            {"url": "https://example.com/report.pdf"}
        )

        assert result.success is False
        assert "suggested_download_urls" not in result.data

    async def test_opens_in_browser(
        self, artifacts_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeBrowser:
            async def open_local_file(self, path: str) -> dict[str, Any]:
                return {"success": True, "url": "file:///opened"}

        monkeypatch.setattr(
            pdf_tools,
            "_download_with_urlopen",
            lambda url, out_path, context=None: out_path.write_bytes(b"%PDF-1.4"),
        )
        tool = DownloadPdfTool(browser=FakeBrowser(), artifacts_dir=artifacts_dir)
        result = await tool.execute({"url": "https://example.com/a.pdf"})
        assert result.success
        assert result.data["browser_url"] == "file:///opened"


class TestPdfParse:
    async def test_validation(self, artifacts_dir: Path) -> None:
        with pytest.raises(ValueError):
            PdfParseTool(artifacts_dir=artifacts_dir).validate_params({"path": " "})

    async def test_missing_file(self, artifacts_dir: Path) -> None:
        tool = PdfParseTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": "does-not-exist.pdf"})
        assert not result.success

    async def test_success_with_markdown(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        md = artifacts_dir / "parsed.md"
        md.write_text("# Title\n" + "x" * 9000, encoding="utf-8")
        parsed = _result(markdown_path=str(md))
        _patch_parse(monkeypatch, parsed)
        tool = PdfParseTool(artifacts_dir=artifacts_dir)
        try:
            result = await tool.execute({"path": str(pdf_file)})
            assert result.success
            assert result.data["markdown"].endswith("...[truncated]")
            assert result.data["image_count"] == 1
            assert result.data["table_count"] == 1
            assert result.data["section_count"] == 1
            assert pdf_result_cache[pdf_cache_key(pdf_file, artifacts_dir)] is parsed
        finally:
            pdf_result_cache.clear()

    async def test_parse_error(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result(error="cascade failed"))
        tool = PdfParseTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert not result.success
        assert "cascade failed" in result.error

    async def test_parse_exception(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: Any, **k: Any) -> PDFParseResult:
            raise RuntimeError("kaboom")

        monkeypatch.setattr(pdf_tools, "parse_pdf", boom)
        tool = PdfParseTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert not result.success
        assert "kaboom" in result.error

    async def test_output_dir_escape(self, artifacts_dir: Path, pdf_file: Path) -> None:
        tool = PdfParseTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "output_dir": "/etc"})
        assert not result.success
        assert "escapes" in result.error


class TestFindTools:
    async def test_find_images(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result())
        tool = PdfFindImagesTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "keyword": "overview"})
        assert result.success and result.data["matching_count"] == 1

    async def test_find_images_error(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result(error="fail"))
        tool = PdfFindImagesTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "keyword": "overview"})
        assert not result.success

    async def test_find_tables(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result())
        tool = PdfFindTablesTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "keyword": "ablation"})
        assert result.success and result.data["matching_count"] == 1

    async def test_find_section_found(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result())
        tool = PdfFindSectionTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "title": "Introduction"})
        assert result.success and result.data["found"] is True
        assert "Intro text" in result.data["content"]

    async def test_find_section_not_found(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result())
        tool = PdfFindSectionTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "title": "Nonexistent"})
        assert result.success and result.data["found"] is False

    async def test_find_section_validation(self, artifacts_dir: Path) -> None:
        with pytest.raises(ValueError):
            PdfFindSectionTool(artifacts_dir=artifacts_dir).validate_params({"path": "x"})

    async def test_content_summary(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result())
        tool = PdfContentSummaryTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert result.success
        assert result.data["image_count"] == 1
        assert result.data["table_count"] == 1


class TestExtractTools:
    async def test_extract_text(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pdf_tools, "extract_text", lambda p: "hello world")
        tool = PdfExtractTextTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert result.success and result.data["text"] == "hello world"

    async def test_extract_text_error(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(p: str) -> str:
            raise RuntimeError("no text")

        monkeypatch.setattr(pdf_tools, "extract_text", boom)
        tool = PdfExtractTextTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert not result.success and "no text" in result.error

    async def test_extract_images_with_browser(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        img_path = artifacts_dir / "fig.png"
        img_path.write_bytes(b"x")

        def fake_extract(path: str, out_dir: Path) -> list[dict[str, Any]]:
            return [{"path": str(img_path), "likely_figure": True}]

        opened: dict[str, Any] = {}

        class FakeBrowser:
            async def open_local_file(self, path: str) -> dict[str, Any]:
                opened["path"] = path
                return {"success": True, "url": "file:///fig"}

        monkeypatch.setattr(pdf_tools, "extract_images", fake_extract)
        tool = PdfExtractImagesTool(browser=FakeBrowser(), artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert result.success
        assert result.data["browser_url"] == "file:///fig"
        assert opened["path"] == str(img_path)

    async def test_extract_images_error(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(path: str, out_dir: Path) -> list[dict[str, Any]]:
            raise RuntimeError("extract fail")

        monkeypatch.setattr(pdf_tools, "extract_images", boom)
        tool = PdfExtractImagesTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file)})
        assert not result.success


class TestGetFigureInfo:
    async def test_validation(self, artifacts_dir: Path) -> None:
        tool = GetFigureInfoTool(artifacts_dir=artifacts_dir)
        with pytest.raises(ValueError):
            tool.validate_params({})
        with pytest.raises(ValueError):
            tool.validate_params({"path": "x", "figure_number": 0})
        tool.validate_params({"path": "x", "figure_number": 2})

    async def test_found_in_images(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parsed = _result()
        calls = 0

        def fake_parse(*args: Any, **kwargs: Any) -> PDFParseResult:
            nonlocal calls
            calls += 1
            return parsed

        monkeypatch.setattr(_pdf_common, "parse_pdf", fake_parse)
        tool = GetFigureInfoTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "figure_number": 1})
        assert result.success and result.data["found"] is True
        assert result.data["caption"] == "System overview"

        # A downstream shared PDF tool must reuse this exact parse.
        mentions = await PdfContentSummaryTool(artifacts_dir=artifacts_dir).execute(
            {"path": str(pdf_file)}
        )
        assert mentions.success
        assert calls == 1

    async def test_fallback_caption(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result(images=[]))
        monkeypatch.setattr(
            pdf_tools, "extract_figure_captions", lambda p: {"figure 2": "Loss curve"}
        )
        tool = GetFigureInfoTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "figure_number": 2})
        assert result.success and result.data["caption"] == "Loss curve"

    async def test_mentioned_only(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result(images=[]))
        monkeypatch.setattr(pdf_tools, "extract_figure_captions", lambda p: {})
        monkeypatch.setattr(pdf_tools, "extract_text", lambda p: "See Figure 3 for details.")
        tool = GetFigureInfoTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "figure_number": 3})
        assert result.success
        assert result.data["found"] is False
        assert result.data["mentioned"] is True

    async def test_not_found(
        self, artifacts_dir: Path, pdf_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_parse(monkeypatch, _result(images=[]))
        monkeypatch.setattr(pdf_tools, "extract_figure_captions", lambda p: {})
        monkeypatch.setattr(pdf_tools, "extract_text", lambda p: "no figures here")
        tool = GetFigureInfoTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf_file), "figure_number": 9})
        assert result.success and result.data["found"] is False

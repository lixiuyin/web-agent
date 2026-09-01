"""Tests for artifact path resolution safeguards."""

from __future__ import annotations

from pathlib import Path

import pytest

from webagent.core.config import AgentConfig
from webagent.tools.builtin import pdf_tools
from webagent.tools.builtin.pdf_mining_tools import ExtractMetricsTool
from webagent.tools.builtin.pdf_qa_tools import PdfQATool
from webagent.tools.builtin.pdf_tools import PdfExtractTextTool, PdfParseTool
from webagent.utils.paths import (
    _find_most_recent_pdf,
    get_artifacts_dir,
    get_output_dir,
    get_pdf_extract_dir,
    get_run_layout,
    resolve_file_path,
    resolve_pdf_path,
)


def _artifacts_dir(tmp_path: Path) -> Path:
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    return artifacts


def test_resolve_pdf_path_keeps_simple_names_under_artifacts(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    pdf = artifacts / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    path, was_fallback, message = resolve_pdf_path("paper.pdf", artifacts)

    assert path == pdf.resolve()
    assert was_fallback is False
    assert message is None


def test_resolve_pdf_path_rejects_absolute_escape(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")

    path, was_fallback, message = resolve_pdf_path(str(outside), artifacts)

    assert path == outside.resolve()
    assert was_fallback is False
    assert message is not None
    assert "escapes the output directory" in message


def test_resolve_pdf_path_rejects_relative_traversal(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")

    path, was_fallback, message = resolve_pdf_path("../../outside.pdf", artifacts)

    assert path == outside.resolve()
    assert was_fallback is False
    assert message is not None
    assert "escapes the output directory" in message


def test_resolve_pdf_path_fallback_stays_inside_artifacts(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    fallback = artifacts / "fallback.pdf"
    fallback.write_bytes(b"%PDF-1.4\n")

    path, was_fallback, message = resolve_pdf_path("missing.pdf", artifacts)

    assert path == fallback.resolve()
    assert was_fallback is True
    assert message == "Using most recent PDF: fallback.pdf"


@pytest.mark.asyncio
async def test_pdf_qa_rejects_existing_pdf_outside_output_root(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    tool = PdfQATool(artifacts_dir=artifacts)

    result = await tool.execute({"path": str(outside), "question": "What is this?"})

    assert result.success is False
    assert result.error is not None
    assert "escapes the output directory" in result.error


@pytest.mark.asyncio
async def test_pdf_extract_text_rejects_existing_pdf_outside_output_root(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    tool = PdfExtractTextTool(artifacts_dir=artifacts)

    result = await tool.execute({"path": str(outside)})

    assert result.success is False
    assert result.error is not None
    assert "escapes the output directory" in result.error


@pytest.mark.asyncio
async def test_pdf_extract_text_resolves_nested_artifacts_relative_path(tmp_path, monkeypatch):
    artifacts = _artifacts_dir(tmp_path)
    pdf = artifacts / "papers" / "paper.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4\n")
    seen: dict[str, str] = {}

    def fake_extract_text(path: str) -> str:
        seen["path"] = path
        return "ok"

    monkeypatch.setattr(pdf_tools, "extract_text", fake_extract_text)
    tool = PdfExtractTextTool(artifacts_dir=artifacts)

    result = await tool.execute({"path": "papers/paper.pdf"})

    assert result.success is True
    assert result.data == {"text": "ok"}
    assert seen["path"] == str(pdf.resolve())


@pytest.mark.asyncio
async def test_pdf_parse_rejects_output_dir_escape(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    pdf = artifacts / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    tool = PdfParseTool(artifacts_dir=artifacts)

    result = await tool.execute({"path": "paper.pdf", "output_dir": "../../outside"})

    assert result.success is False
    assert result.error is not None
    assert "output_dir escapes the output directory" in result.error


def test_get_pdf_extract_dir_groups_under_documents_and_isolates_sources(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    first = artifacts / "downloads" / "paper.pdf"
    second = artifacts / "downloads" / "other.pdf"
    first.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    assert get_pdf_extract_dir(artifacts) == artifacts / "documents" / "default"
    assert get_pdf_extract_dir(artifacts, first).parent == artifacts / "documents"
    assert get_pdf_extract_dir(artifacts, first).name.startswith("paper-")
    assert get_pdf_extract_dir(artifacts, first) != get_pdf_extract_dir(artifacts, second)


def test_resolve_parse_output_dir_defaults_to_pdf_subdir(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    # No resolved source yet → use the categorized compatibility directory.
    resolved = pdf_tools._resolve_parse_output_dir(None, artifacts)
    assert resolved == (artifacts / "documents" / "default").resolve()


@pytest.mark.asyncio
async def test_pdf_mining_tool_rejects_existing_pdf_outside_output_root(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    tool = ExtractMetricsTool(artifacts_dir=artifacts)

    result = await tool.execute({"path": str(outside)})

    assert result.success is False
    assert result.error is not None
    assert "escapes the output directory" in result.error


class TestGetArtifactsAndOutputDir:
    def test_get_artifacts_dir_from_path(self, tmp_path):
        assert get_artifacts_dir(tmp_path / "a") == (tmp_path / "a").resolve()

    def test_get_artifacts_dir_from_config(self):
        cfg = AgentConfig(_env_file=None)
        assert get_artifacts_dir(cfg) == cfg.artifacts_dir.resolve()

    def test_get_artifacts_dir_default(self):
        # None → constructs a default AgentConfig internally.
        assert get_artifacts_dir(None).name == "artifacts"

    def test_get_output_dir_from_path(self, tmp_path):
        assert get_output_dir(tmp_path / "o") == (tmp_path / "o").resolve()

    def test_get_output_dir_from_config(self):
        cfg = AgentConfig(_env_file=None)
        assert get_output_dir(cfg) == cfg.output_dir.resolve()

    def test_get_output_dir_default(self):
        assert isinstance(get_output_dir(None), Path)

    def test_get_run_layout_uses_exact_output_root(self, tmp_path):
        cfg = AgentConfig(_env_file=None, output_dir=tmp_path / "run")
        layout = get_run_layout(cfg)
        assert layout.root == cfg.output_dir
        assert layout.artifacts_dir == cfg.artifacts_dir

    def test_pdf_fallback_searches_current_download_category(self, tmp_path):
        artifacts = _artifacts_dir(tmp_path)
        downloaded = artifacts / "downloads" / "paper.pdf"
        downloaded.parent.mkdir()
        downloaded.write_bytes(b"%PDF")
        assert _find_most_recent_pdf(artifacts) == downloaded


class TestResolveFilePath:
    def test_absolute_inside_root_kept(self, tmp_path):
        artifacts = _artifacts_dir(tmp_path)
        target = artifacts / "x.png"
        target.write_bytes(b"x")
        assert resolve_file_path(str(target), artifacts) == target.resolve()

    def test_relative_found_in_artifacts(self, tmp_path):
        artifacts = _artifacts_dir(tmp_path)
        (artifacts / "y.png").write_bytes(b"y")
        assert resolve_file_path("y.png", artifacts) == (artifacts / "y.png").resolve()

    def test_found_in_images_subdir(self, tmp_path):
        artifacts = _artifacts_dir(tmp_path)
        img_dir = artifacts / "pdf" / "images"
        img_dir.mkdir(parents=True)
        (img_dir / "fig.png").write_bytes(b"z")
        assert resolve_file_path("fig.png", artifacts) == (img_dir / "fig.png").resolve()

    def test_escape_raises(self, tmp_path):
        artifacts = _artifacts_dir(tmp_path)
        with pytest.raises(ValueError, match="escapes the output directory"):
            resolve_file_path("/etc/passwd", artifacts)


class TestFindMostRecentPdf:
    def test_returns_none_when_dir_missing(self, tmp_path):
        assert _find_most_recent_pdf(tmp_path / "missing") is None

    def test_returns_none_when_no_pdfs(self, tmp_path):
        artifacts = _artifacts_dir(tmp_path)
        assert _find_most_recent_pdf(artifacts) is None

    def test_picks_most_recent_excluding(self, tmp_path):
        import os
        import time

        artifacts = _artifacts_dir(tmp_path)
        old = artifacts / "old.pdf"
        new = artifacts / "new.pdf"
        old.write_bytes(b"%PDF")
        new.write_bytes(b"%PDF")
        now = time.time()
        os.utime(old, (now - 100, now - 100))
        os.utime(new, (now, now))
        assert _find_most_recent_pdf(artifacts) == new
        # Excluding the newest returns the older one.
        assert _find_most_recent_pdf(artifacts, excluded_path=new.resolve()) == old

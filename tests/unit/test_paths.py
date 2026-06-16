"""Tests for artifact path resolution safeguards."""

from __future__ import annotations

from pathlib import Path

import pytest

from webagent.tools.builtin import pdf_tools
from webagent.tools.builtin.pdf_mining_tools import ExtractMetricsTool
from webagent.tools.builtin.pdf_qa_tools import PdfQATool
from webagent.tools.builtin.pdf_tools import PdfExtractTextTool, PdfParseTool
from webagent.utils.paths import get_pdf_extract_dir, resolve_pdf_path


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


def test_get_pdf_extract_dir_groups_under_pdf_subdir(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    assert get_pdf_extract_dir(artifacts) == artifacts / "pdf"


def test_resolve_parse_output_dir_defaults_to_pdf_subdir(tmp_path):
    artifacts = _artifacts_dir(tmp_path)
    # No explicit output_dir → API-extracted content is grouped under pdf/.
    resolved = pdf_tools._resolve_parse_output_dir(None, artifacts)
    assert resolved == (artifacts / "pdf").resolve()


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

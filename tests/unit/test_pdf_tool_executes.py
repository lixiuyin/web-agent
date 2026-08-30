"""End-to-end execute() tests for the PDF mining and Q&A tools.

Parse results are injected through the shared ``pdf_result_cache`` so the full
tool pipeline (path resolution → cached load → analysis → ToolResult) runs
without network or cloud OCR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from webagent.parser.models import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
)
from webagent.tools.builtin._pdf_common import pdf_cache_key, pdf_result_cache
from webagent.tools.builtin.pdf_mining_tools import (
    CompareEntitiesTool,
    ExtractCitationsTool,
    ExtractMetricsTool,
    ExtractTableDataTool,
    ExtractTopicsTool,
    FindMentionsTool,
    GetHierarchyTool,
    GetMetadataTool,
    GetSectionTool,
    SummarizeSectionsTool,
)
from webagent.tools.builtin.pdf_qa_tools import (
    PdfAnalyzeFigureTool,
    PdfListFiguresTool,
    PdfListSectionsTool,
    PdfListTablesTool,
    PdfQATool,
    PdfSearchTool,
)


def _fixture_result() -> PDFParseResult:
    r = PDFParseResult(markdown_path=None, json_path=None, images_dir="i", output_dir="o")
    r.text_blocks.extend(
        [
            TextBlock("Attention Is All You Need", 0, (0, 0, 0, 0), level=1),
            TextBlock("Ashish Vaswani, Noam Shazeer", 0, (0, 0, 0, 0)),
            TextBlock(
                "Abstract: We present the Transformer. Our accuracy: 88.4 "
                "outperforms LSTM on translation tasks.",
                0,
                (0, 0, 0, 0),
            ),
            TextBlock("As shown in Figure 1 the model works.", 1, (0, 0, 0, 0)),
        ]
    )
    r.images.append(
        ImageInfo(
            "/nonexistent/fig1.png",
            0,
            (0, 0, 0, 0),
            caption="Model architecture",
            figure_number="1",
        )
    )
    r.tables.append(
        TableInfo(
            "/nonexistent/t1.html",
            1,
            (0, 0, 0, 0),
            caption="BLEU scores",
            table_number="1",
            html_body="<table><tr><th>Model</th></tr><tr><td>LSTM</td></tr></table>",
        )
    )
    r.sections.update(
        {
            "1:Introduction": [TextBlock("Intro paragraph.", 0, (0, 0, 0, 0), level=1)],
            "2:Method": [TextBlock("Method details.", 1, (0, 0, 0, 0), level=2)],
        }
    )
    return r


@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    """A dummy PDF whose parse result is pre-seeded into the cache."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    pdf_result_cache[pdf_cache_key(pdf, tmp_path)] = _fixture_result()
    return pdf


def _params(pdf: Path, **extra: Any) -> dict[str, Any]:
    return {"path": str(pdf), **extra}


class TestMiningTools:
    async def test_extract_table_data(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = ExtractTableDataTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, table_number="1"))
        assert result.success
        assert result.data["headers"] == ["Model"]
        assert result.data["rows"] == [["LSTM"]]

    async def test_extract_table_data_query_filter(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = ExtractTableDataTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, table_number="1", query="lstm"))
        assert result.success
        assert result.data["match_count"] == 1

    async def test_extract_table_data_not_found(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = ExtractTableDataTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, table_number="9"))
        assert result.success and result.data["found"] is False

    async def test_find_mentions_figure(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = FindMentionsTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, type="figure", number="1"))
        assert result.success and result.data["mention_count"] == 1

    async def test_find_mentions_table(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = FindMentionsTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, type="table", number="1"))
        assert result.success

    async def test_get_section(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = GetSectionTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, section_title="Introduction"))
        assert result.success and "Intro paragraph." in result.data["content"]

    async def test_get_hierarchy(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await GetHierarchyTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success and result.data["total_sections"] == 2

    async def test_get_metadata(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await GetMetadataTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success
        assert result.data["title"] == "Attention Is All You Need"
        assert result.data["authors"] == ["Ashish Vaswani", "Noam Shazeer"]

    async def test_extract_metrics(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await ExtractMetricsTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success
        names = {m["metric"] for m in result.data["performance_metrics"]}
        assert "accuracy" in names

    async def test_extract_topics(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await ExtractTopicsTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success and result.data["top_keywords"]

    async def test_extract_citations(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await ExtractCitationsTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success
        assert "citations" in result.data

    async def test_summarize_sections(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await SummarizeSectionsTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success and result.data["section_count"] == 2

    async def test_compare_entities(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await CompareEntitiesTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success
        assert result.data["entity_count"] >= 0

    async def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        tool = GetMetadataTool(artifacts_dir=tmp_path)
        result = await tool.execute({"path": str(tmp_path / "nope.pdf")})
        assert not result.success


class TestQaTools:
    async def test_pdf_qa(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = PdfQATool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, question="What about the Transformer?"))
        assert result.success
        assert result.data["context"]
        assert "Transformer" in result.data["context"] or result.data["context"] == ""

    async def test_pdf_search(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = PdfSearchTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, query="accuracy translation"))
        assert result.success
        assert "results" in result.data

    async def test_pdf_list_figures(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await PdfListFiguresTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success
        assert result.data["total_figures"] == 1
        assert result.data["figures"][0]["figure_number"] == "1"

    async def test_pdf_list_tables(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await PdfListTablesTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success and result.data["total_tables"] == 1

    async def test_pdf_list_sections(self, pdf_file: Path, tmp_path: Path) -> None:
        result = await PdfListSectionsTool(artifacts_dir=tmp_path).execute(_params(pdf_file))
        assert result.success and result.data["total_sections"] == 2

    async def test_pdf_analyze_figure_number_resolution(
        self, pdf_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Figure file does not exist in the fixture; execute should fail cleanly.
        tool = PdfAnalyzeFigureTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, figure_number_or_caption="1"))
        assert not result.success  # image file missing -> error
        assert "not found" in result.error or "Image" in result.error

    async def test_pdf_analyze_figure_not_resolved(self, pdf_file: Path, tmp_path: Path) -> None:
        tool = PdfAnalyzeFigureTool(artifacts_dir=tmp_path)
        result = await tool.execute(_params(pdf_file, figure_number_or_caption="99"))
        assert result.success and result.data["found"] is False

    async def test_validation_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            await PdfQATool(artifacts_dir=tmp_path).validate_params({"path": ""})
        with pytest.raises(ValueError):
            PdfListTablesTool(artifacts_dir=tmp_path).validate_params({})
        with pytest.raises(ValueError):
            FindMentionsTool(artifacts_dir=tmp_path).validate_params(
                {"path": "x", "type": "other", "number": "1"}
            )

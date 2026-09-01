"""Branch tests for ``pdf_mining_tools``: validation, path/parse errors and
several data-shaping branches not exercised by the happy-path execute tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from webagent.parser.models import PDFParseResult, TextBlock
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


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


_PATH_ONLY_TOOLS = [
    GetHierarchyTool,
    ExtractMetricsTool,
    ExtractTopicsTool,
    ExtractCitationsTool,
    SummarizeSectionsTool,
    CompareEntitiesTool,
]


class TestValidation:
    @pytest.mark.parametrize("cls", _PATH_ONLY_TOOLS)
    def test_path_required(self, cls: Any, artifacts_dir: Path) -> None:
        with pytest.raises(ValueError):
            cls(artifacts_dir=artifacts_dir).validate_params({"path": " "})

    def test_table_data_requires_table_number(self, artifacts_dir: Path) -> None:
        tool = ExtractTableDataTool(artifacts_dir=artifacts_dir)
        with pytest.raises(ValueError):
            tool.validate_params({"path": "x"})

    def test_get_section_requires_title(self, artifacts_dir: Path) -> None:
        with pytest.raises(ValueError):
            GetSectionTool(artifacts_dir=artifacts_dir).validate_params({"path": "x"})

    def test_find_mentions_type_and_number(self, artifacts_dir: Path) -> None:
        tool = FindMentionsTool(artifacts_dir=artifacts_dir)
        with pytest.raises(ValueError):
            tool.validate_params({"path": "x", "type": "bogus", "number": "1"})
        with pytest.raises(ValueError):
            tool.validate_params({"path": "x", "type": "figure"})


class TestPathErrors:
    @pytest.mark.parametrize("cls", _PATH_ONLY_TOOLS)
    async def test_missing_pdf_returns_error(self, cls: Any, artifacts_dir: Path) -> None:
        result = await cls(artifacts_dir=artifacts_dir).execute({"path": "missing.pdf"})
        assert not result.success

    async def test_metadata_url_rejected(self, artifacts_dir: Path) -> None:
        tool = GetMetadataTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": "https://example.com/a.pdf"})
        assert not result.success

    async def test_metadata_missing_file(self, artifacts_dir: Path) -> None:
        tool = GetMetadataTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": "nope.pdf"})
        assert not result.success


def _seed(artifacts_dir: Path, result: PDFParseResult) -> Path:
    pdf = artifacts_dir / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")
    pdf_result_cache[pdf_cache_key(pdf, artifacts_dir)] = result
    return pdf


def _result_with_text(text: str) -> PDFParseResult:
    r = PDFParseResult(
        markdown_path=None, json_path=None, images_dir="i", output_dir="o", backend="marker"
    )
    r.text_blocks.append(TextBlock(text, 0, (0, 0, 0, 0)))
    return r


class TestCompareEntities:
    async def test_finds_comparisons_and_filters(self, artifacts_dir: Path) -> None:
        text = "Transformer outperforms LSTM on translation. BERT versus GPT was also studied."
        pdf = _seed(artifacts_dir, _result_with_text(text))
        tool = CompareEntitiesTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf)})
        assert result.success
        assert result.data["comparison_count"] >= 1

    async def test_entity_filter_narrows_results(self, artifacts_dir: Path) -> None:
        text = "Transformer outperforms LSTM. BERT surpasses GPT."
        pdf = _seed(artifacts_dir, _result_with_text(text))
        tool = CompareEntitiesTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf), "entity": "Transformer"})
        assert result.success
        assert result.data["entity_filter"] == "Transformer"
        for c in result.data["comparisons"]:
            assert "transformer" in (c["entity_a"] + c["entity_b"]).lower()


class TestSummarizeSections:
    async def test_excludes_deep_levels(self, artifacts_dir: Path) -> None:
        r = _result_with_text("body")
        r.sections["1:Intro"] = [TextBlock("Intro body text.", 0, (0, 0, 0, 0), level=1)]
        r.sections["3:Deep"] = [TextBlock("deep", 2, (0, 0, 0, 0), level=3)]
        pdf = _seed(artifacts_dir, r)
        tool = SummarizeSectionsTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf)})
        assert result.success
        titles = {s["title"] for s in result.data["summaries"]}
        assert "Intro" in titles and "Deep" not in titles


class TestFindMentions:
    async def test_table_mentions(self, artifacts_dir: Path) -> None:
        r = _result_with_text("As shown in Table 2, the results are strong.")
        pdf = _seed(artifacts_dir, r)
        tool = FindMentionsTool(artifacts_dir=artifacts_dir)
        result = await tool.execute({"path": str(pdf), "type": "table", "number": "2"})
        assert result.success

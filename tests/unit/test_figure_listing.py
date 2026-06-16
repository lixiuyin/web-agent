"""Tests for figure listing/selection: real numbered figures must not be confused
with uncaptioned logos/decorations (regression: a cover logo was mistaken for
'Figure 1')."""

from __future__ import annotations

from pathlib import Path

import pytest

from webagent.parser.models import ImageInfo, PDFParseResult
from webagent.tools.builtin import pdf_qa_tools
from webagent.tools.builtin.pdf_qa_tools import (
    PdfListFiguresTool,
    _figure_sort_key,
    _get_cache_key,
)


def test_figure_sort_key_orders_numerically_then_by_letter():
    keys = ["10", "2", "1", "3a", "3b"]
    assert sorted(keys, key=_figure_sort_key) == ["1", "2", "3a", "3b", "10"]


def _seed_result(artifacts: Path) -> tuple[Path, PDFParseResult]:
    images_dir = artifacts / "pdf" / "images"
    images_dir.mkdir(parents=True)
    pdf = artifacts / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    def _img(name: str) -> str:
        p = images_dir / name
        p.write_bytes(b"\xff\xd8\xff")  # minimal jpeg-ish bytes
        return str(p)

    result = PDFParseResult(
        markdown_path=None,
        json_path=None,
        images_dir=str(images_dir),
        output_dir=str(artifacts / "pdf"),
        backend="marker",
    )
    # Two uncaptioned page-1 logos, then Figure 2, then Figure 1 (out of order).
    result.images = [
        ImageInfo(path=_img("logo_a.jpg"), page_idx=0, bbox=(0, 0, 0, 0)),
        ImageInfo(path=_img("logo_b.jpg"), page_idx=0, bbox=(0, 0, 0, 0)),
        ImageInfo(
            path=_img("fig2.jpg"),
            page_idx=2,
            bbox=(0, 0, 0, 0),
            caption="Figure 2: architecture overview.",
            figure_number="2",
        ),
        ImageInfo(
            path=_img("fig1.jpg"),
            page_idx=1,
            bbox=(0, 0, 0, 0),
            caption="Figure 1: model capabilities.",
            figure_number="1",
        ),
    ]
    return pdf, result


@pytest.mark.asyncio
async def test_pdf_list_figures_separates_logos_and_sorts_by_number(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    pdf, result = _seed_result(artifacts)
    pdf_qa_tools._pdf_cache[_get_cache_key(pdf.resolve())] = result

    tool = PdfListFiguresTool(artifacts_dir=artifacts)
    res = await tool.execute({"path": "paper.pdf"})

    assert res.success is True
    # Only the two captioned figures count as figures, sorted Figure 1 then 2.
    assert res.data["total_figures"] == 2
    assert [f["figure_number"] for f in res.data["figures"]] == ["1", "2"]
    assert res.data["figures"][0]["caption"].startswith("Figure 1:")
    assert res.data["figures"][0]["path"].endswith("fig1.jpg")
    # The logos are kept separately and never labelled as numbered figures.
    assert res.data["unlabeled_image_count"] == 2
    assert all("figure_number" not in img for img in res.data["unlabeled_images"])

    pdf_qa_tools._pdf_cache.clear()


@pytest.mark.asyncio
async def test_pdf_analyze_figure_resolves_number_not_extraction_order(tmp_path):
    artifacts = tmp_path / "outputs" / "artifacts"
    artifacts.mkdir(parents=True)
    pdf, result = _seed_result(artifacts)
    pdf_qa_tools._pdf_cache[_get_cache_key(pdf.resolve())] = result

    class _StubPlanner:
        vision_actually_works = True

        async def analyze_image(self, img, question):
            return "This figure depicts the model capabilities across modalities " * 3

    from webagent.tools.builtin.pdf_qa_tools import PdfAnalyzeFigureTool

    tool = PdfAnalyzeFigureTool(artifacts_dir=artifacts, planner=_StubPlanner())
    # "Figure 1" must resolve to fig1.jpg, NOT the first extracted image (a logo).
    res = await tool.execute({"path": "paper.pdf", "figure_number_or_caption": "Figure 1"})

    assert res.success is True
    assert res.data["found"] is True
    assert res.data["figure_number"] == "1"
    assert res.data["image_path"].endswith("fig1.jpg")

    pdf_qa_tools._pdf_cache.clear()

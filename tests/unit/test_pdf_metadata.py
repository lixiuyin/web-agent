"""Regression tests for paper-metadata author extraction."""

from __future__ import annotations

from webagent.parser.models import PDFParseResult, TextBlock
from webagent.tools.builtin.pdf_mining_tools import extract_paper_metadata


def _result(blocks: list[TextBlock]) -> PDFParseResult:
    r = PDFParseResult(markdown_path=None, json_path=None, images_dir="i", output_dir="o")
    r.text_blocks.extend(blocks)
    return r


def test_sentence_with_comma_is_not_mistaken_for_authors():
    # Prose containing a comma must NOT become an author list (the original bug).
    blocks = [
        TextBlock("Quarterly Report", 0, (0, 0, 0, 0), level=1, block_type="title"),
        TextBlock("Revenue grew 18% year over year, led by the APAC segment.", 0, (0, 0, 0, 0)),
    ]
    md = extract_paper_metadata(_result(blocks))
    assert md["title"] == "Quarterly Report"
    assert md["authors"] == []


def test_real_author_line_is_extracted():
    blocks = [
        TextBlock("Attention Is All You Need", 0, (0, 0, 0, 0), level=1, block_type="title"),
        TextBlock("Ashish Vaswani, Noam Shazeer, Niki Parmar", 0, (0, 0, 0, 0)),
    ]
    md = extract_paper_metadata(_result(blocks))
    assert md["authors"] == ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"]

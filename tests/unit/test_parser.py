"""Unit tests for the cloud document parser cascade."""

from __future__ import annotations

import pytest

from webagent.core.config import AgentConfig
from webagent.parser import (
    PDFParseResult,
    find_images_by_keyword,
    find_tables_by_keyword,
    parse_pdf,
    select_parsers,
)
from webagent.parser._build import build_from_page_texts, image_captions_from_pages
from webagent.parser._profile import DocumentProfile, profile_document
from webagent.parser._quality import assess_quality
from webagent.parser.providers.mineru import MinerUAPIParser

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _profile(suffix: str, avg_chars: float, pages: int = 3) -> DocumentProfile:
    return DocumentProfile(
        suffix=suffix,
        page_count=pages,
        avg_chars_per_page=avg_chars,
        image_ratio=0.0,
        has_text_layer=avg_chars > 0,
        is_likely_scanned=avg_chars < 50,
        size_bytes=1000,
    )


# ── Router ──────────────────────────────────────────────────────────────────


def test_router_text_pdf_prefers_marker():
    assert select_parsers(_profile(".pdf", 500))[0] == "marker"


def test_router_scanned_pdf_prefers_mineru():
    assert select_parsers(_profile(".pdf", 5))[0] == "mineru"


def test_router_image_prefers_paddle_and_drops_mineru():
    order = select_parsers(_profile(".png", 0, pages=1))
    assert order[0] == "paddle"
    assert "mineru" not in order


def test_router_hint_promotes_provider_to_primary():
    order = select_parsers(_profile(".pdf", 500), user_hint="paddle")
    assert order[0] == "paddle"


def test_router_ignores_unknown_hint():
    assert select_parsers(_profile(".pdf", 500), user_hint="bogus")[0] == "marker"


# ── Markdown → structured builder ───────────────────────────────────────────


def _empty_result() -> PDFParseResult:
    return PDFParseResult(markdown_path=None, json_path=None, images_dir="x", output_dir="y")


def test_build_from_markdown_extracts_headings_and_sections():
    result = _empty_result()
    build_from_page_texts(result, ["# Intro\n\nHello world.\n\n## Methods\n\nWe did things."])
    titles = [b.text for b in result.text_blocks if b.block_type == "title"]
    assert titles == ["Intro", "Methods"]
    # Section keyed by "level:title" holds the following paragraph.
    assert any("Methods" in k for k in result.sections)


def test_build_from_markdown_extracts_table():
    result = _empty_result()
    md = "See Table 1 below.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"
    build_from_page_texts(result, [md])
    assert len(result.tables) == 1
    assert result.tables[0].table_number == "1"
    # html_body must be HTML (not raw markdown) so the downstream HTML parser works.
    html = result.tables[0].html_body
    assert "<table>" in html and "<th>a</th>" in html
    # Round-trip through the actual downstream consumer (rank 2 regression).
    from webagent.tools.builtin.pdf_mining_tools import parse_table_html

    parsed = parse_table_html(html)
    assert parsed["headers"] == ["a", "b"]
    assert parsed["rows"] == [["1", "2"]]


def test_degraded_results_are_not_cached():
    """Local/error results must not poison the cross-run PDF cache (rank 12)."""
    from webagent.tools.builtin.pdf_qa_tools import _PdfResultCache

    cache = _PdfResultCache()
    cloud = PDFParseResult(None, None, "i", "o", backend="mineru")
    local = PDFParseResult(None, None, "i", "o", backend="pymupdf")
    errored = PDFParseResult(None, None, "i", "o", backend="marker", error="boom")
    cache["a"] = cloud
    cache["b"] = local
    cache["c"] = errored
    assert "a" in cache  # cloud result cached
    assert "b" not in cache  # local fallback not cached
    assert "c" not in cache  # errored result not cached


# ── MinerU content_list mapping ─────────────────────────────────────────────


def test_mineru_content_list_mapping(tmp_path):
    parser = MinerUAPIParser()
    config = AgentConfig()
    from webagent.parser._request import ParseRequest

    req = ParseRequest(
        file_path=tmp_path / "x.pdf",
        profile=_profile(".pdf", 100),
        output_dir=tmp_path,
        images_dir=tmp_path / "images",
        config=config,
    )
    result = _empty_result()
    content_list = [
        {"type": "text", "text": "Title Here", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Body paragraph.", "page_idx": 0},
        {"type": "equation", "text": "E=mc^2", "page_idx": 0},
        {
            "type": "image",
            "img_path": "images/fig.jpg",
            "img_caption": ["Figure 2: a plot"],
            "page_idx": 1,
        },
        {
            "type": "table",
            "img_path": "images/tab.jpg",
            "table_caption": ["Table 3: results"],
            "table_body": "<table><tr><td>x</td></tr></table>",
            "page_idx": 1,
        },
    ]
    parser._map_content_list(result, content_list, req)

    assert [b.block_type for b in result.text_blocks] == ["title", "paragraph", "formula"]
    assert len(result.images) == 1
    assert result.images[0].figure_number == "2"
    assert result.images[0].path.endswith("fig.jpg")
    assert len(result.tables) == 1
    assert result.tables[0].table_number == "3"
    assert "<table>" in result.tables[0].html_body
    # find helpers operate on the populated result
    assert find_images_by_keyword(result, "plot")
    assert find_tables_by_keyword(result, "results")


# ── Marker image ↔ caption association ──────────────────────────────────────


def test_image_caption_from_alt_text():
    # Marker often embeds the caption directly in the image alt text.
    pages = [
        "intro text\n\n"
        "![Figure 1: Qwen capabilities. Two teddy bears in a room.](2173c010_img.jpg)\n\n"
        "more text"
    ]
    mapping = image_captions_from_pages(pages)
    page_idx, caption = mapping["2173c010_img.jpg"]
    assert page_idx == 0
    assert caption.startswith("Figure 1:")


def test_image_caption_from_nearby_line():
    # When the alt text is just a description, the caption is the nearest
    # "Figure N:" line on the page (e.g. the Transformer architecture diagram).
    pages = [
        "page zero, no images",
        "{2}----\n\n"
        "![Diagram of the Transformer model architecture.](b230b8f2_img.jpg)\n\n"
        "Figure 1: The Transformer - model architecture.\n\n"
        "The Transformer follows this overall architecture.",
    ]
    mapping = image_captions_from_pages(pages)
    page_idx, caption = mapping["b230b8f2_img.jpg"]
    assert page_idx == 1  # second page → 0-indexed 1
    assert caption == "Figure 1: The Transformer - model architecture."


def test_image_caption_picks_nearest_of_multiple_figures():
    pages = [
        "![alt one](figA.jpg)\n\nFigure 3: first caption.\n\n"
        "Figure 4: second caption.\n\n![alt two](figB.jpg)"
    ]
    mapping = image_captions_from_pages(pages)
    assert mapping["figA.jpg"][1].startswith("Figure 3:")
    assert mapping["figB.jpg"][1].startswith("Figure 4:")


def test_image_without_caption_maps_to_empty():
    mapping = image_captions_from_pages(["![logo](brand.png)\n\njust prose, no figures here"])
    assert mapping["brand.png"] == (0, "")


# ── Quality gate ────────────────────────────────────────────────────────────


def test_quality_rejects_empty_text_for_text_pdf():
    result = _empty_result()
    q = assess_quality(result, _profile(".pdf", 500))
    assert not q.is_satisfactory
    assert "empty_text" in q.reasons[0]


def test_quality_accepts_good_text():
    result = _empty_result()
    build_from_page_texts(result, ["This is a perfectly good paragraph of extracted text." * 5])
    assert assess_quality(result, _profile(".pdf", 500)).is_satisfactory


def test_quality_propagates_provider_error():
    result = _empty_result()
    result.error = "boom"
    assert not assess_quality(result, _profile(".pdf", 500)).is_satisfactory


# ── End-to-end cascade via local fallback (no network) ──────────────────────


def _make_pdf(path, text: str) -> None:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_cascade_falls_back_to_local_when_cloud_unconfigured(tmp_path):
    pdf = tmp_path / "sample.pdf"
    _make_pdf(pdf, "Hello from the local fallback path.")

    # No cloud keys configured → every cloud provider raises NOT_CONFIGURED,
    # so the cascade must use the local PyMuPDF extractor.
    config = AgentConfig(
        marker_api_key="", mineru_api_key="", paddleocr_api_key="", paddleocr_base_url=""
    )
    result = parse_pdf(pdf, tmp_path / "out", config=config)

    assert result.error is None
    assert result.backend == "pymupdf"
    assert any("Hello from the local fallback" in b.text for b in result.text_blocks)
    assert result.markdown_path is not None


def test_cascade_reports_error_for_missing_file(tmp_path):
    result = parse_pdf(tmp_path / "does_not_exist.pdf", tmp_path, config=AgentConfig())
    assert result.error is not None


def test_profile_document_on_real_pdf(tmp_path):
    pdf = tmp_path / "p.pdf"
    _make_pdf(pdf, "Some text content here for profiling.")
    profile = profile_document(pdf)
    assert profile.suffix == ".pdf"
    assert profile.page_count == 1
    assert profile.has_text_layer


def test_provider_error_message_uses_type_when_cause_blank():
    """A blank-stringed cause (e.g. httpx.ConnectError('')) must stay diagnosable."""
    from webagent.parser._errors import ParserProviderError

    class _Blank(Exception):
        def __str__(self) -> str:
            return ""

    err = ParserProviderError(provider="mineru", cause=_Blank())
    assert "mineru" in str(err)
    assert "_Blank" in str(err)  # type name surfaces instead of empty detail

"""Tests for the parser output quality heuristics."""

from __future__ import annotations

from webagent.parser._profile import DocumentProfile
from webagent.parser._quality import assess_quality, result_text
from webagent.parser.models import PDFParseResult, TableInfo, TextBlock


def _profile(
    *,
    scanned: bool = False,
    pages: int = 1,
    avg_chars: float = 2000.0,
) -> DocumentProfile:
    return DocumentProfile(
        suffix=".pdf",
        page_count=pages,
        avg_chars_per_page=avg_chars,
        image_ratio=0.0,
        has_text_layer=True,
        is_likely_scanned=scanned,
        size_bytes=1000,
    )


def _result(text: str = "", *, error: str | None = None, tables: bool = False) -> PDFParseResult:
    r = PDFParseResult(
        markdown_path=None, json_path=None, images_dir="i", output_dir="o", error=error
    )
    if text:
        r.text_blocks.append(TextBlock(text, 0, (0, 0, 0, 0)))
    if tables:
        r.tables.append(TableInfo("/t.html", 0, (0, 0, 0, 0)))
    return r


def test_result_text_concatenates_blocks():
    r = _result()
    r.text_blocks.extend([TextBlock("a", 0, (0, 0, 0, 0)), TextBlock("b", 0, (0, 0, 0, 0))])
    assert result_text(r) == "a\nb"


def test_provider_error_fails():
    q = assess_quality(_result(error="boom"), _profile())
    assert not q.is_satisfactory and q.score == 0.0


def test_empty_text_fails():
    q = assess_quality(_result(""), _profile())
    assert not q.is_satisfactory
    assert "empty_text" in q.reasons


def test_empty_but_has_assets_ok():
    # Low expected volume so the text-ratio check is skipped; assets keep it valid.
    q = assess_quality(_result("", tables=True), _profile(pages=1, avg_chars=10.0))
    assert q.is_satisfactory


def test_scanned_short_text_fails():
    q = assess_quality(_result("x"), _profile(scanned=True))
    assert not q.is_satisfactory
    assert any("scanned_text_too_short" in r for r in q.reasons)


def test_scanned_with_enough_text_ok():
    q = assess_quality(_result("y" * 200), _profile(scanned=True))
    assert q.is_satisfactory and q.score == 0.8


def test_text_too_short_ratio():
    # expected = 2000 * 5 pages = 10000; give far less text.
    q = assess_quality(_result("short text"), _profile(pages=5, avg_chars=2000.0))
    assert not q.is_satisfactory
    assert any("text_too_short" in r for r in q.reasons)


def test_high_control_chars():
    text = "valid words here " * 5 + "\x00" * 20
    q = assess_quality(_result(text), _profile(pages=1, avg_chars=10.0))
    assert any("high_control_chars" in r for r in q.reasons)


def test_good_document_passes():
    q = assess_quality(
        _result("This is a normal paragraph of readable text."), _profile(pages=1, avg_chars=10.0)
    )
    assert q.is_satisfactory

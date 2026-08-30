"""Tests for conservative local PDF figure detection and rendering."""

from __future__ import annotations

from pathlib import Path

import fitz  # type: ignore[import-untyped]
from benchmarks.suites.document_figures.fast_path import build_benchmark_corpus, run_benchmark

from webagent.utils.pdf_figures import (
    detect_and_render_local_figure,
    detect_local_figure_regions,
)


def test_multi_document_benchmark_is_exact(tmp_path: Path) -> None:
    result = run_benchmark(tmp_path / "benchmark")

    assert result["documents"] == 10
    assert result["expected_figures"] == 9
    assert result["metrics"]["precision"] == 1.0
    assert result["metrics"]["recall"] == 1.0
    assert result["metrics"]["eligible_precision"] == 1.0
    assert result["metrics"]["false_bypass"] == 0
    assert result["metrics"]["fast_path_coverage"] >= 0.7
    assert result["metrics"]["render_success_rate"] == 1.0


def test_exact_number_renders_one_unambiguous_figure(tmp_path: Path) -> None:
    corpus = build_benchmark_corpus(tmp_path / "corpus")
    document = next(item for item in corpus if item.name == "vector_caption_below")

    rendered = detect_and_render_local_figure(
        document.path,
        "1",
        tmp_path / "renders",
        min_confidence=0.9,
    )

    assert rendered is not None
    assert rendered.region.figure_number == "1"
    assert rendered.region.visual_kind == "vector"
    assert rendered.width >= 700
    assert rendered.height >= 400


def test_duplicate_figure_numbers_are_ambiguous(tmp_path: Path) -> None:
    ambiguous = tmp_path / "ambiguous.pdf"
    with fitz.open() as source:
        page = source.new_page(width=595, height=842)
        for visual, caption in (
            (fitz.Rect(70, 90, 525, 280), fitz.Rect(70, 295, 525, 340)),
            (fitz.Rect(70, 445, 525, 640), fitz.Rect(70, 655, 525, 700)),
        ):
            page.draw_rect(visual, width=1.5)
            page.draw_line(visual.top_left, visual.bottom_right, width=1.5)
            page.draw_line(visual.bottom_left, visual.top_right, width=1.5)
            page.insert_textbox(caption, "Figure 1: Duplicate-number ambiguity.", fontsize=9)
        source.save(ambiguous)

    assert len(detect_local_figure_regions(ambiguous, figure_number="1")) == 2
    assert detect_and_render_local_figure(ambiguous, "1", tmp_path / "out") is None

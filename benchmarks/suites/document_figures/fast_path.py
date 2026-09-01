"""Offline multi-document benchmark for local PDF figure rendering.

Run from the repository root:

    python -m benchmarks.suites.document_figures.fast_path
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]
from PIL import Image, ImageDraw

from benchmarks.core import allocate_execution_dir, default_study_dir
from webagent.evaluation import StudyExecutionLayout
from webagent.utils.pdf_figures import (
    FloatBBox,
    LocalFigureRegion,
    detect_local_figure_regions,
    render_local_figure,
)


@dataclass(frozen=True)
class ExpectedFigure:
    figure_number: str
    page_idx: int
    bbox: FloatBBox


@dataclass(frozen=True)
class BenchmarkDocument:
    name: str
    path: Path
    expected: tuple[ExpectedFigure, ...]


def build_benchmark_corpus(root: Path) -> list[BenchmarkDocument]:
    """Create ten deterministic PDFs covering distinct layout conditions."""
    root.mkdir(parents=True, exist_ok=True)
    return [
        _single_vector(root, "vector_caption_below", caption_above=False),
        _single_vector(root, "vector_caption_above", caption_above=True),
        _single_raster(root),
        _two_figures(root),
        _logo_interference(root),
        _fragmented_plot(root),
        _two_column(root),
        _landscape(root),
        _negative_mention(root),
        _table_only(root),
    ]


def run_benchmark(root: Path) -> dict[str, Any]:
    """Generate the corpus, detect/render figures, and return aggregate metrics."""
    corpus_dir = root / "inputs" / "corpus"
    renders_dir = root / "artifacts" / "renders"
    documents = build_benchmark_corpus(corpus_dir)
    true_positive = 0
    false_positive = 0
    false_negative = 0
    render_success = 0
    detected_total = 0
    eligible_true_positive = 0
    eligible_false_positive = 0
    expected_total = sum(len(document.expected) for document in documents)
    durations: list[float] = []
    cases: list[dict[str, Any]] = []

    for document in documents:
        started = time.perf_counter()
        detected = detect_local_figure_regions(document.path)
        detected_total += len(detected)
        detect_seconds = time.perf_counter() - started
        durations.append(detect_seconds)
        unmatched = list(document.expected)
        detections: list[dict[str, Any]] = []
        for index, region in enumerate(detected):
            matched = _match_expected(region, unmatched)
            is_true_positive = False
            if matched is None:
                false_positive += 1
                coverage = 0.0
                purity = 0.0
            else:
                coverage, purity = _coverage_and_purity(region.bbox, matched.bbox)
                if coverage >= 0.85 and purity >= 0.55:
                    true_positive += 1
                    unmatched.remove(matched)
                    is_true_positive = True
                else:
                    false_positive += 1
            fast_path_eligible = region.confidence >= 0.9
            if fast_path_eligible:
                if is_true_positive:
                    eligible_true_positive += 1
                else:
                    eligible_false_positive += 1
            rendered = render_local_figure(
                document.path,
                region,
                renders_dir / document.name,
            )
            if rendered.image_path.is_file() and rendered.width >= 100 and rendered.height >= 100:
                render_success += 1
            detections.append(
                {
                    "index": index,
                    "region": asdict(region),
                    "coverage": round(coverage, 4),
                    "purity": round(purity, 4),
                    "fast_path_eligible": fast_path_eligible,
                    "render": str(rendered.image_path),
                    "render_size": [rendered.width, rendered.height],
                }
            )
        false_negative += len(unmatched)
        cases.append(
            {
                "name": document.name,
                "pdf": str(document.path),
                "expected": [asdict(item) for item in document.expected],
                "detections": detections,
                "unmatched": [asdict(item) for item in unmatched],
                "detection_seconds": round(detect_seconds, 6),
            }
        )

    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    eligible_precision = eligible_true_positive / max(
        1, eligible_true_positive + eligible_false_positive
    )
    sorted_durations = sorted(durations)
    p95_index = max(0, min(len(sorted_durations) - 1, round(0.95 * len(sorted_durations)) - 1))
    return {
        "schema_version": 1,
        "documents": len(documents),
        "expected_figures": expected_total,
        "metrics": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "fast_path_eligible": eligible_true_positive,
            "false_bypass": eligible_false_positive,
            "eligible_precision": round(eligible_precision, 4),
            "fast_path_coverage": round(eligible_true_positive / max(1, expected_total), 4),
            "fallback_rate": round(
                (expected_total - eligible_true_positive) / max(1, expected_total),
                4,
            ),
            "render_success_rate": round(render_success / max(1, detected_total), 4),
            "mean_detection_seconds": round(statistics.fmean(durations), 6),
            "p95_detection_seconds": round(sorted_durations[p95_index], 6),
        },
        "cases": cases,
    }


def _match_expected(
    region: LocalFigureRegion,
    expected: list[ExpectedFigure],
) -> ExpectedFigure | None:
    for item in expected:
        if (
            item.figure_number.lower() == region.figure_number.lower()
            and item.page_idx == region.page_idx
        ):
            return item
    return None


def _coverage_and_purity(candidate: FloatBBox, expected: FloatBBox) -> tuple[float, float]:
    intersection = _intersection_area(candidate, expected)
    return intersection / max(1.0, _bbox_area(expected)), intersection / max(
        1.0, _bbox_area(candidate)
    )


def _intersection_area(left: FloatBBox, right: FloatBBox) -> float:
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _bbox_area(bbox: FloatBBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _new_document(
    path: Path, *, width: float = 595, height: float = 842
) -> tuple[fitz.Document, fitz.Page]:
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.insert_text((54, 42), "Offline Figure Benchmark", fontsize=13)
    return document, page


def _save(document: fitz.Document, path: Path) -> None:
    document.save(path)
    document.close()


def _draw_vector(page: fitz.Page, rect: fitz.Rect, *, border: bool = True) -> None:
    if border:
        page.draw_rect(rect, color=(0.1, 0.2, 0.7), width=1.4)
    page.draw_line((rect.x0 + 30, rect.y1 - 35), (rect.x1 - 20, rect.y1 - 35), width=1.2)
    page.draw_line((rect.x0 + 30, rect.y0 + 20), (rect.x0 + 30, rect.y1 - 35), width=1.2)
    points = [
        (rect.x0 + 35, rect.y1 - 55),
        (rect.x0 + 105, rect.y1 - 105),
        (rect.x0 + 180, rect.y1 - 75),
        (rect.x1 - 35, rect.y0 + 45),
    ]
    for start, end in pairwise(points):
        page.draw_line(start, end, color=(0.8, 0.1, 0.1), width=2)
    page.insert_text((rect.x0 + 42, rect.y0 + 28), "Vector model comparison", fontsize=10)


def _caption(page: fitz.Page, rect: fitz.Rect, text: str) -> None:
    remaining = page.insert_textbox(rect, text, fontsize=9, lineheight=1.15)
    if remaining < 0:
        raise RuntimeError(f"caption did not fit: {text}")


def _single_vector(root: Path, name: str, *, caption_above: bool) -> BenchmarkDocument:
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    visual = fitz.Rect(80, 180 if caption_above else 120, 515, 440 if caption_above else 380)
    caption_rect = fitz.Rect(80, 105 if caption_above else 395, 515, 155 if caption_above else 445)
    _draw_vector(page, visual)
    _caption(page, caption_rect, "Figure 1: Accuracy and efficiency across model variants.")
    page.insert_text((70, 505), "Body text remains outside the figure region.", fontsize=10)
    _save(document, path)
    return BenchmarkDocument(name, path, (ExpectedFigure("1", 0, tuple(visual)),))


def _single_raster(root: Path) -> BenchmarkDocument:
    name = "raster_caption_below"
    path = root / f"{name}.pdf"
    png = root / "raster_source.png"
    image = Image.new("RGB", (640, 320), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((15, 15, 625, 305), outline="navy", width=5)
    draw.line((45, 270, 200, 120, 360, 180, 590, 45), fill="red", width=7)
    image.save(png)
    document, page = _new_document(path)
    visual = fitz.Rect(70, 120, 525, 360)
    page.insert_image(visual, filename=str(png))
    _caption(page, fitz.Rect(70, 375, 525, 430), "Figure 1: Raster chart with a caption below.")
    _save(document, path)
    return BenchmarkDocument(name, path, (ExpectedFigure("1", 0, tuple(visual)),))


def _two_figures(root: Path) -> BenchmarkDocument:
    name = "two_figures_one_page"
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    first = fitz.Rect(75, 85, 520, 280)
    second = fitz.Rect(75, 445, 520, 650)
    _draw_vector(page, first)
    _caption(page, fitz.Rect(75, 292, 520, 340), "Figure 1: Upper architecture diagram.")
    _draw_vector(page, second)
    _caption(page, fitz.Rect(75, 665, 520, 715), "Figure 2: Lower performance chart.")
    _save(document, path)
    return BenchmarkDocument(
        name,
        path,
        (ExpectedFigure("1", 0, tuple(first)), ExpectedFigure("2", 0, tuple(second))),
    )


def _logo_interference(root: Path) -> BenchmarkDocument:
    name = "logo_interference"
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    page.draw_circle((110, 95), 28, color=(0.2, 0.6, 0.2), fill=(0.8, 1, 0.8))
    page.insert_text((78, 135), "LAB LOGO", fontsize=8)
    visual = fitz.Rect(85, 235, 510, 470)
    _draw_vector(page, visual)
    _caption(page, fitz.Rect(85, 485, 510, 535), "Figure 1: Main experiment, not the logo.")
    _save(document, path)
    return BenchmarkDocument(name, path, (ExpectedFigure("1", 0, tuple(visual)),))


def _fragmented_plot(root: Path) -> BenchmarkDocument:
    name = "fragmented_vector_plot"
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    visual = fitz.Rect(90, 170, 505, 430)
    _draw_vector(page, visual, border=False)
    _caption(
        page,
        fitz.Rect(90, 445, 505, 500),
        "Figure 1: Fragmented paths without an enclosing border.",
    )
    _save(document, path)
    # No enclosing rectangle is drawn: ground truth is the union of the axes,
    # polyline, and title glyph bounds rather than the unused conceptual canvas.
    drawn_content = (119.5, 185.0, 485.5, 396.0)
    return BenchmarkDocument(name, path, (ExpectedFigure("1", 0, drawn_content),))


def _two_column(root: Path) -> BenchmarkDocument:
    name = "two_column_layout"
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    visual = fitz.Rect(55, 160, 285, 390)
    _draw_vector(page, visual)
    _caption(page, fitz.Rect(55, 405, 285, 470), "Figure 1: Left-column result.")
    page.insert_textbox(
        fitz.Rect(320, 150, 545, 520),
        "Right-column body text. " * 25,
        fontsize=9,
    )
    _save(document, path)
    return BenchmarkDocument(name, path, (ExpectedFigure("1", 0, tuple(visual)),))


def _landscape(root: Path) -> BenchmarkDocument:
    name = "landscape_page"
    path = root / f"{name}.pdf"
    document, page = _new_document(path, width=842, height=595)
    visual = fitz.Rect(120, 100, 720, 380)
    _draw_vector(page, visual)
    _caption(page, fitz.Rect(120, 395, 720, 445), "Figure 1: Landscape architecture overview.")
    _save(document, path)
    return BenchmarkDocument(name, path, (ExpectedFigure("1", 0, tuple(visual)),))


def _negative_mention(root: Path) -> BenchmarkDocument:
    name = "mention_only_negative"
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    page.insert_textbox(
        fitz.Rect(70, 120, 525, 300),
        "Figure 1 shows why a body-text mention must never become a detected caption. " * 4,
        fontsize=11,
    )
    _save(document, path)
    return BenchmarkDocument(name, path, ())


def _table_only(root: Path) -> BenchmarkDocument:
    name = "table_only_negative"
    path = root / f"{name}.pdf"
    document, page = _new_document(path)
    table = fitz.Rect(90, 160, 505, 380)
    page.draw_rect(table, width=1)
    for y in (215, 270, 325):
        page.draw_line((table.x0, y), (table.x1, y), width=1)
    for x in (225, 365):
        page.draw_line((x, table.y0), (x, table.y1), width=1)
    _caption(page, fitz.Rect(90, 395, 505, 440), "Table 1: This grid is not a figure.")
    _save(document, path)
    return BenchmarkDocument(name, path, ())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Exact execution directory. By default a unique execution is allocated below "
            "outputs/studies/pdf-figure-fast-path-v1/executions/."
        ),
    )
    args = parser.parse_args()
    execution_root = (
        args.output.resolve()
        if args.output is not None
        else allocate_execution_dir(
            default_study_dir("pdf-figure-fast-path-v1"),
            model="local-geometry-detector",
            condition="offline-fast-path",
        )
    )
    StudyExecutionLayout.from_root(execution_root).prepare()
    result = run_benchmark(execution_root)
    output = execution_root / "results.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"Full results: {output.resolve()}")


if __name__ == "__main__":
    main()

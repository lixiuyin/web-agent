"""Detect and render numbered figures directly from a PDF page.

This is a conservative fast path for text-native PDFs whose figures are stored
as vector drawings or raster image objects. It only returns candidates with an
explicit caption and nearby graphical content; ambiguous layouts fall back to
the structured cloud parser.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import fitz  # type: ignore[import-untyped]

FloatBBox = tuple[float, float, float, float]

_CAPTION_RE = re.compile(
    r"^\s*(?:figure|fig\.)\s*(\d+[a-z]?)\s*[:.\-–—]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LocalFigureRegion:
    """A high-confidence numbered figure region on one PDF page."""

    figure_number: str
    caption: str
    page_idx: int
    bbox: FloatBBox
    caption_bbox: FloatBBox
    confidence: float
    caption_position: Literal["above", "below"]
    visual_kind: Literal["vector", "raster", "mixed"]


@dataclass(frozen=True)
class RenderedLocalFigure:
    """A detected figure plus its rendered image path."""

    region: LocalFigureRegion
    image_path: Path
    width: int
    height: int


@dataclass
class _VisualCluster:
    rect: fitz.Rect
    has_vector: bool = False
    has_raster: bool = False


def detect_local_figure_regions(
    pdf_path: str | Path,
    *,
    figure_number: str | None = None,
) -> list[LocalFigureRegion]:
    """Return conservative caption-grounded figure regions in document order."""
    requested = figure_number.lower() if figure_number else None
    regions: list[LocalFigureRegion] = []
    with fitz.open(str(pdf_path)) as document:
        for page_idx, page in enumerate(document):
            captions = _caption_blocks(page)
            if requested is not None:
                captions = [item for item in captions if item[0].lower() == requested]
            if not captions:
                continue
            clusters = _visual_clusters(page)
            for number, caption, caption_rect in captions:
                region = _match_caption_to_visual(
                    page,
                    page_idx,
                    number,
                    caption,
                    caption_rect,
                    clusters,
                )
                if region is not None:
                    regions.append(region)
    return sorted(regions, key=lambda item: (item.page_idx, item.caption_bbox[1]))


def render_local_figure(
    pdf_path: str | Path,
    region: LocalFigureRegion,
    output_dir: str | Path,
    *,
    dpi: int = 144,
) -> RenderedLocalFigure:
    """Render one detected figure region to a stable PNG file."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    safe_number = re.sub(r"[^0-9a-z]+", "_", region.figure_number.lower())
    image_path = destination / f"local_figure_{safe_number}_p{region.page_idx + 1}.png"
    with fitz.open(str(pdf_path)) as document:
        page = document[region.page_idx]
        scale = dpi / 72.0
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=fitz.Rect(region.bbox),
            alpha=False,
        )
        pixmap.save(str(image_path))
        return RenderedLocalFigure(
            region=region,
            image_path=image_path,
            width=pixmap.width,
            height=pixmap.height,
        )


def detect_and_render_local_figure(
    pdf_path: str | Path,
    figure_number: str,
    output_dir: str | Path,
    *,
    dpi: int = 144,
    min_confidence: float = 0.9,
) -> RenderedLocalFigure | None:
    """Render an exact numbered figure when detection is unambiguous."""
    matches = [
        region
        for region in detect_local_figure_regions(pdf_path, figure_number=figure_number)
        if region.confidence >= min_confidence
    ]
    if len(matches) != 1:
        return None
    return render_local_figure(pdf_path, matches[0], output_dir, dpi=dpi)


def _caption_blocks(page: fitz.Page) -> list[tuple[str, str, fitz.Rect]]:
    captions: list[tuple[str, str, fitz.Rect]] = []
    for block in page.get_text("blocks", sort=True):
        text = " ".join(str(block[4]).split())
        match = _CAPTION_RE.match(text)
        if match:
            captions.append((match.group(1), text, fitz.Rect(block[:4])))
    return captions


def _visual_clusters(page: fitz.Page) -> list[_VisualCluster]:
    clusters: list[_VisualCluster] = []
    page_rect = page.rect
    for drawing in page.get_drawings():
        rect = _valid_visual_rect(drawing.get("rect"), page_rect)
        if rect is not None:
            clusters.append(_VisualCluster(rect=rect, has_vector=True))
    for image in page.get_image_info(xrefs=True):
        rect = _valid_visual_rect(image.get("bbox"), page_rect)
        if rect is not None:
            clusters.append(_VisualCluster(rect=rect, has_raster=True))
    return _merge_clusters(clusters)


def _valid_visual_rect(value: Any, page_rect: fitz.Rect) -> fitz.Rect | None:
    try:
        rect = fitz.Rect(value) & page_rect
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (rect.x0, rect.y0, rect.x1, rect.y1)):
        return None
    # Ignore printer marks and tiny glyph decorations. Long zero-width/height
    # lines remain useful because the cluster merge can connect plot axes.
    if rect.width < 1 and rect.height < 1:
        return None
    if max(rect.width, rect.height) < 6:
        return None
    if (
        rect.height < 1
        and rect.width >= page_rect.width * 0.7
        and (rect.y0 <= page_rect.height * 0.08 or rect.y0 >= page_rect.height * 0.92)
    ):
        # Common running-header/footer rule, not figure geometry.
        return None
    # PyMuPDF represents stroked horizontal/vertical lines as zero-area Rects;
    # inflate them slightly so graph clustering treats axes and connecting edges
    # as real geometry rather than empty rectangles.
    if rect.width < 1:
        rect.x0 = max(page_rect.x0, rect.x0 - 0.5)
        rect.x1 = min(page_rect.x1, rect.x1 + 0.5)
    if rect.height < 1:
        rect.y0 = max(page_rect.y0, rect.y0 - 0.5)
        rect.y1 = min(page_rect.y1, rect.y1 + 0.5)
    return rect


def _merge_clusters(items: list[_VisualCluster], gap: float = 12.0) -> list[_VisualCluster]:
    clusters = list(items)
    changed = True
    while changed:
        changed = False
        merged: list[_VisualCluster] = []
        while clusters:
            current = clusters.pop()
            index = 0
            while index < len(clusters):
                other = clusters[index]
                if _rects_near(current.rect, other.rect, gap):
                    current.rect |= other.rect
                    current.has_vector = current.has_vector or other.has_vector
                    current.has_raster = current.has_raster or other.has_raster
                    clusters.pop(index)
                    changed = True
                else:
                    index += 1
            merged.append(current)
        clusters = merged
    return clusters


def _rects_near(left: fitz.Rect, right: fitz.Rect, gap: float) -> bool:
    expanded = fitz.Rect(left.x0 - gap, left.y0 - gap, left.x1 + gap, left.y1 + gap)
    return bool(expanded.intersects(right))


def _match_caption_to_visual(
    page: fitz.Page,
    page_idx: int,
    number: str,
    caption: str,
    caption_rect: fitz.Rect,
    clusters: list[_VisualCluster],
) -> LocalFigureRegion | None:
    page_area = max(1.0, page.rect.width * page.rect.height)
    maximum_gap = max(72.0, page.rect.height * 0.14)
    candidates: list[tuple[float, float, _VisualCluster, Literal["above", "below"]]] = []
    for cluster in clusters:
        area_ratio = max(0.0, cluster.rect.width * cluster.rect.height) / page_area
        if area_ratio < 0.004:
            continue
        relation = _caption_relation(cluster.rect, caption_rect)
        if relation is None:
            continue
        position, vertical_gap = relation
        if vertical_gap > maximum_gap:
            continue
        overlap = _horizontal_overlap_ratio(cluster.rect, caption_rect)
        if overlap < 0.15 and cluster.rect.width < page.rect.width * 0.4:
            continue
        score = _candidate_confidence(
            area_ratio=area_ratio,
            vertical_gap=vertical_gap,
            maximum_gap=maximum_gap,
            horizontal_overlap=overlap,
            caption_position=position,
        )
        candidates.append((score, vertical_gap, cluster, position))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], -_area(item[2].rect)))
    confidence, _gap, cluster, position = candidates[0]
    bbox = _expand_with_labels(page, cluster.rect, caption_rect)
    if position == "below":
        bbox.y1 = min(bbox.y1, caption_rect.y0 - 2.0)
    else:
        bbox.y0 = max(bbox.y0, caption_rect.y1 + 2.0)
    return LocalFigureRegion(
        figure_number=number,
        caption=caption,
        page_idx=page_idx,
        bbox=(float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1)),
        caption_bbox=(
            float(caption_rect.x0),
            float(caption_rect.y0),
            float(caption_rect.x1),
            float(caption_rect.y1),
        ),
        confidence=confidence,
        caption_position=position,
        visual_kind=_visual_kind(cluster),
    )


def _caption_relation(
    visual_rect: fitz.Rect,
    caption_rect: fitz.Rect,
) -> tuple[Literal["above", "below"], float] | None:
    if visual_rect.y1 <= caption_rect.y0 + 3:
        return "below", max(0.0, caption_rect.y0 - visual_rect.y1)
    if visual_rect.y0 >= caption_rect.y1 - 3:
        return "above", max(0.0, visual_rect.y0 - caption_rect.y1)
    return None


def _visual_kind(cluster: _VisualCluster) -> Literal["vector", "raster", "mixed"]:
    if cluster.has_vector and cluster.has_raster:
        return "mixed"
    if cluster.has_raster:
        return "raster"
    return "vector"


def _candidate_confidence(
    *,
    area_ratio: float,
    vertical_gap: float,
    maximum_gap: float,
    horizontal_overlap: float,
    caption_position: str,
) -> float:
    proximity = max(0.0, 1.0 - vertical_gap / maximum_gap)
    area_score = min(1.0, area_ratio / 0.04)
    position_score = 1.0 if caption_position == "below" else 0.9
    score = 0.35 + 0.25 * proximity + 0.15 * horizontal_overlap
    score += 0.15 * area_score + 0.10 * position_score
    return round(min(1.0, score), 4)


def _horizontal_overlap_ratio(left: fitz.Rect, right: fitz.Rect) -> float:
    overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    denominator = max(1.0, min(left.width, right.width))
    return float(min(1.0, overlap / denominator))


def _expand_with_labels(
    page: fitz.Page,
    visual_rect: fitz.Rect,
    caption_rect: fitz.Rect,
) -> fitz.Rect:
    expanded = fitz.Rect(visual_rect)
    label_band = fitz.Rect(
        max(0.0, visual_rect.x0 - 24),
        max(0.0, visual_rect.y0 - 14),
        min(page.rect.x1, visual_rect.x1 + 24),
        min(page.rect.y1, visual_rect.y1 + 14),
    )
    for block in page.get_text("blocks", sort=True):
        block_rect = fitz.Rect(block[:4])
        if block_rect.intersects(caption_rect):
            continue
        center = fitz.Point(
            (block_rect.x0 + block_rect.x1) / 2,
            (block_rect.y0 + block_rect.y1) / 2,
        )
        if label_band.contains(center):
            expanded |= block_rect
    margin = 6.0
    return fitz.Rect(
        max(0.0, expanded.x0 - margin),
        max(0.0, expanded.y0 - margin),
        min(page.rect.x1, expanded.x1 + margin),
        min(page.rect.y1, expanded.y1 + margin),
    )


def _area(rect: fitz.Rect) -> float:
    return float(max(0.0, rect.width) * max(0.0, rect.height))


__all__ = [
    "FloatBBox",
    "LocalFigureRegion",
    "RenderedLocalFigure",
    "detect_and_render_local_figure",
    "detect_local_figure_regions",
    "render_local_figure",
]

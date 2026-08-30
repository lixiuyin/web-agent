"""PDF text and image extraction (migrated from pdf_utils.py)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]


def extract_text(pdf_path: str) -> str:
    """Read full PDF and return concatenated text."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    with fitz.open(path) as doc:
        return "\n".join(page.get_text("text") for page in doc).strip()


def _normalise_caption(text: str) -> str:
    return " ".join(text.split())


def _keep_longest(captions: dict[str, str], figure_num: str, caption_text: str) -> None:
    """Record a caption, keeping the longest variant seen for that figure."""
    if not caption_text:
        return
    figure_key = f"figure {figure_num}"
    if figure_key not in captions or len(caption_text) > len(captions[figure_key]):
        captions[figure_key] = caption_text


def _captions_from_lines(lines: list[str]) -> dict[str, str]:
    """Extract captions line-by-line (handles captions wrapped across lines)."""
    captions: dict[str, str] = {}
    start_re = re.compile(r"^(?:Figure|Fig\.?)\s+(\d+)\s*[:.\-]?\s*(.*)$", re.IGNORECASE)
    stop_re = re.compile(r"^(?:Figure|Fig\.?|Table)\s+\d+\s*[:.\-]", re.IGNORECASE)

    for idx, line in enumerate(lines):
        match = start_re.match(line)
        if not match:
            continue
        parts = [match.group(2).strip()] if match.group(2).strip() else []

        for next_line in lines[idx + 1 : idx + 8]:
            if stop_re.match(next_line):
                break
            parts.append(next_line)
            caption_so_far = _normalise_caption(" ".join(parts))
            if len(caption_so_far) >= 120 and caption_so_far.endswith((".", "!", "?")):
                break

        _keep_longest(captions, match.group(1), _normalise_caption(" ".join(parts)))

    return captions


def _captions_from_regex(full_text: str, captions: dict[str, str]) -> None:
    """Fallback extraction for PDFs whose captions are not line-start aligned.

    Merges into *captions*, keeping the longest caption per figure.
    """
    pattern = r"(?:Figure|Fig\.?)\s+(\d+)\s*[:.\-]\s*([^\n]{10,500})"
    for match in re.finditer(pattern, full_text, re.IGNORECASE):
        _keep_longest(captions, match.group(1), _normalise_caption(match.group(2)))


def extract_figure_captions(pdf_path: str) -> dict[str, str]:
    """Extract figure captions from PDF text using pattern matching.

    Academic papers typically use patterns like:
    - "Figure 1: Description here"
    - "Figure 1. Description here"
    - "Fig. 1. Description here"

    Returns a dict mapping figure identifiers to their captions.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with fitz.open(path) as doc:
        full_text = "\n".join(page.get_text("text") for page in doc)

    # PyMuPDF often wraps captions across physical lines, so line-oriented
    # extraction is more reliable than a single "[^\n]*" regex.
    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    captions = _captions_from_lines(lines)
    _captions_from_regex(full_text, captions)
    return captions


def _caption_near_rect(page: Any, img_rect: Any) -> str:
    """Read text just above (or below) an image rect as its caption."""
    # Search above the image first — captions usually sit under the figure
    # title but above the next block.
    search_rect = fitz.Rect(
        img_rect.x0 - 50,  # expand left
        max(0, img_rect.y0 - 100),  # look above for caption
        img_rect.x1 + 50,  # expand right
        img_rect.y0 - 5,  # just above the image
    )
    caption = page.get_text("text", clip=search_rect).strip()

    # If no caption above, try below
    if not caption:
        search_rect = fitz.Rect(
            img_rect.x0 - 50,
            img_rect.y1 + 5,
            img_rect.x1 + 50,
            min(page.rect.y1, img_rect.y1 + 100),
        )
        caption = page.get_text("text", clip=search_rect).strip()

    return _clean_caption(caption)


def _clean_caption(caption: str) -> str:
    """Keep caption-like lines; otherwise truncate to the last 200 chars."""
    if not caption:
        return caption
    lines = caption.split("\n")
    caption_lines = [line for line in lines if "figure" in line.lower() or "fig" in line.lower()]
    if caption_lines:
        return " | ".join(caption_lines)
    return caption[-200:] if len(caption) > 200 else caption


def _caption_for_image(page: Any, xref: int) -> str:
    """Extract nearby caption text for an embedded image, if any."""
    try:
        # fitz returns a list of (xref, rect) tuples; find the rect for our xref
        img_info = page.get_image_rects(xref)
        img_rect = img_info[0] if img_info else None
        if img_rect:
            return _caption_near_rect(page, img_rect)
    except Exception:
        pass
    return ""


def extract_images(pdf_path: str, output_dir: str | Path) -> list[dict[str, Any]]:
    """Extract embedded images and save to *output_dir*.

    Returns list of dicts with image info including nearby text (captions).
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # First, extract all figure captions from the document
    figure_captions = extract_figure_captions(pdf_path)

    results: list[dict[str, Any]] = []
    with fitz.open(path) as doc:
        for page_idx in range(len(doc)):
            page = doc[page_idx]

            for img_idx, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = doc.extract_image(xref)
                ext = base_image.get("ext", "png")
                out_path = output_dir / f"page{page_idx + 1}_img{img_idx + 1}.{ext}"
                out_path.write_bytes(base_image["image"])

                caption = _caption_for_image(page, xref)

                # Mark likely figures (large images on first few pages)
                width = base_image.get("width", 0)
                height = base_image.get("height", 0)
                is_likely_figure = page_idx + 1 <= 3 and width * height > 100000

                results.append(
                    {
                        "path": str(out_path),
                        "page": page_idx + 1,
                        "width": width,
                        "height": height,
                        "ext": ext,
                        "caption": caption,
                        "likely_figure": is_likely_figure,
                    }
                )

    # Enhance results with figure captions from the document
    # Map images to figure numbers based on order and content
    figure_idx = 1
    for result in results:
        if result["likely_figure"]:
            figure_key = f"figure {figure_idx}"
            if figure_key in figure_captions:
                result["figure_caption"] = figure_captions[figure_key]
                result["figure_number"] = figure_idx
                figure_idx += 1
            else:
                result["figure_caption"] = ""
                result["figure_number"] = None
        else:
            result["figure_caption"] = ""
            result["figure_number"] = None

    return results

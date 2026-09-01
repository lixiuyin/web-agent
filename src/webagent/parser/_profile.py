"""Document profiling — fast local pre-analysis to drive parser routing.

Uses only PyMuPDF (fitz) — no cloud calls, no heavy ML deps.  Always returns a
profile; never raises (falls back to a generic "unknown" profile on error so
the router still picks something).
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".gif"})

# chars/page below which a PDF is treated as scanned / handwritten
SCANNED_TEXT_THRESHOLD = 50


@dataclass(frozen=True)
class DocumentProfile:
    """Immutable summary of a document's characteristics for routing."""

    suffix: str
    page_count: int
    avg_chars_per_page: float
    image_ratio: float  # 0.0–1.0, fraction of pages with significant images
    has_text_layer: bool
    is_likely_scanned: bool
    size_bytes: int


def profile_document(file_path: Path) -> DocumentProfile:
    """Analyse a document and return a profile for router selection.

    Never raises — returns a conservative "unknown" profile on any error.
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()
    size_bytes = 0
    with contextlib.suppress(OSError):
        size_bytes = os.path.getsize(file_path)

    if suffix in IMAGE_EXTS:
        return DocumentProfile(
            suffix=suffix,
            page_count=1,
            avg_chars_per_page=0.0,
            image_ratio=1.0,
            has_text_layer=False,
            is_likely_scanned=False,
            size_bytes=size_bytes,
        )

    if suffix == ".pdf":
        return _profile_pdf(file_path, size_bytes)

    # Other document formats — treat as text-present, no images.
    return DocumentProfile(
        suffix=suffix,
        page_count=1,
        avg_chars_per_page=0.0,
        image_ratio=0.0,
        has_text_layer=True,
        is_likely_scanned=False,
        size_bytes=size_bytes,
    )


def _profile_pdf(file_path: Path, size_bytes: int) -> DocumentProfile:
    """Profile a PDF using PyMuPDF (fitz)."""
    try:
        import fitz  # type: ignore[import-untyped]

        with fitz.open(str(file_path)) as doc:
            page_count = len(doc)
            if page_count == 0:
                return _unknown_profile(".pdf", size_bytes)

            total_chars = 0
            image_pages = 0
            for page in doc:
                raw_text = page.get_text("text")
                text = raw_text if isinstance(raw_text, str) else ""
                total_chars += len(text.strip())
                if page.get_images(full=True):
                    image_pages += 1

            avg_chars = total_chars / page_count
            return DocumentProfile(
                suffix=".pdf",
                page_count=page_count,
                avg_chars_per_page=avg_chars,
                image_ratio=image_pages / page_count,
                has_text_layer=avg_chars > 0,
                is_likely_scanned=avg_chars < SCANNED_TEXT_THRESHOLD,
                size_bytes=size_bytes,
            )
    except Exception:
        logger.debug("PDF profiling failed for %s", file_path.name, exc_info=True)
        return _unknown_profile(".pdf", size_bytes)


def _unknown_profile(suffix: str, size_bytes: int) -> DocumentProfile:
    """Fallback profile when analysis fails — triggers cloud parsing."""
    return DocumentProfile(
        suffix=suffix,
        page_count=0,
        avg_chars_per_page=0.0,
        image_ratio=0.0,
        has_text_layer=False,
        is_likely_scanned=False,
        size_bytes=size_bytes,
    )

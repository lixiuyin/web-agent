"""Parser routing — selects providers based on document profile.

Pure function, trivially unit-testable.  Returns an ordered tuple of parser
names (primary first) so the cascade can try them sequentially.

The local PyMuPDF parser is excluded from normal routing — it serves only as an
emergency fallback when every cloud API is unavailable (see ``cascade``).
"""

from __future__ import annotations

from typing import Literal

from ._profile import IMAGE_EXTS, SCANNED_TEXT_THRESHOLD, DocumentProfile

ParserName = Literal["marker", "mineru", "paddle"]


def select_parsers(profile: DocumentProfile, user_hint: str = "") -> tuple[ParserName, ...]:
    """Return an ordered list of parser names (primary → fallback).

    ``user_hint`` is the configured ``ocr_provider`` value — treated as a soft
    preference, not a hard override.  If the hint names a provider that could
    handle this document, it is promoted to primary.
    """
    suffix = profile.suffix

    # Single image file → Paddle first (layout-parsing specialist).
    # MinerU v4 extract requires PDF input, so it is dropped for standalone images.
    if suffix in IMAGE_EXTS:
        return _apply_hint(("paddle", "marker"), user_hint)

    if suffix == ".pdf":
        if profile.avg_chars_per_page < SCANNED_TEXT_THRESHOLD:
            # Scanned / handwritten — MinerU OCR is strongest.
            return _apply_hint(("mineru", "marker", "paddle"), user_hint)
        # Normal text / mixed layout — Marker handles structure best.
        return _apply_hint(("marker", "mineru", "paddle"), user_hint)

    # Other document formats.
    return _apply_hint(("marker", "mineru", "paddle"), user_hint)


def _apply_hint(order: tuple[ParserName, ...], hint: str) -> tuple[ParserName, ...]:
    """Promote the hinted provider to the front if it appears in the list."""
    hint = (hint or "").strip().lower()
    if not hint or hint not in order:
        return order
    remaining = tuple(p for p in order if p != hint)
    return (hint, *remaining)

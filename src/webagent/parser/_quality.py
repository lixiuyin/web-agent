"""Parser output quality assessment — heuristic checks for cascade fallback.

Runs after each provider returns a ``PDFParseResult``.  If the output fails
quality thresholds, the cascade logs a warning and tries the next provider.
All checks are pure heuristics (no LLM calls).
"""

from __future__ import annotations

from dataclasses import dataclass

from ._profile import DocumentProfile
from .models import PDFParseResult

# Thresholds
MIN_TEXT_RATIO = 0.05  # min text volume as a fraction of expected
MIN_SCANNED_CHARS_PER_PAGE = 80
MAX_CONTROL_CHAR_RATIO = 0.03


@dataclass(frozen=True)
class QualityResult:
    """Quality assessment outcome."""

    is_satisfactory: bool
    score: float  # 0.0–1.0
    reasons: tuple[str, ...]


def result_text(result: PDFParseResult) -> str:
    """Concatenate all extracted text from a parse result."""
    return "\n".join(b.text for b in result.text_blocks if b.text)


def assess_quality(result: PDFParseResult, profile: DocumentProfile) -> QualityResult:
    """Check whether a parsed document meets minimum quality thresholds.

    Designed to catch extraction *failures*, not to grade content.  Scanned /
    image-heavy documents are exempt from volume-based checks.
    """
    if result.error:
        return _fail(f"provider_error:{result.error}", 0.0)

    stripped = result_text(result).strip()

    # A result that produced structured assets (tables/images) but little text
    # is still useful for image/scanned PDFs.
    has_assets = bool(result.tables or result.images)

    if not stripped and not profile.is_likely_scanned and not has_assets:
        return _fail("empty_text", 0.0)

    if profile.is_likely_scanned:
        chars_per_page = len(stripped) / max(1, profile.page_count)
        if chars_per_page < MIN_SCANNED_CHARS_PER_PAGE and not has_assets:
            return _fail(f"scanned_text_too_short({chars_per_page:.1f}/pg)", 0.2)
        return QualityResult(is_satisfactory=True, score=0.8, reasons=())

    reasons: list[str] = []
    score = 0.9

    expected = profile.avg_chars_per_page * profile.page_count
    if expected > 100 and len(stripped) < expected * MIN_TEXT_RATIO:
        reasons.append(f"text_too_short(ratio={len(stripped) / expected:.2f})")
        score -= 0.3

    ctrl = sum(1 for c in stripped if ord(c) < 0x20 and c not in "\n\r\t")
    if len(stripped) > 50 and ctrl / len(stripped) > MAX_CONTROL_CHAR_RATIO:
        reasons.append(f"high_control_chars(ratio={ctrl / len(stripped):.3f})")
        score -= 0.2

    score = max(0.0, score)
    if reasons:
        return QualityResult(is_satisfactory=False, score=score, reasons=tuple(reasons))
    return QualityResult(is_satisfactory=True, score=score, reasons=())


def _fail(reason: str, score: float) -> QualityResult:
    return QualityResult(is_satisfactory=False, score=score, reasons=(reason,))

"""Backward-compatibility shim for the document parser.

The local dots.mocr / Chandra VLM backend has been replaced by the cloud-native
parser cascade in :mod:`webagent.parser` (Marker → MinerU → Paddle, with a local
PyMuPDF fallback).  This module re-exports the public surface the PDF tool
modules historically imported from ``webagent.utils.chandra_pdf`` so they keep
working unchanged.

New code should import from :mod:`webagent.parser` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from webagent.parser import (
    ImageInfo,
    PDFParseResult,
    TableInfo,
    TextBlock,
    find_images_by_keyword,
    find_section_by_title,
    find_tables_by_keyword,
    generate_content_summary,
    get_element_at_position,
    parse_pdf,
)

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig

__all__ = [
    "ImageInfo",
    "PDFParseResult",
    "TableInfo",
    "TextBlock",
    "find_images_by_keyword",
    "find_section_by_title",
    "find_tables_by_keyword",
    "generate_content_summary",
    "get_element_at_position",
    "parse_pdf_with_chandra",
]


def parse_pdf_with_chandra(
    pdf_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    config: AgentConfig | None = None,
    **_legacy: Any,
) -> PDFParseResult:
    """Parse a PDF via the cloud parser cascade.

    Compatibility wrapper preserving the historical name/signature.  Obsolete
    keyword arguments from the local-model era (``method``, ``parse_structured``,
    ``subprocess_timeout``) are accepted and ignored.
    """
    return parse_pdf(pdf_path, output_dir, config=config)
